"""QQ 邮箱自动回复：和微信共用一个监听、一本账和同一套回复内核。

流程（每一步点哪里由程序按网页控件树定，不让模型看图决定，和微信 11/9 以后的做法一样）：
  发现  每 3 秒读一次收件箱列表，出现新的一行、发件人在白名单、时间是今天 → 登记一批
  想    用标题 + 列表里的正文预览交给回复内核（reply_turn.decide）生成回复
  做    记下「已发送」最上面 3 行 → 回收件箱点开这封 → 点「回复」→ 点正文 → 粘贴
  核对  发送前：收件人只能是测试邮箱、主题是"回复：原标题"、正文正好是这句（mail_nodes.send_ready）
        发送后：「已发送」最上面正好新插进这一封才算发出；说不清就转人工，不重发
坑：
  - 测试邮箱自己发给自己，回出去的"回复：…"也会落进收件箱，所以"回复："开头的不当新邮件，否则自己回自己。
  - Chrome 最小化时读不到网页内容，这时看不到新邮件；被挡住没关系（启动参数关了后台降频）。
  - 收件箱列表不会自己刷新（11/19 真机：等了两分半，列表里一直没有新信），只有窗口标题会变成
    "收到 N 封新邮件"，而且停在收件箱时再点收件箱也不刷新。所以标题响了（或每 2 分钟）先切到「已发送」
    再切回「收件箱」，这一步和处理邮件一样要等人停手。
  - 只用列表里的预览当正文：演示邮件都很短。长邮件只看得到开头，打开后读全文还没做。
"""
import re
import time
from datetime import datetime

from agent.core import reply_turn, trigger
from agent.core.mail_check import LIST_TOP, NAV_RIGHT, judge_replied, top_subjects
from agent.tools import center, click, paste_text, window_rect
from agent.tools.inputs import InputRefused
from agent.tools.web import body_text, find_mail_window, open_folder, read_page, wait_page
from agent.tools.window import _foreground_is, _text, activate, user32, wintypes

MAIL = 'QQ邮箱'            # 账本和托盘里这一路的名字
POLL_EVERY = 3.0          # 读一次收件箱约 0.6 秒，太勤会拖慢微信那边的循环
REFRESH_EVERY = 30.0      # 标题提示有新邮件时，最多这么久点一次「收件箱」刷新列表
FIND_EVERY = 10.0         # 还没接上邮箱窗口时多久找一次：每次要把每个 Chrome 窗口的控件树读一遍
REFRESH_IDLE = 120.0      # 标题门铃会自己消失（11/19 真机：几分钟后提示没了、列表仍是旧的），再按这个间隔定时刷新
# 列表里今天的邮件显示"刚刚""N分钟前""N小时前"或时分，更早的显示日期（11/19 真机：刚到的显示"6分钟前"）
TODAY = re.compile(r'^(刚刚|\d{1,2}分钟前|\d{1,2}小时前|\d{1,2}:\d{2})$')
SEND = '发送'
REPLY = '回复'


def inbox_rows(items):
    """把收件箱页面拆成一封封邮件。

    输入：read_page 的结果
    输出：[{'sender', 'time', 'subject', 'preview', 'box': 标题那块的位置}]，从上到下
    两种版式，按窗口宽窄自动切换：
      多行（11/19 真机，窗口窄）：每封占两行半，第一行左边发件人、右边时间；下面一行标题，再下面一行正文预览。
      单行（11/23 真机，窗口宽）：发件人、标题、预览、时间排在同一行。原来只认多行，宽窗口下一封都认不出来。
    """
    texts = [it for it in items if it['kind'] == 'TextControl' and it['box'][0] > NAV_RIGHT
             and it['box'][1] > LIST_TOP and it['text']]
    rows = []
    for stamp in texts:
        x1, y1 = stamp['box'][0], stamp['box'][1]
        if x1 < 700 or len(stamp['text']) > 8 or not re.search(r'\d', stamp['text']):
            continue
        line = [it for it in texts if abs(it['box'][1] - y1) <= 3]
        if any(it['box'][0] > x1 for it in line):
            continue              # 时间在这一行最右边；不是的话多半是带数字的短预览
        sender = sorted((it for it in line if it['box'][0] < x1 - 100), key=lambda it: it['box'][0])
        if len(sender) >= 2:      # 单行版式：从左到右是发件人、标题、预览
            rows.append({'sender': sender[0]['text'], 'time': stamp['text'], 'subject': sender[1]['text'],
                         'preview': ' '.join(it['text'] for it in sender[2:]), 'box': sender[1]['box']})
            continue
        subject = [it for it in texts if 15 <= it['box'][1] - y1 <= 32 and it['box'][0] < x1 - 100]
        preview = [it for it in texts if 33 <= it['box'][1] - y1 <= 55 and it['box'][0] < x1 - 100]
        if len(sender) != 1 or len(subject) != 1:
            continue
        rows.append({'sender': sender[0]['text'], 'time': stamp['text'], 'subject': subject[0]['text'],
                     'preview': preview[0]['text'] if len(preview) == 1 else '', 'box': subject[0]['box']})
    return sorted(rows, key=lambda row: row['box'][1])


