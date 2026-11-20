"""离线验证 QQ 邮箱自动回复：列表拆行、发现新邮件、发送前后的核对。不碰真实网页。

运行：python -m unittest agent.demos.test_mail_flow
"""
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from agent.core import mail_flow, wechat_reply_flow
from agent.core.mail_flow import MAIL, MailWatcher, inbox_rows, wanted
from agent.tools.reply_store import ReplyStore

ME = 'tester@example.test'


def t(text, x, y, w=80):
    return {'text': text, 'box': [x, y, x + w, y + 16], 'score': 1.0, 'kind': 'TextControl', 'value': ''}


def inbox(*mails):
    """照 11/19 真机版式拼一页收件箱：发件人和时间一行，下面标题，再下面预览，每封占 84 像素。"""
    items = [t('收件箱', 53, 203), t('收件箱', 269, 161), t('删除', 527, 162), t('上周', 237, 200)]
    for i, (sender, when, subject, preview) in enumerate(mails):
        y = 230 + 84 * i
        items += [t(sender, 295, y), t(when, 860, y + 1, 37), t(subject, 269, y + 23), t(preview, 269, y + 45, 180)]
    return items


OLD = ('tester', '11/10', '测试邮件 1', '这是第 1 封测试邮件，请回复。')
NEW = ('tester', '23:05', '请确认评审会', '下周三的评审会能参加吗？')
ECHO = ('tester', '23:06', '回复：请确认评审会', '可以参加')


class TestInbox(unittest.TestCase):
    def test_rows_are_split_per_mail(self):
        rows = inbox_rows(inbox(NEW, OLD))
        self.assertEqual([(r['sender'], r['time'], r['subject'], r['preview']) for r in rows], [NEW, OLD])

    def test_only_todays_whitelisted_non_reply_mail_is_wanted(self):
        row = dict(zip(('sender', 'time', 'subject', 'preview'), NEW))
        self.assertTrue(wanted(row, [ME]))
        self.assertFalse(wanted({**row, 'sender': 'stranger'}, [ME]))
        self.assertFalse(wanted({**row, 'time': '11/10'}, [ME]))
        for when in ('刚刚', '6分钟前', '2小时前', '9:05'):
            self.assertTrue(wanted({**row, 'time': when}, [ME]), when)
        self.assertFalse(wanted({**row, 'subject': '回复：请确认评审会'}, [ME]))   # 自己回出去又落进收件箱的


