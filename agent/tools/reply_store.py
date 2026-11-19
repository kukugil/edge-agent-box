"""本机微信回复账本。SQLite 中的状态与消息观察一起提交。"""
import hashlib
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from agent.tools.history import _same


def contact_key(title: str) -> str:
    """以白名单原名作键，避免列表重排改变待处理会话的归属。"""
    return hashlib.sha256(title.encode('utf-8')).hexdigest()


def _pack(rows):
    return json.dumps(rows, ensure_ascii=False, default=datetime.isoformat)


def _unpack(value):
    rows = json.loads(value)
    for row in rows:
        row['at'] = datetime.fromisoformat(row['at']) if row['at'] else None
    return rows


def _snapshot(rows):
    return [{k: row[k] for k in ('who', 'kind', 'text', 'at')}
            for row in rows if row['who'] in ('us', 'them')]


def appended(previous, current):
    """返回可确认新增的气泡。没有可靠锚点或有多个解释时返回 None。"""
    if not previous:
        return current
    if not current:
        return None
    if len(previous) == len(current) and all(_same(a, b) for a, b in zip(previous, current)):
        return []
    matches = []
    for size in range(min(len(previous), len(current)), 0, -1):
        if all(_same(a, b) for a, b in zip(previous[-size:], current[:size])):
            matches.append(size)
    if len(matches) == 1:
        return current[matches[0]:]
    return None