def row_key(row):
    """认同一封信。不带时间：相对时间每分钟都在变（"6分钟前"→"7分钟前"），带上会把同一封当新信反复登记。"""
    return row['sender'], row['subject'], row['preview']


def on_inbox(items):
    """当前页是不是收件箱列表：主体顶部有「收件箱」标题。"""
    return any(it['text'] == '收件箱' and it['box'][0] > NAV_RIGHT and it['box'][1] < 200 for it in items)


def skip_reason(row, allowed):
    """这一行为什么不回；该回交回 None。

    名单只认邮箱地址或 QQ 号：列表里显示的是对方昵称时对不上，这封就不回（11/23 真机：真实来信被悄悄跳过）。
    """
    names = {name for addr in allowed for name in (addr, addr.split('@')[0])}
    if row['subject'].startswith('回复：'):
        return '是回出去的"回复："那封'
    if row['sender'] not in names:
        return '发件人不在邮件名单（名单只认邮箱地址或 QQ 号）'
    if not TODAY.match(row['time']):
        return '不是今天的'
    return None


def wanted(row, allowed):
    """这一行要不要回：发件人在白名单、是今天的、不是"回复："开头（自己回出去又落进收件箱的那封）。"""
    return skip_reason(row, allowed) is None


class Abort(Exception):
    """这一轮做不下去（切不到前台、页面没出来）：不点了，按重试处理。"""


