"""微信白名单私聊的可恢复回复入口。预览与实际发送使用不同账本。"""
import msvcrt
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from agent.core import graph, messages, reply_turn, send, trigger
from agent.tools import capture_window, check_responsive, find_windows, norm, ocr
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
        self.waiting = False

    def _single_window(self):
        """双开或主窗口变化时，停止操作原来绑定的窗口。"""
        return [window.hwnd for window in graph._wechat_windows()] == [self.hwnd]

    def _minimized(self):
        """只有绑定的主窗口被最小化、也没有别的微信大窗口时，算作人临时收起，可以等。
        11/19 好友测试三次都是人切窗时误把微信最小化（多半是点了任务栏图标），程序跟着退出。
        等的时候不去还原：还原等于抢前台，人可能正在用别的窗口。"""
        return not graph._wechat_windows() and any(
            w.hwnd == self.hwnd and w.visible and w.minimized
            for w in find_windows(title='微信', class_prefix='Qt'))

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
        # 模拟没开或不是文件传输助手时原样交回；开着时把手机发来的我方气泡改标为来信
        return trigger.sim_view(title, view)

    @staticmethod
    def _simulated(title):
        return trigger.SIM['on'] and norm(title) == norm(trigger.SIM_NAME)

    def _observe(self, title, msgs):
        """登记来信。模拟时开始前的旧气泡标成 none，账本快照会丢掉它们、边界变空；
        这里只对账本按我方气泡算进边界，聊天历史和回复内核看到的仍是原标记。"""
        if self._simulated(title):
            msgs = [{**msg, 'who': 'us'} if msg['who'] == 'none' else msg for msg in msgs]
        self.store.observe(title, msgs)

    def _collect(self, shot):
        """同一张图确认角标和当前打开会话，先登记再切窗。"""
        rows = find_unread_rows(shot)
        for title in self.titles:
            matches = [row for row in rows if norm(row['title']) == norm(title)]
            if matches:
                count = _badge_count(shot, matches[0]) if len(matches) == 1 else -1
                if not self._simulated(title):
                    # 模拟时角标对不上屏幕：手机来的消息在电脑上算我方，只认屏幕上新增的气泡
                    self.store.discover(title, count)
        current = trigger.open_title(shot)
        title = next((title for title in self.titles if norm(title) == norm(current) and current), None)
        if title:
            view = self._read(title)
            if view['ok']:
                self._observe(title, view['msgs'])
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
        self._observe(title, view['msgs'])
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
            self._observe(title, latest['msgs'])
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
            # 必须在下一次读屏前登记：否则模拟会把刚发的回复当成手机来信，自己回自己
            trigger.sim_sent(task)
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
        # 先 prepare 把微信拉到前面再核对能否响应，和旧入口一致；反过来做，人在别的窗口时一启动就失败
        if (not self._single_window() or not trigger.prepare(self.hwnd)
                or not check_responsive(self.hwnd)[0]
                or not any(self.adapter.unique(title) for title in self.titles)):
            raise RuntimeError('未找到可唯一定位的白名单测试会话')
        while time.monotonic() < deadline:
            if not self._single_window():
                if not self._minimized():
                    raise RuntimeError('无法确认唯一且可响应的微信主窗口')
                if not self.waiting:
                    self.log('微信被最小化，等它恢复到前台再看')
                self.waiting = True
                time.sleep(trigger.POLL)
                continue
            ok, why = check_responsive(self.hwnd)
            if not ok:
                # 只是不在前台（人在用别的窗口）就等，不截图也不动微信；隐藏、卡死仍停（最小化在上面等）
                if '前台' not in why:
                    raise RuntimeError('无法确认唯一且可响应的微信主窗口：' + why)
                if not self.waiting:
                    self.log('微信不在前台，等它回到前台再看')
                self.waiting = True
                time.sleep(trigger.POLL)
                continue
            if self.waiting:
                self.log('微信回到前台，继续监听')
                self.waiting = False
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

