"""微信白名单私聊的可恢复回复入口。预览与实际发送使用不同账本。"""
import msvcrt
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from agent.core import graph, messages, reply_turn, send, trigger
from agent.tools import capture_window, check_responsive, norm, ocr
from agent.tools.reply_store import ReplyStore
from agent.tools.unread import bell_state, find_unread_rows
from agent.tools.wechat_uia import WeChatUIA


def database_path(mode):
    """按运行方式选择独立账本，预览任务不能升格为真实发送。"""
    root = trigger.history_folder('', False).parent
    return root / f'wechat-reply-{mode}.sqlite3'


@contextmanager
def listener_lock(path):
    """一个账号同一时刻只运行一个新流程监听。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name('wechat-reply.listener.lock').open('a+b') as stream:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        yield


def _badge_count(shot, row):
    """角标明确显示数字时才认作确切未读数。"""
    if row['dot_only']:
        return -1
    badge = shot.image.crop(row['box']).convert('RGB')
    badge = badge.resize((badge.width * 4, badge.height * 4))
    value = ''.join(item['text'].strip() for item in ocr(badge))
    return int(value) if re.fullmatch(r'[1-9][0-9]?', value) else -1


class Listener:
    """连接现有微信适配器、回复内核和待处理账本。"""

    def __init__(self, hwnd, titles, store, *, preview, log=print):
        self.hwnd = hwnd
        self.titles = titles
        self.store = store
        self.preview = preview
        self.log = log
        self.adapter = WeChatUIA(hwnd)
        self.adapter.title_ok = self._safe_target
        self.adapter.count_mine = lambda title, text: (
            send._uia_count(hwnd, title, text) if self._safe_target(title) else None)
        self.last_view = None

    def _single_window(self):
        """双开或主窗口变化时，停止操作原来绑定的窗口。"""
        return [window.hwnd for window in graph._wechat_windows()] == [self.hwnd]

    def _safe_target(self, title):
        """发送前需要唯一会话行、选中状态以及可读标题一致。"""
        if (not self._single_window() or not trigger.is_front(self.hwnd)
                or not self.adapter.unique(title) or not self.adapter.selected(title)):
            return False
        shot = capture_window(self.hwnd)
        current = trigger.open_title(shot) if shot is not None else ''
        return bool(current and norm(current) == norm(title))

    def _read(self, title):
        if not self.adapter.unique(title) or not self.adapter.selected(title) or not self._single_window():
            return {'ok': False, 'source': '', 'msgs': [], 'reason': '会话身份无法核对'}
        view = messages.read_chat(self.hwnd, title)
        if view['source'] != 'uia':
            return {**view, 'ok': False, 'reason': '无法核对微信控件中的原文'}
        return view

    def _collect(self, shot):
        """同一张图确认角标和当前打开会话，先登记再切窗。"""
        rows = find_unread_rows(shot)
        for title in self.titles:
            matches = [row for row in rows if norm(row['title']) == norm(title)]
            if matches:
                count = _badge_count(shot, matches[0]) if len(matches) == 1 else -1
                self.store.discover(title, count)
        current = trigger.open_title(shot)
        title = next((title for title in self.titles if norm(title) == norm(current) and current), None)
        if title:
            view = self._read(title)
            if view['ok']:
                self.store.observe(title, view['msgs'])
            else:
                self.store.watch_failed(title, view['reason'])

    def process(self, title):
        """验证会话、刷新来信、生成草稿，必要时走现有发送护栏。"""
        if (not self._single_window() or not self.adapter.unique(title)
                or not check_responsive(self.hwnd)[0]
                or not trigger.prepare(self.hwnd) or not self.adapter.open(title)):
            self.store.read_failed(title, '无法唯一定位并打开测试会话')
            return
        view = messages.wait_quiet(lambda: self._read(title))
        if not view['ok'] or not view['quiet']:
            self.store.read_failed(title, view['reason'] if not view['ok'] else '来信尚未稳定')
            return
        self.store.observe(title, view['msgs'])
        if not self.store.can_reply(title):
            return
        turn = self.store.pending(title)
        if turn is None:
            trigger.remember(title, view)
            return
        history = trigger.remember(title, view)
        previous = reply_turn.context_before(history, turn['incoming'])
        if previous is None:
            self.store.finish(turn['id'], 'review', '待回复批次无法对齐，或本人已在本批来信后发言')
            return
        result = reply_turn.decide(previous, turn['incoming'], self.store.open_issues(title, exclude_turn=turn['id']),
                                   datetime.now(), trigger.TONE, trigger.ask_qwen, trigger.rank_history)
        if result['status'] == 'retry':
            self.store.retry(turn['id'], result['reason'])
            return
        latest = self._read(title)
        if not latest['ok']:
            self.store.retry(turn['id'], '生成后无法复核来信')
            return
        if trigger.msg_key(latest['msgs']) != trigger.msg_key(view['msgs']):
            self.store.observe(title, latest['msgs'])
            return
        if self.preview or result['status'] == 'unanswered':
            state = 'preview' if self.preview else 'unanswered'
            self.store.finish(turn['id'], state, result['reason'], draft=result['text'], issues=result['issues'])
            self.log(f'批次 #{turn["id"]} {state} · {result["reason"]}')
            return
        self._deliver(title, view, turn, result)

    def _deliver(self, title, view, turn, result):
        task = {'to': title, 'msg': result['text'], 'reply_snapshot': trigger.msg_key(view['msgs']),
                'pause': False, 'tier': result['tier'], 'whole_reply': True,
                'audit': lambda status: self.store.audit(turn['id'], status, result['text'],
                                                        issues=result['issues'], reason=result['reason'])}
        outcome, reason = send.deliver(self.adapter, task, log=self.log)
        if outcome == send.OK:
            trigger.remember(title, self._read(title), sent_task=task)
        elif outcome == send.HUMAN:
            self.store.finish(turn['id'], 'review', reason, issues=result['issues'])
        elif outcome == send.RETRY and task.get('msg') is not None:
            self.store.retry(turn['id'], reason)
        self.log(f'批次 #{turn["id"]} {outcome} · {reason}')

    def run(self, seconds):
        """定时重扫，角标和打开会话各自有变化时均会复查。"""
        deadline = time.monotonic() + seconds
        self.store.recover()
        if (not self._single_window() or not check_responsive(self.hwnd)[0]
                or not any(self.adapter.unique(title) for title in self.titles)
                or not trigger.prepare(self.hwnd)):
            raise RuntimeError('未找到可唯一定位的白名单测试会话')
        while time.monotonic() < deadline:
            if not self._single_window() or not check_responsive(self.hwnd)[0]:
                raise RuntimeError('无法确认唯一且可响应的微信主窗口')
            shot = capture_window(self.hwnd)
            if shot is None:
                time.sleep(trigger.POLL)
                continue
            observed = bell_state(shot), trigger.chat_sig(shot), trigger.open_title(shot)
            if observed != self.last_view:
                self._collect(shot)
                self.last_view = observed
            scheduled = self.store.scheduled(self.titles)
            if scheduled:
                title = scheduled[0]
                self.process(title)
                # 轮转避免同一联系人连发或失败时挤掉其他人的队列。
                self.titles = [name for name in self.titles if name != title] + [title]
            else:
                time.sleep(trigger.POLL)


def run(hwnd, seconds, titles, *, mode, path=None):
    """受控演练入口。两个模式都需要显式指定测试会话。"""
    path = Path(path) if path else database_path(mode)
    with listener_lock(database_path(mode)):
        store = ReplyStore(path, mode=mode)
        Listener(hwnd, titles, store, preview=mode == 'preview').run(seconds)