class MailWatcher:
    """盯一个 QQ 邮箱网页窗口。poll 只读不点；process 处理最早的一封。"""

    def __init__(self, hwnd, store, allowed, *, log=print, find=find_mail_window):
        self.hwnd = hwnd          # None：邮箱窗口还没开，poll 里每 FIND_EVERY 秒找一次
        self.find = find
        self.next_find = 0.0
        self.store = store
        self.allowed = list(allowed)
        self.log = log
        self.seen = None          # 启动时屏上已有的邮件只当基线，不回
        self.next_poll = 0.0
        self.hidden = False
        self.refresh = False      # 标题提示有新邮件，列表要点「收件箱」才会刷新
        self.title = None
        self.next_refresh = time.monotonic()   # 刚接入时读到的就是新的，2 分钟后再定时刷新

    # ---------- 发现 ----------
    def attach(self):
        """确认手上有邮箱窗口：窗口关了就放下，没有就隔一阵找一次。交回现在有没有窗口。

        为什么在运行中找：原来只在启动时找一次，人先开监听、后开邮箱窗口，这一整轮都不看邮件（11/23 真机）。
        换了窗口不清基线：还是同一个邮箱，清了会把已有的信当新信再回一遍。
        """
        if self.hwnd is not None and not user32.IsWindow(wintypes.HWND(self.hwnd)):
            self.hwnd = None
            self.log('邮箱窗口关了：重新点"打开邮箱窗口"后会自动接上')
        if self.hwnd is not None:
            return True
        if time.monotonic() < self.next_find:
            return False
        self.next_find = time.monotonic() + FIND_EVERY
        self.hwnd = self.find()
        if self.hwnd is not None:
            self.next_poll = 0.0
            self.log('邮箱窗口接上了')
        return self.hwnd is not None

    def poll(self):
        """读一次收件箱，登记新邮件。交回这次登记了几封。"""
        if not self.attach() or time.monotonic() < self.next_poll:
            return 0
        self.next_poll = time.monotonic() + POLL_EVERY
        if user32.IsIconic(wintypes.HWND(self.hwnd)):
            if not self.hidden:
                self.log('邮箱窗口被最小化：看不到新邮件，还原后接着看')
            self.hidden = True
            return 0
        self.hidden = False
        title = _text(user32.GetWindowTextW, self.hwnd)
        if '封新邮件' in title and title != self.title:
            self.refresh = True   # 标题变了才算门铃响：刷新后标题可能一直挂着"收到 N 封"，不能每 30 秒刷一次
        self.title = title
        items = read_page(self.hwnd)
        if not on_inbox(items):
            return 0              # 停在别的页（人在看信、或上一轮没回到收件箱）：先不登记，免得把旧信当新信
        rows = inbox_rows(items)
        keys = {row_key(row) for row in rows}
        if self.seen is None:
            self.seen = keys
            self.log(f'邮箱已接入：收件箱现有 {len(rows)} 封只作基线')
            return 0
        fresh = [row for row in rows if row_key(row) not in self.seen]
        self.seen |= keys
        count = 0
        for row in fresh:
            why = skip_reason(row, self.allowed)
            if why:
                # 不写发件人和标题：托盘动态演示时人人看得见。自己回出去的那封每次都有，不报
                if not row['subject'].startswith('回复：'):
                    self.log(f'来了 1 封新邮件，不回：{why}')
                continue
            mail = {'who': 'them', 'kind': 'text', 'text': f"{row['subject']}\n{row['preview']}",
                    'at': datetime.now(), **{k: row[k] for k in ('sender', 'time', 'subject', 'preview')}}
            if self.store.enqueue(MAIL, [mail]) is not None:
                count += 1
        if count:
            self.log(f'收到 {count} 封新邮件')
        return count

    def _refresh_due(self):
        now = time.monotonic()
        return (self.refresh and now >= self.next_refresh) or now >= self.next_refresh + REFRESH_IDLE

    def has_work(self):
        """有要用屏幕的事：待回复的邮件，或者该刷新列表了（标题门铃响了，或者离上次刷新已经 2 分钟）。"""
        if self.hwnd is None:
            return False
        if self.seen is not None and self._refresh_due():
            return True
        return self.store.can_reply(MAIL) and self.store.pending(MAIL) is not None

    # ---------- 做 ----------
    def _front(self):
        ok, why = activate(self.hwnd)
        if not ok or not _foreground_is(self.hwnd):
            raise Abort(f'邮箱窗口切不到前台（{why}）')

    def _tap(self, box_or_point):
        """点之前再确认一次 Chrome 在前台：被挡住时点下去会落到挡在前面的窗口上（11/10 Codex）。"""
        self._front()
        l, t, _, _ = window_rect(self.hwnd)
        x, y = center(box_or_point) if len(box_or_point) == 4 else box_or_point
        click(l + int(x), t + int(y))

    def _folder(self, name):
        items = open_folder(self.hwnd, name)
        if not items:
            raise Abort(f'打不开「{name}」')
        return items

    def _button(self, items, text):
        """顶部工具栏上文字正好是 text 的按钮（逐字比：「回复全部」「分别发送」都不算）。"""
        hits = [it for it in items if it['text'] == text and it['box'][0] > NAV_RIGHT and it['box'][1] < 230]
        return min(hits, key=lambda it: it['box'][1]) if hits else None

    def process(self):
        """先刷新列表（标题提示有新邮件时），再处理最早登记的一封。任何一步没把握都不点发送。"""
        if self.seen is not None and self._refresh_due():
            self.refresh = False
            self.next_refresh = time.monotonic() + REFRESH_EVERY
            try:
                self._front()
                # 停在收件箱时再点收件箱，列表不会重新加载（11/19 真机：刷了两次列表都没变）；
                # 先切到「已发送」再切回来才会重新拉取（同日实测：4.4 秒，新信出现在最上面）
                self._folder('已发送')
                self._folder('收件箱')
            except Abort as exc:
                self.log(f'刷新收件箱没成功：{exc}')
                return
            self.next_poll = 0
            self.poll()
            return
        turn = self.store.pending(MAIL)
        if turn is None or not self.store.can_reply(MAIL):
            return
        mail = turn['incoming'][0]
        result = reply_turn.decide([], turn['incoming'], self.store.open_issues(MAIL, exclude_turn=turn['id']),
                                   datetime.now(), trigger.TONE, trigger.ask_qwen, trigger.rank_history)
        if result['status'] == 'retry':
            self.store.retry(turn['id'], result['reason'])
            self.log(f'邮件批次 #{turn["id"]} retry · {result["reason"]}')
            return
        if result['status'] == 'unanswered':
            self.store.finish(turn['id'], 'unanswered', result['reason'], draft=result['text'], issues=result['issues'])
            self.log(f'邮件批次 #{turn["id"]} unanswered · {result["reason"]}')
            return
        try:
            outcome, why = self._deliver(turn, mail, result)
        except Abort as exc:
            self.store.retry(turn['id'], str(exc))
            outcome, why = 'retry', str(exc)
        finally:
            try:
                self._folder('收件箱')     # 回到收件箱，下一轮才能接着发现新邮件
            except Abort:
                pass
        self.log(f'邮件批次 #{turn["id"]} {outcome} · {why}')

    def _deliver(self, turn, mail, result):
        # 换行改成空格：编辑器把多行拆成几块，核对时拼起来没有换行，和原文永远对不上
        subject, text = mail['subject'], ' '.join(result['text'].split())
        task = {'subject': subject, 'msg': text}
        self._front()
        top_before = top_subjects(self._folder('已发送'), 3)
        inbox = self._folder('收件箱')
        rows = [row for row in inbox_rows(inbox) if row_key(row) == row_key(mail)]
        if len(rows) != 1:
            raise Abort('收件箱里找不到这封（或有两封一模一样的）')
        self._tap(rows[0]['box'])
        button, items = wait_page(self.hwnd, lambda it: self._button(it, REPLY))
        if button is None:
            raise Abort('打开邮件后没看到「回复」按钮')
        self._tap(button['box'])
        reply_subject = '回复：' + subject
        ready, items = wait_page(self.hwnd, lambda it: [x for x in it if x['kind'] == 'EditControl'
                                                        and x['text'] == '主题' and x['value'] == reply_subject])
        if not ready:
            raise Abort('回信页没出来')
        # 回信页先出框、再整页重画一次，刚出来就粘贴会被清掉（11/19 真机连续两次"正文没写进去"，写信页同样）
        time.sleep(2)
        written = None
        for _ in range(2):
            items = read_page(self.hwnd)
            body = [it for it in items if it['text'] == '输入正文']
            if not body:
                raise Abort('回信页没出来')
            x1, y1, _, _ = body[0]['box']
            self._tap([x1 + 60, y1 + 20])     # 点正文第一行：点正中会点进下面引用的原邮件（11/5）
            try:
                paste_text(text, mode='replace')
            except InputRefused:
                pass                           # 剪贴板读回会带上引用区，下面按控件树里正文那块再核一次
            written, items = wait_page(self.hwnd, lambda it: body_text(it) == text, timeout=3)
            if written:
                break
        if not written:
            raise Abort('正文没写进去')
        from agent.core.mail_nodes import send_ready   # 晚导入：mail_nodes 导入时要读白名单里的邮箱
        ok, why = send_ready(items, task)
        if not ok:
            raise Abort('发送前核对没过：' + why)
        self.store.audit(turn['id'], 'about_to_send', text, issues=result['issues'])
        # 发送许可：点之前当场再读一次，收件人、主题、正文、按钮位置任何一样变了都不点
        items = read_page(self.hwnd)
        ok, why = send_ready(items, task)
        send = self._button(items, SEND)
        if not ok or send is None:
            self.store.audit(turn['id'], 'fail', text)
            raise Abort('发送许可作废：' + (why or '找不到「发送」按钮'))
        self._tap(send['box'])
        time.sleep(2)                      # 发完要等列表刷新，一点完就数会数到上一封（D8）
        status, why = 'unknown', '未确认新增的已发送邮件'
        for _ in range(10):
            try:
                sent = self._folder('已发送')
            except Abort:
                time.sleep(1)
                continue
            status, why = judge_replied(sent, reply_subject, 0, top_before=top_before)
            if status == 'ok':
                break
            time.sleep(1)
        self.store.audit(turn['id'], status if status in ('ok', 'unknown') else 'unknown', text,
                         reason=result['reason'] if status == 'ok' else why)
        return ('sent', '「已发送」里核对到这封') if status == 'ok' else ('review', why)
