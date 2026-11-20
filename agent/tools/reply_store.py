"""本机微信回复账本。SQLite 中的状态与消息观察一起提交。"""
import hashlib
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
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


MIN_OVERLAP = 3


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
    # 控件树只给屏上十来条、没有消息编号，只能按内容对齐。固定回执等重复短句会让首尾凑巧再对上 1–2 条；
    # 最长重叠至少 3 条且不短于次长的两倍才采信（11/18 真机：10 条对 1 条），否则仍交人工核对
    if len(matches) > 1 and matches[0] >= MIN_OVERLAP and matches[0] >= 2 * matches[1]:
        return current[matches[0]:]
    return None


def _tail_after_us(rows):
    """我方最后一条之后对方的气泡。我方包括程序发的和本人发的：本人回过，就以本人那句为界。"""
    last = max((i for i, row in enumerate(rows) if row['who'] == 'us'), default=-1)
    return [row for row in rows[last + 1:] if row['who'] == 'them']


def after_anchor(previous, current, unread, since):
    """对不上屏幕时的兜底：以我方最后一条为界取待回复（网页客服、RPA 自动回复的常见做法）。

    输入：previous 上次屏幕（没有则 None）、current 这次屏幕、unread 角标数（None/-1 表示不知道）、
         since 监听开始时刻（None 表示不按时间筛）
    输出：要登记的对方气泡
    坑：气泡时间来自时间分隔条，只精确到分钟，所以给 1 分钟余量；没有分隔条的气泡时间是 None。
    """
    fresh = _tail_after_us(current)
    taken = _tail_after_us(previous) if previous else []
    # 上次已经登记过、仍挂在界后开头的那几条不再登记
    if taken and len(taken) <= len(fresh) and all(_same(a, b) for a, b in zip(taken, fresh)):
        fresh = fresh[len(taken):]
    if since is not None:
        fresh = [row for row in fresh if row['at'] is None or row['at'] >= since - timedelta(minutes=1)]
    if previous is None:
        if unread and unread > 0:
            fresh = fresh[-unread:]   # 第一次见：角标数是唯一确切的数量
        elif any(row['at'] is None for row in fresh):
            fresh = fresh[-1:]        # 不知道数量也不知道时间：只认最后一条，宁可少回旧话
    return fresh


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
        """读不到只记次数、不锁会话：会话仍留在待读队列，由监听按退避重试。
        11/19 好友测试：切窗、最小化时连读两次失败就锁死，要人手动解开，比漏回一条还难恢复。"""
        with self.transaction() as db:
            db.execute('UPDATE session SET read_failures=read_failures+1 WHERE contact=? AND blocked=0',
                       (contact_key(title),))

    def observe(self, title, rows, *, since=None):
        """保存屏幕边界并登记新来信。首次已读画面只建立基线。

        先按上次屏幕对齐；对不上（或第一次见、或新增条数少于角标数）时，以我方最后一条为界：
        界后对方说的都算待回复，去掉上次已登记过的那几条。不再因对不上而锁会话。
        since：监听开始时刻，界后但时间更早的气泡（启动前就在的旧话）不算。
        返回 'aligned'、'anchor'（按我方最后一条重新划界）或 None（只建基线 / 会话已锁）。"""
        key = contact_key(title)
        current = _snapshot(rows)
        with self.transaction() as db:
            session = db.execute('SELECT * FROM session WHERE contact=?', (key,)).fetchone()
            if session is None:
                db.execute('INSERT INTO session(contact,snapshot) VALUES(?,?)', (key, _pack(current)))
                return None
            if session['blocked']:
                return None
            baseline, unread = session['snapshot'], session['unread']
            previous_rows = _unpack(baseline) if baseline is not None else None
            added = appended(previous_rows, current) if previous_rows is not None else None
            aligned = None if added is None else [row for row in added if row['who'] == 'them']
            how = 'aligned'
            if aligned is None or (unread and unread > 0 and len(aligned) < unread):
                fresh = after_anchor(previous_rows, current, unread, since)
                if aligned is None or len(fresh) > len(aligned):
                    aligned, how = fresh, 'anchor'
            db.execute('UPDATE session SET snapshot=?,unread=NULL,read_failures=0 WHERE contact=?',
                       (_pack(current), key))
            if not aligned:
                return how
            previous = db.execute("SELECT * FROM turn WHERE contact=? AND state='queued' ORDER BY id DESC LIMIT 1",
                                  (key,)).fetchone()
            if previous is None:
                self._insert(db, key, aligned)
            else:
                db.execute('UPDATE turn SET incoming=?,draft=NULL,attempts=0 WHERE id=?',
                           (_pack(_unpack(previous['incoming']) + aligned), previous['id']))
            return how

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

