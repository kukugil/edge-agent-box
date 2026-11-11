"""D13 离线回归：只用合成图和假工具，不访问桌面或发送内容。"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from agent.core import check as c, graph as g, trigger as tr, mail_check as mc, mail_nodes as mn
from agent.tools import journal, Shot
from agent.tools.unread import badge_rows, find_row


def item(text, x=820, y=400, score=0.99):
    """输入假文字、位置和置信度，输出 OCR 字块，供离线断言使用。"""
    return {'text': text, 'box': [x, y, x + 80, y + 20], 'score': score}


def fixture():
    """输入无，输出假任务、标题、气泡和空白图，每个用例独立持有数据。"""
    task = {'to': '测试好友A', 'msg': '测试回复1'}
    return task, [item(task['to'], 290, 45)], item(task['msg']), Image.new('RGB', (960, 800), 'white')


def state(task):
    """输入假任务，输出按过回车的状态，用来测试核对节点。"""
    s = g.new_state(task, 1)
    s.update(action={'type': 'key', 'key': 'enter'}, steps=[{'result': '已按回车', 'action': {}}])
    return s


class AnchorTests(unittest.TestCase):
    """每个方法均为合成输入到需求结果的断言。"""

    def test_height_800(self):
        """输入 800 高合成图，断言实测底边 645。"""
        self.assert_anchor(960, 800, 655, 645)

    def test_height_900(self):
        """输入 900 高合成图，断言实测底边 745。"""
        self.assert_anchor(1100, 900, 755, 745)

    def test_height_700(self):
        """输入 700 高合成图，断言实测底边 545。"""
        self.assert_anchor(960, 700, 555, 545)

    def assert_anchor(self, width, height, line, want):
        """输入尺寸、分隔线和人工预期，断言像素定位结果。"""
        image = Image.new('RGB', (width, height), 'white')
        image.paste((236, 236, 236), (275, line, width - 20, line + 1))
        self.assertEqual(c.layout(image), {'right': width, 'bottom': want})

    def test_anchor_shift(self):
        """输入偏离兜底位置的线，证明实际搜线而非只返回固定差值。"""
        self.assert_anchor(960, 800, 600, 590)

    def test_fallback(self):
        """输入无线合成图，输出高度兜底。"""
        self.assertEqual(c.chat_bottom(Image.new('RGB', (960, 900), 'white')), 745)

    def test_old_message(self):
        """输入旧同句且计数不增，输出不成功。"""
        task, head, bubble, _ = fixture()
        self.assertEqual(c.judge_sent(head + [bubble], task, 1)[0], c.UNKNOWN)

    def test_incoming_same(self):
        """输入对方同句，输出不成功。"""
        task, head, _, _ = fixture()
        self.assertEqual(c.judge_sent(head + [item(task['msg'], 320)], task, 0)[0], c.UNKNOWN)

    def test_delay_three_seconds(self):
        """输入前三秒无气泡、第三秒出现的假 OCR，输出最终成功。"""
        task, head, bubble, image = fixture()
        elapsed = []
        with patch.object(g, 'capture_window', return_value=Shot(image, 0, 0)), \
             patch.object(g, 'ocr', side_effect=[head, head, head, head + [bubble]]), \
             patch.object(g.time, 'sleep', side_effect=elapsed.append), \
             patch.object(g, 'read_focused_text', side_effect=AssertionError('成功不应读输入框')):
            result = g.check(state(task))
        self.assertEqual((result['outcome'], result['done'], elapsed), (c.OK, True, [1, 1, 1]))

    def test_readback_fail(self):
        """输入五次无新增、读回原句，输出确定失败且只读回一次。"""
        task, head, _, image = fixture()
        with patch.object(g, 'capture_window', return_value=Shot(image, 0, 0)), \
             patch.object(g, 'ocr', return_value=head), patch.object(g.time, 'sleep'), \
             patch.object(g, 'read_focused_text', return_value=task['msg']) as read:
            result = g.check(state(task))
        self.assertEqual((result['outcome'], result['done'], read.call_count), (c.FAIL, False, 1))

    def test_red_failure(self):
        """输入最下我方气泡左侧红色标记，输出不确定。"""
        task, head, bubble, image = fixture()
        image.paste((250, 50, 50), (790, 401, 801, 413))
        self.assertEqual(c.judge_sent(head + [bubble], task, 0, image=image)[0], c.UNKNOWN)

    def test_wrong_title_even_with_draft(self):
        """输入错误标题且仍有草稿，输出不确定，禁止按失败重试。"""
        task, _, _, _ = fixture()
        self.assertEqual(c.judge_sent([item('测试好友B', 290, 45)], task, 0, still_in_box=True)[0], c.UNKNOWN)

    def test_truncated_title(self):
        """输入两种省略号标题，均拒绝匹配。"""
        for name in ('测试好友A…', '测试好友A...'):
            self.assertFalse(c.title_matches([item(name, 290, 45)], name))

    def test_low_score(self):
        """输入标题低置信字块，拒绝匹配。"""
        self.assertFalse(c.title_matches([item('测试好友A', 290, 45, 0.89)], '测试好友A'))

    def test_truncated_row(self):
        """输入带省略号的列表标题，输出找不到行。"""
        with patch('agent.tools.unread.read_titles', return_value=['测试好友A…']):
            self.assertIsNone(find_row(Image.new('RGB', (960, 800)), '测试好友A…'))

    def test_isolated_badge(self):
        """输入隔离序号、带角标列表和 pending，输出不再挑该会话。"""
        names, _ = tr.pick_sessions([{'title': '测试好友A'}], ['测试好友A'], {'测试好友A': 0}, isolated={1})
        self.assertEqual(names, [])

    def test_serve_unsure_isolates(self):
        """输入 UNSURE 后持续带角标的假服务，断言仅执行一次任务。"""
        with patch.object(tr, 'WHITELIST', ['测试好友A']), patch.object(tr, 'bell', return_value=None), \
             patch.object(tr, 'scan', return_value=[{'title': '测试好友A'}]), \
             patch.object(tr, 'run_one', return_value=(tr.UNSURE, None)) as run, \
             patch.object(tr, 'is_front', return_value=True), \
             patch.object(tr, 'wait_for_change', side_effect=KeyboardInterrupt):
            result = tr.serve(1, 10)
        self.assertEqual((run.call_count, result['unsure']), (1, 1))

    def test_journal_restart_clear(self):
        """输入发送前账本，断言重启隔离；清账选项解除隔离且不访问应用。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            task = {'journal': path, 'id': 'test-1', 'who': 1, 'msg': '测试回复1'}
            journal.record(task, 'about_to_send')
            self.assertEqual(tr.startup_isolated(path), {1})
            record = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(set(record), {'time', 'id', 'who', 'hash', 'status'})
            self.assertEqual(len(record['hash']), 8)
            self.assertEqual(tr.startup_isolated(path, clear_journal=True), set())

    def test_journal_terminal_states(self):
        """输入未知、失败和成功记录，断言按任务保留未决项。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            task = {'journal': path, 'id': 'test-1', 'who': 1, 'msg': '测试回复1'}
            journal.record(task, 'unknown')
            journal.record({**task, 'id': 'test-2'}, 'ok')
            self.assertEqual(journal.unresolved(path), {1})
            journal.record(task, 'fail')
            self.assertEqual(journal.unresolved(path), set())

    def test_journal_before_enter(self):
        """输入许可通过的假 act，断言回车发生前记录已落盘。"""
        task, _, _, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            task.update(journal=Path(tmp) / 'journal.jsonl', id='test-1', who=1)
            s = state(task)
            s.update(allow=True, steps=[])
            def enter(key):
                """输入假按键，核对发前记录；不发真实按键，输出无。"""
                self.assertEqual(journal.unresolved(task['journal']), {1})
            with patch.object(g, 'activate', return_value=(True, '')), \
                 patch.object(g, 'send_permit', return_value=(True, '')), \
                 patch.object(g, 'press', side_effect=enter) as press, patch.object(g.time, 'sleep'):
                result = g.act(s)
            self.assertEqual(press.call_count, 1)
            self.assertEqual(result['steps'][-1]['result'], '已按回车')

    def test_run_one_fail_retry(self):
        """输入按回车后确定失败的图结果，输出 RETRY 并落盘 fail。"""
        task, _, _, _ = fixture()
        end = dict(done=False, outcome='fail', steps=[{'result': '已按回车'}])
        with tempfile.TemporaryDirectory() as tmp:
            task.update(journal=Path(tmp) / 'journal.jsonl', id='test-1', who=1)
            with patch.object(tr, 'prepare', return_value=True), patch.object(tr, 'open_chat', return_value='fake'), \
                 patch.object(g, 'build_graph') as build:
                build.return_value.invoke.return_value = end
                self.assertEqual(tr.run_one(1, task)[0], tr.RETRY)
            self.assertEqual(journal.unresolved(task['journal']), set())

    def test_missing_capture_unknown(self):
        """输入始终失败的截图，输出 UNKNOWN，允许重启账本保留隔离。"""
        task, _, _, _ = fixture()
        with patch.object(g, 'capture_window', return_value=None), patch.object(g.time, 'sleep'), \
             patch.object(g, 'read_focused_text', return_value=task['msg']):
            self.assertEqual(g.check(state(task))['outcome'], c.UNKNOWN)

    def test_journal_failure_blocks_enter(self):
        """输入落盘异常，输出异常向上传递且从未按回车。"""
        task, _, _, _ = fixture()
        s = state(task)
        s.update(allow=True)
        with patch.object(g, 'activate', return_value=(True, '')), \
             patch.object(g, 'send_permit', return_value=(True, '')), \
             patch.object(g.journal, 'record', side_effect=OSError('测试写入失败')), \
             patch.object(g, 'press') as press:
            with self.assertRaises(OSError):
                g.act(s)
        press.assert_not_called()

    def test_open_chat_dynamic_input(self):
        """输入 900 高假窗口，输出分隔线下 50 的假点击，未动真实鼠标。"""
        image = Image.new('RGB', (1100, 900), 'white')
        image.paste((236, 236, 236), (275, 755, 1080, 756))
        shot = Shot(image, 10, 20)
        with patch.object(tr, 'capture_window', return_value=shot), \
             patch.object(tr, 'ocr_crop', return_value=[item('测试好友A', 290, 45)]), \
             patch.object(tr, 'click') as click:
            tr.open_chat(1, '测试好友A')
        click.assert_called_once_with(687, 825)

    def test_layout_counts_tall_window(self):
        """输入 900 高窗口和底部气泡，断言 look 使用当前图布局。"""
        task, head, _, _ = fixture()
        image = Image.new('RGB', (1100, 900), 'white')
        s = g.new_state(task, 1)
        with patch.object(g, 'capture_window', return_value=Shot(image, 0, 0)), \
             patch.object(g, 'ocr', return_value=head + [item(task['msg'], 900, 700)]):
            self.assertEqual(g.look(s)['count_before'], 1)

    def test_below_right(self):
        """输入回复下方右侧墨迹，输出 NONE。"""
        self.assert_ink((820, 460, 840, 480), tr.NONE)

    def test_below_center(self):
        """输入回复下方居中系统提示墨迹，输出 NONE。"""
        self.assert_ink((600, 460, 630, 480), tr.NONE)

    def test_below_left(self):
        """输入回复下方左侧墨迹，输出 NEW。"""
        self.assert_ink((350, 460, 370, 480), tr.NEW)

    def test_animation_above(self):
        """输入回复上方两帧变化的动图墨迹，两帧都输出 NONE。"""
        self.assert_ink((350, 200, 380, 220), tr.NONE)
        self.assert_ink((360, 205, 390, 235), tr.NONE)

    def assert_ink(self, box, want):
        """输入墨迹位置和人工预期，断言回复后像素检查结果。"""
        task, head, bubble, image = fixture()
        image.paste((0, 0, 0), box)
        self.assertEqual(tr.new_after_reply(Shot(image, 0, 0), head + [bubble], task), want)

    def test_red_without_white(self):
        """输入角标位置纯红大块，输出无角标。"""
        image = Image.new('RGB', (960, 800), 'white')
        image.paste((250, 50, 50), (95, 100, 115, 120))
        self.assertEqual(badge_rows(image), [])

    def test_badge_digit_and_dot(self):
        """输入白字红底角标和无白字小红点，两者均保留。"""
        image = Image.new('RGB', (960, 800), 'white')
        image.paste((250, 50, 50), (95, 100, 115, 120))
        image.paste('white', (104, 104, 107, 115))
        image.paste((250, 50, 50), (100, 200, 109, 209))
        self.assertEqual(len(badge_rows(image)), 2)

    def test_mail_one(self):
        """输入已发送新增一行，输出 OK。"""
        self.assertEqual(mc.judge_replied([mc._ROW1], '回复：测试邮件 1', 0)[0], c.OK)

    def test_mail_two(self):
        """输入已发送新增两行，输出 UNKNOWN。"""
        self.assertEqual(mc.judge_replied([mc._ROW1, mc._ROW1B], '回复：测试邮件 1', 0)[0], c.UNKNOWN)

    def test_mail_dedup(self):
        """输入同一 y±3 重复控件，输出仅一封。"""
        duplicate = {**mc._ROW1, 'box': [494, 233, 600, 251]}
        self.assertEqual(mc.count_subject([mc._ROW1, duplicate], '回复：测试邮件 1'), 1)

    def test_mail_fail(self):
        """输入无新增且仍在原回信页，输出 FAIL。"""
        self.assertEqual(mc.judge_replied([], '回复：测试邮件 1', 0, True)[0], c.FAIL)

    def test_mail_node_outcome(self):
        """输入假邮件列表新增两行，断言节点写入不确定结果并结束。"""
        task = {'to': 'tester01@qq.com', 'subject': '测试邮件 1', 'msg': '测试回复1'}
        s = state(task)
        s.update(action={'type': 'done'}, allow=True, steps=[])
        with patch.object(mn, 'open_folder', return_value=[mc._ROW1, mc._ROW1B]), \
             patch.object(mn, 'read_page', return_value=[]):
            result = mn.check(s)
        self.assertEqual((result['outcome'], result['done']), (c.UNKNOWN, False))


if __name__ == '__main__':
    with contextlib.redirect_stdout(io.StringIO()):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(AnchorTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f'{result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun} 通过')
    raise SystemExit(not result.wasSuccessful())
