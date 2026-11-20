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
  - 只用列表里的预览当正文：演示邮件都很短。长邮件只看得到开头，打开后读全文还没做。
"""
import re
import time
from datetime import datetime

from agent.core import reply_turn, trigger
from agent.core.mail_check import LIST_TOP, NAV_RIGHT, judge_replied, top_subjects
from agent.tools import center, click, paste_text, window_rect
from agent.tools.inputs import InputRefused
from agent.tools.web import body_text, open_folder, read_page, wait_page
from agent.tools.window import _foreground_is, activate, user32, wintypes

MAIL = 'QQ邮箱'            # 账本和托盘里这一路的名字
POLL_EVERY = 3.0          # 读一次收件箱约 0.6 秒，太勤会拖慢微信那边的循环
TODAY = re.compile(r'^\d{1,2}:\d{2}$')     # 列表里今天的邮件只显示时分，更早的显示日期
SEND = '发送'
REPLY = '回复'


def inbox_rows(items):
    """把收件箱页面拆成一封封邮件。

    输入：read_page 的结果
    输出：[{'sender', 'time', 'subject', 'preview', 'box': 标题那块的位置}]，从上到下
    版式（11/19 真机）：每封占两行半，第一行左边发件人、右边时间；下面一行标题，再下面一行正文预览。
    """
    texts = [it for it in items if it['kind'] == 'TextControl' and it['box'][0] > NAV_RIGHT
             and it['box'][1] > LIST_TOP and it['text']]
    rows = []
    for stamp in texts:
        x1, y1 = stamp['box'][0], stamp['box'][1]
        if x1 < 700 or len(stamp['text']) > 8 or not re.search(r'\d', stamp['text']):
            continue
        sender = [it for it in texts if abs(it['box'][1] - y1) <= 3 and it['box'][0] < x1 - 100]
        subject = [it for it in texts if 15 <= it['box'][1] - y1 <= 32 and it['box'][0] < x1 - 100]
        preview = [it for it in texts if 33 <= it['box'][1] - y1 <= 55 and it['box'][0] < x1 - 100]
        if len(sender) != 1 or len(subject) != 1:
            continue
        rows.append({'sender': sender[0]['text'], 'time': stamp['text'], 'subject': subject[0]['text'],
                     'preview': preview[0]['text'] if len(preview) == 1 else '', 'box': subject[0]['box']})
    return sorted(rows, key=lambda row: row['box'][1])


def row_key(row):
    return row['sender'], row['time'], row['subject'], row['preview']


def on_inbox(items):
    """当前页是不是收件箱列表：主体顶部有「收件箱」标题。"""
    return any(it['text'] == '收件箱' and it['box'][0] > NAV_RIGHT and it['box'][1] < 200 for it in items)


def wanted(row, allowed):
    """这一行要不要回：发件人在白名单、是今天的、不是"回复："开头（自己回出去又落进收件箱的那封）。"""
    names = {name for addr in allowed for name in (addr, addr.split('@')[0])}
    return row['sender'] in names and bool(TODAY.match(row['time'])) and not row['subject'].startswith('回复：')


class Abort(Exception):
    """这一轮做不下去（切不到前台、页面没出来）：不点了，按重试处理。"""


class MailWatcher:
    """盯一个 QQ 邮箱网页窗口。poll 只读不点；process 处理最早的一封。"""

    def __init__(self, hwnd, store, allowed, *, log=print):
        self.hwnd = hwnd
        self.store = store
        self.allowed = list(allowed)
        self.log = log
        self.seen = None          # 启动时屏上已有的邮件只当基线，不回
        self.next_poll = 0.0
        self.hidden = False

    # ---------- 发现 ----------
    def poll(self):
        """读一次收件箱，登记新邮件。交回这次登记了几封。"""
        if time.monotonic() < self.next_poll:
            return 0
        self.next_poll = time.monotonic() + POLL_EVERY
        if user32.IsIconic(wintypes.HWND(self.hwnd)):
            if not self.hidden:
                self.log('邮箱窗口被最小化：看不到新邮件，还原后接着看')
            self.hidden = True
            return 0
        self.hidden = False
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
            if not wanted(row, self.allowed):
                continue
            mail = {'who': 'them', 'kind': 'text', 'text': f"{row['subject']}\n{row['preview']}",
                    'at': datetime.now(), **{k: row[k] for k in ('sender', 'time', 'subject', 'preview')}}
            if self.store.enqueue(MAIL, [mail]) is not None:
                count += 1
        if count:
            self.log(f'收到 {count} 封新邮件')
        return count

    def has_work(self):
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
        """处理最早登记的一封。任何一步没把握都不点发送。"""
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
        subject, text = mail['subject'], result['text']
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
        body = [it for it in items if it['text'] == '输入正文']
        if not ready or not body:
            raise Abort('回信页没出来')
        x1, y1, _, _ = body[0]['box']
        self._tap([x1 + 60, y1 + 20])     # 点正文第一行：点正中会点进下面引用的原邮件（11/5）
        try:
            paste_text(text, mode='replace')
        except InputRefused:
            pass                           # 剪贴板读回会带上引用区，下面按控件树里正文那块再核一次
        written, items = wait_page(self.hwnd, lambda it: body_text(it) == text, timeout=3)
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