class ReplyStore:
    """一个本地账号的收件观察、待回复批次和可核对结果。"""

    def __init__(self, path, *, mode=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            if mode is not None:
                db.execute("INSERT OR IGNORE INTO settings VALUES('mode',?)", (mode,))
                if db.execute("SELECT value FROM settings WHERE name='mode'").fetchone()[0] != mode:
                    raise ValueError('预览和发送不可共用同一个回复账本')
            db.execute("""CREATE TABLE IF NOT EXISTS session (
                contact TEXT PRIMARY KEY, snapshot TEXT, unread INTEGER,
                read_failures INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS turn (
                id INTEGER PRIMARY KEY, contact TEXT NOT NULL, incoming TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', draft TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
            )""")
            db.execute('CREATE INDEX IF NOT EXISTS turns_by_contact ON turn(contact, state, id)')
            db.execute("""CREATE TABLE IF NOT EXISTS issue (
                turn_id INTEGER NOT NULL, part INTEGER NOT NULL, contact TEXT NOT NULL,
                text TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(turn_id, part)
            )""")

    @contextmanager
    def transaction(self):
        """一个操作独占写事务，异常时由连接回滚。"""
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            yield db

    def _insert(self, db, key, rows, state='queued', reason=''):
        return db.execute(
            'INSERT INTO turn(contact,incoming,state,reason,created) VALUES(?,?,?,?,?)',
            (key, _pack(rows), state, reason, time.time())
        ).lastrowid

    def recover(self):
        """曾提交键盘发送许可的批次可能已发，恢复时隔离到待核对。"""
        with self.transaction() as db:
            db.execute("""UPDATE session SET blocked=1 WHERE contact IN
                          (SELECT contact FROM turn WHERE state='armed')""")
            db.execute("UPDATE turn SET state='review', reason='重启前发送结果不明' WHERE state='armed'")

    def discover(self, title, count):
        """开窗前登记未读。-1 表示有角标但数量不可核验。"""
        with self.transaction() as db:
            db.execute('''INSERT INTO session(contact,unread) VALUES(?,?)
                ON CONFLICT(contact) DO UPDATE SET unread=MAX(COALESCE(session.unread,-1),excluded.unread)
                WHERE session.blocked=0''', (contact_key(title), count))

    def scheduled(self, titles):
        """返回有待读或待回复事项的会话，按白名单顺序供轮询。"""
        with self.transaction() as db:
            return [title for title in titles if db.execute("""
                SELECT 1 FROM session WHERE contact=? AND blocked=0 AND
                (unread IS NOT NULL OR EXISTS(SELECT 1 FROM turn
                 WHERE turn.contact=session.contact AND state='queued'))""",
                (contact_key(title),)).fetchone()]

    def can_reply(self, title):
        """核对当前会话是否允许继续自动处理。"""
        with self.transaction() as db:
            row = db.execute('SELECT blocked FROM session WHERE contact=?', (contact_key(title),)).fetchone()
            return bool(row is not None and not row['blocked'])

    def watch_failed(self, title, reason):
        """打开的聊天区发生变化但读不到时，登记一次重读。"""
        with self.transaction() as db:
            db.execute('''INSERT INTO session(contact,unread) VALUES(?,0)
                ON CONFLICT(contact) DO UPDATE SET unread=COALESCE(session.unread,0)
                WHERE session.blocked=0''', (contact_key(title),))
        self.read_failed(title, reason)

    def read_failed(self, title, reason):
        """两次读不到仍保留待读证据，避免占住其他会话。"""
        key = contact_key(title)
        with self.transaction() as db:
            row = db.execute('SELECT read_failures,blocked FROM session WHERE contact=?', (key,)).fetchone()
            if row is None or row['blocked']:
                return
            db.execute('UPDATE session SET read_failures=read_failures+1 WHERE contact=?', (key,))
            if row['read_failures'] >= 1:
                self._review_boundary(db, key, [], reason)

    def _review_boundary(self, db, key, rows, reason):
        self._insert(db, key, rows, 'review', reason)
        db.execute("UPDATE turn SET state='review',reason=? WHERE contact=? AND state='queued'", (reason, key))
        db.execute('UPDATE session SET blocked=1 WHERE contact=?', (key,))

    def observe(self, title, rows):
        """保存屏幕边界并登记新来信。首次已读画面只建立基线。"""
        key = contact_key(title)
        current = _snapshot(rows)
        with self.transaction() as db:
            session = db.execute('SELECT * FROM session WHERE contact=?', (key,)).fetchone()
            if session is None:
                db.execute('INSERT INTO session(contact,snapshot) VALUES(?,?)', (key, _pack(current)))
                return
            if session['blocked']:
                return
            baseline, unread = session['snapshot'], session['unread']
            if baseline is None:
                added = current[-unread:] if unread and 0 < unread <= len(current) else None
                if added is not None and any(row['who'] != 'them' for row in added):
                    added = None
            else:
                added = appended(_unpack(baseline), current)
            if added is None or (unread and unread > 0 and
                                 sum(row['who'] == 'them' for row in added) < unread):
                self._review_boundary(db, key, current, '来信边界无法核对')
                return
            if unread == -1 and not any(row['who'] == 'them' for row in added):
                self._review_boundary(db, key, current, '未读来信未能定位')
                return
            db.execute('UPDATE session SET snapshot=?,unread=NULL,read_failures=0 WHERE contact=?',
                       (_pack(current), key))
            incoming = [row for row in added if row['who'] == 'them']
            if not incoming:
                return
            previous = db.execute("SELECT * FROM turn WHERE contact=? AND state='queued' ORDER BY id DESC LIMIT 1",
                                  (key,)).fetchone()
            if previous is None:
                self._insert(db, key, incoming)
            else:
                db.execute('UPDATE turn SET incoming=?,draft=NULL,attempts=0 WHERE id=?',
                           (_pack(_unpack(previous['incoming']) + incoming), previous['id']))

    def pending(self, title):
        """返回已经登记、尚未覆盖的最早批次。"""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM turn WHERE contact=? AND state='queued' ORDER BY id LIMIT 1",
                             (contact_key(title),)).fetchone()
            return {**dict(row), 'incoming': _unpack(row['incoming'])} if row else None

    def open_issues(self, title, *, exclude_turn=None):
        """返回这个联系人的未决人工事项，供分流和托盘读取。"""
        with self.transaction() as db:
            return [dict(row) for row in db.execute('''
                SELECT issue.*, turn.created FROM issue JOIN turn ON turn.id=issue.turn_id
                WHERE issue.contact=? AND issue.resolved=0 AND turn_id!=? ORDER BY turn_id,part''',
                (contact_key(title), exclude_turn or 0))]

    def finish(self, turn_id, state, reason, *, draft=None, issues=()):
        """原子记录整批结果和事项。已核对发送的结果由发送器单独提交。"""
        with self.transaction() as db:
            row = db.execute('SELECT contact,state FROM turn WHERE id=?', (turn_id,)).fetchone()
            if row['state'] not in ('queued', 'armed', 'sent'):
                raise ValueError('批次已结案')
            db.execute('UPDATE turn SET state=?,reason=?,draft=? WHERE id=?', (state, reason, draft, turn_id))
            for part, text in enumerate(issues):
                db.execute('INSERT OR IGNORE INTO issue VALUES(?,?,?,?,0)', (turn_id, part, row['contact'], text))

    def retry(self, turn_id, reason):
        """暂时失败最多尝试两次，超限转待核对。"""
        with self.transaction() as db:
            db.execute("""UPDATE turn SET attempts=attempts+1,reason=?,
                state=CASE WHEN attempts>=1 THEN 'review' ELSE 'queued' END
                WHERE id=? AND state='queued'""", (reason, turn_id))

    def audit(self, turn_id, status, text, *, issues=(), reason=''):
        """与发送器共享同一批号，确保回车前后状态可以回放。"""
        states = {'about_to_send': ('queued', 'armed'), 'ok': ('armed', 'sent'),
                  'unknown': ('armed', 'review'), 'fail': ('armed', 'queued')}
        previous, state = states[status]
        with self.transaction() as db:
            changed = db.execute('UPDATE turn SET state=?,reason=?,draft=? WHERE id=? AND state=?',
                                 (state, reason if status == 'ok' and reason else status, text, turn_id, previous)).rowcount
            if changed != 1:
                raise ValueError('批次发送状态已变化')
            key = db.execute('SELECT contact FROM turn WHERE id=?', (turn_id,)).fetchone()['contact']
            if status == 'about_to_send':
                for part, issue in enumerate(issues):
                    db.execute('INSERT OR IGNORE INTO issue VALUES(?,?,?,?,0)', (turn_id, part, key, issue))
            elif status == 'unknown':
                db.execute('UPDATE session SET blocked=1 WHERE contact=?', (key,))

    def preview_items(self, title):
        """返回预览草稿和原来信，仅供本机验收查看。"""
        with self.transaction() as db:
            return [{**dict(row), 'incoming': _unpack(row['incoming'])} for row in db.execute(
                "SELECT * FROM turn WHERE contact=? AND state='preview' ORDER BY id",
                (contact_key(title),))]

    def review_items(self, title):
        """读出需要人工判断的批次，不修改对应会话状态。"""
        with self.transaction() as db:
            return [{**dict(row), 'incoming': _unpack(row['incoming'])} for row in db.execute(
                "SELECT * FROM turn WHERE contact=? AND state='review' ORDER BY id",
                (contact_key(title),))]

    def resolve(self, title, turn_id, part=None):
        """人工确认结束一个事项或待核对批次，禁止自动重发该批。"""
        key = contact_key(title)
        with self.transaction() as db:
            if part is not None:
                return db.execute('UPDATE issue SET resolved=1 WHERE contact=? AND turn_id=? AND part=? AND resolved=0',
                                  (key, turn_id, part)).rowcount == 1
            changed = db.execute("UPDATE turn SET state='resolved' WHERE contact=? AND id=? AND state='review'",
                                 (key, turn_id)).rowcount
            if changed and not db.execute("SELECT 1 FROM turn WHERE contact=? AND state='review'",
                                          (key,)).fetchone():
                # 人工已在微信中核对，下一块可读画面重新建立基线。
                db.execute('DELETE FROM session WHERE contact=? AND blocked=1', (key,))
            return changed == 1