class TestPoll(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = ReplyStore(Path(self.tmp.name) / 'reply.db', mode='send')
        self.logs = []
        self.watcher = MailWatcher(9, self.store, [ME], log=self.logs.append)

    def tearDown(self):
        self.tmp.cleanup()

    def poll(self, page, iconic=False):
        self.watcher.next_poll = 0
        with patch.object(mail_flow, 'read_page', return_value=page), \
             patch.object(mail_flow.user32, 'IsIconic', return_value=iconic):
            return self.watcher.poll()

    def test_baseline_then_new_mail_is_queued_once(self):
        self.assertEqual(self.poll(inbox(OLD)), 0)                   # 启动时已有的只作基线
        self.assertEqual(self.poll(inbox(NEW, OLD)), 1)
        self.assertEqual(self.poll(inbox(NEW, OLD)), 0)              # 同一封不重复登记
        moved = ('tester', '7分钟前', NEW[2], NEW[3])
        self.assertEqual(self.poll(inbox(moved, OLD)), 0)            # 相对时间变了也还是同一封
        self.assertEqual(self.poll(inbox(ECHO, NEW, OLD)), 0)        # 自己的回复不回
        turn = self.store.pending(MAIL)
        self.assertEqual(turn['incoming'][0]['subject'], '请确认评审会')
        self.assertEqual(self.logs, ['邮箱已接入：收件箱现有 1 封只作基线', '收到 1 封新邮件'])

    def test_not_on_inbox_or_minimized_reads_nothing(self):
        self.poll(inbox(OLD))
        self.assertEqual(self.poll([t('写信', 109, 156)]), 0)       # 人打开了一封信：先不登记
        self.assertEqual(self.poll(inbox(NEW, OLD), iconic=True), 0)
        self.assertIsNone(self.store.pending(MAIL))
        self.assertEqual(self.poll(inbox(NEW, OLD)), 1)


class TestRefresh(unittest.TestCase):
    def test_title_doorbell_refreshes_inbox_then_finds_new_mail(self):
        """列表不会自己刷新，标题变成"收到 N 封新邮件"时点一下「收件箱」再读。"""
        with TemporaryDirectory() as tmp:
            store = ReplyStore(Path(tmp) / 'reply.db', mode='send')
            watcher = MailWatcher(9, store, [ME], log=lambda line: None)
            pages = [inbox(OLD), inbox(OLD), inbox(NEW, OLD)]
            title = Mock(side_effect=['QQ邮箱 - Google Chrome', '收到 1 封新邮件 - Google Chrome', '收到 1 封新邮件 - Google Chrome'])
            watcher._front = lambda: None
            watcher._folder = Mock(return_value=inbox(NEW, OLD))
            with patch.object(mail_flow, 'read_page', side_effect=pages),                  patch.object(mail_flow, '_text', side_effect=lambda fn, h: title()),                  patch.object(mail_flow.user32, 'IsIconic', return_value=False):
                watcher.poll()                              # 基线
                self.assertFalse(watcher.has_work())
                watcher.next_poll = 0
                self.assertEqual(watcher.poll(), 0)         # 标题响了，列表还是旧的
                self.assertTrue(watcher.has_work())
                watcher.process()                           # 点「收件箱」刷新，再读一次
            watcher._folder.assert_called_once_with('收件箱')
            self.assertEqual(store.pending(MAIL)['incoming'][0]['subject'], NEW[2])
            self.assertFalse(watcher.refresh)


class TestProcess(unittest.TestCase):
    """把网页换成假的，只看程序在什么条件下点发送、账本怎么记。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = ReplyStore(Path(self.tmp.name) / 'reply.db', mode='send')
        row = dict(zip(('sender', 'time', 'subject', 'preview'), NEW))
        self.store.enqueue(MAIL, [{'who': 'them', 'kind': 'text', 'text': '请确认评审会\n下周三的评审会能参加吗？',
                                   'at': datetime(2026, 11, 19, 23, 5), **row}])
        self.watcher = MailWatcher(9, self.store, [ME], log=lambda line: None)
        self.taps = []
        self.watcher._tap = lambda box: self.taps.append(box)
        self.watcher._front = lambda: None
        self.watcher._folder = lambda name: inbox(NEW, OLD) if name == '收件箱' else [t('已发送', 269, 161)]
        reply_page = [{'text': '主题', 'box': [300, 250, 800, 270], 'kind': 'EditControl', 'value': '回复：请确认评审会'},
                      {'text': '输入正文', 'box': [280, 300, 900, 600], 'kind': 'GroupControl', 'value': ''},
                      t('发送', 360, 170, 30), t('回复', 300, 170, 30)]
        self.page = reply_page
        self.result = {'status': 'send', 'text': '这个我晚点确认下回你', 'tier': 'free', 'reason': '明确事项交本人处理',
                       'issues': ['确认评审会能否参加']}

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, ready=(True, ''), judged=('ok', '')):
        with patch.object(mail_flow.reply_turn, 'decide', return_value=self.result), \
             patch.object(mail_flow, 'wait_page', side_effect=lambda h, pred, timeout=12: (pred(self.page) or True, self.page)), \
             patch.object(mail_flow, 'read_page', return_value=self.page), \
             patch.object(mail_flow, 'paste_text'), patch.object(mail_flow.time, 'sleep'), \
             patch('agent.core.mail_nodes.send_ready', return_value=ready), \
             patch.object(mail_flow, 'top_subjects', return_value=['回复：测试邮件 3']), \
             patch.object(mail_flow, 'judge_replied', return_value=judged), \
             patch.object(mail_flow, 'body_text', return_value='这个我晚点确认下回你'):
            self.watcher.process()

    def state(self):
        with self.store.transaction() as db:
            return db.execute('SELECT state FROM turn').fetchone()['state']

    def test_sent_after_both_checks_and_issue_goes_to_tray(self):
        self.run_once()
        self.assertEqual(self.state(), 'sent')
        self.assertEqual([i['text'] for i in self.store.open_issues(MAIL)], ['确认评审会能否参加'])
        self.assertIn([360, 170, 390, 186], self.taps)       # 点了「发送」

    def test_failed_pre_send_check_never_clicks_send(self):
        self.run_once(ready=(False, '收件人不是测试号'))
        self.assertEqual(self.state(), 'queued')             # 留着下一轮重试，第二次还不行转人工
        self.assertNotIn([360, 170, 390, 186], self.taps)    # 没点「发送」

    def test_unclear_send_result_goes_to_review_and_stops_mail(self):
        self.run_once(judged=('unknown', '「已发送」最上面不是正好新插进这一封'))
        self.assertEqual(self.state(), 'review')
        self.assertFalse(self.store.can_reply(MAIL))         # 说不清发没发：不再自动回，等人在托盘里核对


class TestMailTurn(unittest.TestCase):
    def listener(self):
        listener = wechat_reply_flow.Listener.__new__(wechat_reply_flow.Listener)
        listener.hwnd, listener.mail, listener.waiting, listener.log = 7, Mock(hwnd=9), False, lambda line: None
        return listener

    def test_screen_still_ours_only_if_nobody_touched_it_since(self):
        """11/19 真机：人在邮箱窗口里写信，停 3 秒就被程序抢去回微信。只有程序用完后没人碰过键鼠才算"还在程序手里"。"""
        listener = self.listener()
        fg = Mock(return_value=7)
        with patch.object(wechat_reply_flow.ctypes.windll.user32, 'GetForegroundWindow', fg),              patch.object(wechat_reply_flow.time, 'monotonic', return_value=100.0):
            listener.handover = (7, 95.0)                       # 5 秒前程序用完，停在 7 号窗口
            with patch.object(wechat_reply_flow, 'idle_seconds', return_value=5.2):
                self.assertTrue(listener._mail_turn())          # 之后没人动过：直接接着用
            with patch.object(wechat_reply_flow, 'idle_seconds', return_value=2):
                self.assertFalse(listener._mail_turn())         # 程序用完后有人动过键鼠：等满 60 秒
            fg.return_value = 9
            with patch.object(wechat_reply_flow, 'idle_seconds', return_value=5.2):
                self.assertFalse(listener._mail_turn())         # 前台换了窗口（人点过）：也等
            with patch.object(wechat_reply_flow, 'idle_seconds', return_value=61):
                self.assertTrue(listener._mail_turn())
            listener.handover = None
            with patch.object(wechat_reply_flow, 'idle_seconds', return_value=5):
                self.assertFalse(listener._mail_turn())


if __name__ == '__main__':
    unittest.main()
