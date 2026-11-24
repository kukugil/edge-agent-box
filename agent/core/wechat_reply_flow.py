"""微信白名单私聊的可恢复回复入口。预览与实际发送使用不同账本。"""
import msvcrt
import re
import ctypes
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from agent.core import graph, messages, reply_turn, send, trigger
from agent.tools import capture_window, check_responsive, find_windows, idle_seconds, norm, ocr
from agent.tools.window import _foreground_is
from agent.tools.reply_store import ReplyStore
from agent.tools.unread import bell_state, find_unread_rows
from agent.tools.wechat_uia import WeChatUIA


# 有待回复、微信却被人切走或最小化时，多久没人碰键鼠就由程序把微信切回前台（11/19 用户定）
IDLE_WAKE = 60
# 读不到会话时的重试间隔（秒），逐次拉长；读不到不再锁会话
RETRY_BACKOFF = (5, 15, 30, 60)
# 会话行预览多久查一次：每次要走一遍控件树
PREVIEW_EVERY = 2.0
# 会话列表不在顶部时（人滚下去了），多久查一次、人停手几秒后双击气泡拉回顶部（11/21 真机：滚到底后漏消息）
TOP_EVERY = 5.0
TOP_IDLE = 10


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

    def __init__(self, hwnd, titles, store, *, preview, log=print, mail=None):
        self.hwnd = hwnd
        self.mail = mail       # MailWatcher 或 None：邮件和微信在同一个循环里轮流用屏幕
        self.handover = None   # 程序上次用完屏幕时的 (前台窗口, monotonic 时刻)，见 _ours
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
        self.next_top = 0.0
        self.top_waiting = False
        self.human = None      # run() 里装上真人输入钩子；没装上时退回 idle_seconds
        self.human_since = time.monotonic()

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
        # 先 prepare 再核对唯一：会话行滚出可见范围时要滚动列表找回，滚轮要微信在前台（11/19 文件传输助手被挤下去，连败 11 次）
        if (not trigger.prepare(self.hwnd)
                or not (self.adapter.unique(title) or (self.adapter.scroll_to(title) and self.adapter.unique(title)))
                or not self.adapter.open(title)):
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

    def run(self, seconds, stop=lambda: False):
        """定时重扫，角标和打开会话各自有变化时均会复查。

        stop：托盘点"停止"时变成 True。只在两轮之间查：一轮里的发送要么做完要么没开始，不会把稿子留在输入框。
        """
        deadline = time.monotonic() + seconds
        from agent.tools.human_input import HumanInput
        self.human = HumanInput().start()
        self.human_since = time.monotonic()
        try:
            self._run(deadline, stop)
        finally:
            self.human.stop()

    def _run(self, deadline, stop):
        self.store.recover()
        # 先 prepare 把微信拉到前面再核对能否响应，和旧入口一致；反过来做，人在别的窗口时一启动就失败
        if (not self._single_window() or not trigger.prepare(self.hwnd)
                or not check_responsive(self.hwnd)[0]):
            raise RuntimeError('未找到可唯一定位的白名单测试会话')
        # 11/21 真机：启动时会话列表被滚到下面，白名单会话的行不在控件树里，一启动就退出。
        # 启动本来就把微信切到了前台，这里先双击气泡回到顶部再核对
        if not any(self.adapter.unique(title) for title in self.titles):
            self.adapter.back_to_top()
            if not any(self.adapter.unique(title) for title in self.titles):
                raise RuntimeError('未找到可唯一定位的白名单测试会话')
        while time.monotonic() < deadline:
            if stop():
                self.log('已停止监听')
                return
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
            if not minimized:
                self._keep_top(front)
            self._poll_previews()
            if self.mail is not None:
                self.mail.poll()
            scheduled = self._ready(self.store.scheduled(self.titles))
            if scheduled and not front:
                idle = idle_seconds()
                if (idle < IDLE_WAKE and not self._ours(idle)) or time.monotonic() < self.next_wake:
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
                self._handed_over()
                text = self.adapter.preview(title)
                if text is not None:
                    self.previews[title] = text   # 自己刚发的回复改了预览，不能再当门铃
                # 轮转避免同一联系人连发或失败时挤掉其他人的队列。
                self.titles = [name for name in self.titles if name != title] + [title]
            elif self.mail is not None and self.mail.has_work() and self._mail_turn():
                self.mail.process()
                self._handed_over()
            else:
                time.sleep(trigger.POLL)

    def _human_idle(self) -> float:
        """真人多少秒没碰键鼠。钩子分得清程序自己的点击；钩子没装上就退回 GetLastInputInfo（会被程序的输入清零，偏保守）。"""
        if self.human is not None and self.human.ok:
            return time.monotonic() - max(self.human.last, self.human_since)
        return idle_seconds()

    def _keep_top(self, front):
        """会话列表被人滚到下面时，等人停手再双击气泡拉回顶部；否则白名单会话的行不在控件树里，新消息发现不了。
        微信在前台：人停手 TOP_IDLE 秒就拉；被别的窗口挡着：和"切回前台"一样等 IDLE_WAKE 秒。"""
        now = time.monotonic()
        if now < self.next_top:
            return
        self.next_top = now + TOP_EVERY
        if self.adapter.at_top() is not False:      # 在顶部，或读不到（读不到不动手）
            self.top_waiting = False
            return
        idle = self._human_idle()
        need = TOP_IDLE if front else IDLE_WAKE
        if idle < need:
            if not self.top_waiting:
                self.log(f'会话列表不在最上面，新消息可能看不到：等键鼠停 {need} 秒后拉回顶部')
            self.top_waiting = True
            return
        if not front and not trigger.prepare(self.hwnd):
            self.next_top = now + 30
            return
        ok = self.adapter.back_to_top()
        self.top_waiting = False
        self._handed_over()
        self.log('已把会话列表拉回顶部' if ok else '没能把会话列表拉回顶部（气泡被挡住或没反应），30 秒后再试')
        if not ok:
            self.next_top = now + 30

    def _handed_over(self):
        """程序用完屏幕：记下此刻的前台窗口和时刻。"""
        self.handover = (ctypes.windll.user32.GetForegroundWindow(), time.monotonic())

    def _ours(self, idle):
        """屏幕是不是还在程序手里：前台仍是程序上次用完的那个窗口，而且那之后没人碰过键鼠。
        这时邮件和微信轮流可以直接接着用，不用等 60 秒（程序自己的点击也会刷新"最近一次键鼠"）。
        11/19 真机：原来只看"前台是不是微信或邮箱"，人在邮箱里写信时停 3 秒就被抢走了。"""
        if self.handover is None:
            return False
        window, done = self.handover
        return ctypes.windll.user32.GetForegroundWindow() == window and idle >= time.monotonic() - done - 0.5

    def _mail_turn(self):
        """轮到邮件时能不能用屏幕：屏幕还在程序手里，或者键鼠停了 IDLE_WAKE 秒。人正在用电脑就等。"""
        idle = idle_seconds()
        # 用自己的标记：和微信那边共用 waiting 时，两边每轮互相翻转，日志每秒刷两行（11/19 真机）
        if idle >= IDLE_WAKE or self._ours(idle):
            self.mail_waiting = False
            return True
        if not getattr(self, 'mail_waiting', False):
            self.log(f'有待回复的邮件：等键鼠停 {IDLE_WAKE} 秒后再接管')
        self.mail_waiting = True
        return False


def run(hwnd, seconds, titles, *, mode, path=None, stop=lambda: False, mail_hwnd=None, allowed_mail=()):
    """受控演练入口。两个模式都需要显式指定测试会话。mail_hwnd 给了就同时盯 QQ 邮箱（只在发送模式）。"""
    path = Path(path) if path else database_path(mode)
    with listener_lock(database_path(mode)):
        store = ReplyStore(path, mode=mode)
        mail = None
        if mail_hwnd and mode == 'send':
            from agent.core.mail_flow import MailWatcher
            mail = MailWatcher(mail_hwnd, store, allowed_mail)
        Listener(hwnd, titles, store, preview=mode == 'preview', mail=mail).run(seconds, stop)

