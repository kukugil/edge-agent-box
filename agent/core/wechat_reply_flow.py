"""微信白名单私聊的可恢复回复入口。预览与实际发送使用不同账本。"""
import msvcrt
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from agent.core import graph, messages, reply_turn, send, trigger
from agent.tools import capture_window, check_responsive, find_windows, idle_seconds, norm, ocr
from agent.tools.reply_store import ReplyStore
from agent.tools.unread import bell_state, find_unread_rows
from agent.tools.wechat_uia import WeChatUIA


# 有待回复、微信却被人切走或最小化时，多久没人碰键鼠就由程序把微信切回前台（11/19 用户定）
IDLE_WAKE = 60
# 读不到会话时的重试间隔（秒），逐次拉长；读不到不再锁会话
RETRY_BACKOFF = (5, 15, 30, 60)
# 会话行预览多久查一次：每次要走一遍控件树
PREVIEW_EVERY = 2.0


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
        self.started = datetime.now()
        self.failures = {}     # 会话 → (连续读不到次数, 下次可重试的 monotonic 时刻)
        self.previews = {}     # 会话 → 上次看到的会话行文字
        self.next_preview = 0.0
        self.next_wake = 0.0

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
        if self.store.observe(title, msgs, since=self.started) == 'anchor':
            self.log('屏幕和上次对不上，按我方最后一条重新划界')

    def _failed(self, title, reason):
        """读不到：记一次，按退避稍后再试，不锁会话。"""
        count = self.failures.get(title, (0, 0))[0] + 1
        delay = RETRY_BACKOFF[min(count, len(RETRY_BACKOFF)) - 1]
        self.failures[title] = (count, time.monotonic() + delay)
        self.store.read_failed(title, reason)
        self.log(f'这次没读到会话（第 {count} 次）：{reason}，{delay} 秒后再试')

    def _ready(self, titles):
        now = time.monotonic()
        return [title for title in titles if self.failures.get(title, (0, 0))[1] <= now]

    def _poll_previews(self):
        """会话行文字变了就登记待看。角标会被手机已读清掉；文件传输助手模拟时没有角标，也靠这个。"""
        if time.monotonic() < self.next_preview:
            return
        self.next_preview = time.monotonic() + PREVIEW_EVERY
        for title in self.titles:
            text = self.adapter.preview(title)
            if text is None:
                continue
            if title in self.previews and text != self.previews[title]:
                self.store.discover(title, -1)
            self.previews[title] = text

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
        if not self._single_window():
            return   # 主循环下一轮处理：最小化就等，换了窗口就停
        if not self.adapter.unique(title) or not trigger.prepare(self.hwnd) or not self.adapter.open(title):
            self._failed(title, '无法唯一定位并打开测试会话')
            return
        view = messages.wait_quiet(lambda: self._read(title))
        if not view['ok'] or not view['quiet']:
            self._failed(title, view['reason'] if not view['ok'] else '来信尚未稳定')
            return
        self.failures.pop(title, None)
        self._observe(title, view['msgs'])
        if not self.store.can_reply(title):
            return
        turn = self.store.pending(title)
        if turn is None:
            trigger.remember(title, view)
            return
        history = trigger.remember(title, view)
        previous, why = reply_turn.align(history, turn['incoming'])
        if previous is None:
            self.store.finish(turn['id'], 'review', why)
            self.log(f'批次 #{turn["id"]} review · {why}')
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
            minimized = not self._single_window()
            if minimized and not self._minimized():
                raise RuntimeError('无法确认唯一且可响应的微信主窗口')
            front = False
            if not minimized:
                ok, why = check_responsive(self.hwnd)
                # 只是不在前台（人在用别的窗口）照常看角标和预览，不动微信；隐藏、卡死仍停
                if not ok and '前台' not in why:
                    raise RuntimeError('无法确认唯一且可响应的微信主窗口：' + why)
                front = ok
            if front and self.waiting:
                self.log('微信回到前台，继续监听')
                self.waiting = False
            # 被挡住时 PrintWindow 仍能截到；最小化时截不到，只靠会话行预览
            shot = None if minimized else capture_window(self.hwnd)
            if shot is not None:
                observed = bell_state(shot), trigger.chat_sig(shot), trigger.open_title(shot)
                if observed != self.last_view:
                    self._collect(shot)
                    self.last_view = observed
            self._poll_previews()
            scheduled = self._ready(self.store.scheduled(self.titles))
            if scheduled and not front:
                idle = idle_seconds()
                if idle < IDLE_WAKE or time.monotonic() < self.next_wake:
                    if not self.waiting:
                        where = '被最小化' if minimized else '不在前台'
                        self.log(f'有待回复，微信{where}：等键鼠停 {IDLE_WAKE} 秒后再接管')
                    self.waiting = True
                    time.sleep(trigger.POLL)
                    continue
                self.log(f'{int(idle)} 秒没人碰键鼠，把微信切回前台')
                if not trigger.prepare(self.hwnd):
                    self.next_wake = time.monotonic() + 10   # 切不过来（比如藏到了托盘）就别每半秒试一次
                    time.sleep(trigger.POLL)
                    continue
                self.waiting = False
            if scheduled:
                title = scheduled[0]
                self.process(title)
                text = self.adapter.preview(title)
                if text is not None:
                    self.previews[title] = text   # 自己刚发的回复改了预览，不能再当门铃
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

