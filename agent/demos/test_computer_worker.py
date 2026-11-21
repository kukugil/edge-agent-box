"""通过假截图、模型和输入设备验证 worker。"""

import base64
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call

from PIL import Image

from agent.computer import uitars, worker


class Timeout(ConnectionError):
    pass


class HttpError(Exception):
    status_code = 400


class WorkerTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.shots = Path(folder.name)
        self.config = dict(language='zh', model='dummy', max_loop=25, pad_top=0,
                           max_pixels=uitars.MAX_PIXELS, loop_interval_ms=1000)
        self.image = Image.new('RGB', (112, 84), 'black')
        self.capture = Mock(return_value=(self.image, (112, 84)))
        self.predict = Mock(return_value='wait()')
        self.perform = Mock(side_effect=lambda action, point, end: point)
        self.check = Mock()
        self.wait = Mock()
        self.events = []

    def run_task(self):
        return worker.run_loop('测试', self.config, self.shots, self.capture, self.predict, self.perform,
                               lambda event, **kw: self.events.append((event, kw)), self.check, self.wait)

    def test_all_25_rounds_including_empty_and_unknown_actions(self):
        self.predict.side_effect = ['无法解析', 'future_action()'] + ['wait()'] * 23
        self.assertEqual(self.run_task(), ('STEP_LIMIT', ''))
        self.assertEqual(self.capture.call_count, 25)
        self.assertEqual(self.wait.call_args_list, [call(1)] * 25)
        completed = [fields for kind, fields in self.events if kind == 'executed']
        self.assertEqual([step['n'] for step in completed], list(range(1, 26)))
        self.assertTrue(all(Path(step['shot']).is_file() and step['seconds'] >= 0 for step in completed))
        self.assertEqual([step['action'] for step in completed[:2]], ['空动作', 'future_action'])
        seventh = self.predict.call_args_list[6].args[0]
        self.assertEqual(len([m for m in seventh if m['role'] == 'assistant']), 6)
        self.assertEqual(len([m for m in seventh if isinstance(m['content'], list)]), 5)

    def test_sequential_actions_and_terminal_short_circuit(self):
        self.predict.return_value = 'Thought: 继续\nAction: click(point=(56,42))\n\nwait()\n\nfinished()\n\nwait()'
        self.assertEqual(self.run_task(), ('DONE', ''))
        self.assertEqual([args.args[0].name for args in self.perform.call_args_list], ['click', 'wait'])
        self.assertEqual(self.events[-1][1]['point'], self.perform.call_args_list[0].args[1])
        self.assertIn('finished', self.events[-1][1]['action'])
        self.wait.assert_not_called()

    def test_call_user_parse_failure_and_stop_after_action(self):
        self.predict.return_value = 'Thought: 请登录\nAction: call_user()'
        self.assertEqual(self.run_task(), ('CALL_USER', '请登录'))
        self.predict.return_value = None
        with self.assertRaisesRegex(ValueError, '解析失败'):
            self.run_task()
        self.assertEqual(self.events[-1][0], 'executed')
        flag = self.shots / 'stop'
        self.check.side_effect = lambda: worker.check_stop(flag)
        self.predict.return_value = 'wait()\n\nfinished()'
        self.perform.side_effect = lambda *_: flag.touch()
        with self.assertRaises(worker.Stopped):
            self.run_task()
        self.perform.assert_called_once()

    def test_execution_exception_includes_action(self):
        self.perform.side_effect = OSError
        with self.assertRaisesRegex(RuntimeError, '执行异常.*wait'):
            self.run_task()
        self.assertEqual(self.events[-1][0], 'executed')

    def test_image_limit_and_padding_leave_original_intact(self):
        message, size = worker.model_image(self.image, 56, uitars.MAX_PIXELS)
        self.assertEqual(size, (112, 140))
        data = message['content'][0]['image_url']['url'].split(',', 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(data))) as image:
            self.assertEqual(image.getpixel((0, 55)), (255, 255, 255))
            self.assertEqual(image.getpixel((0, 56)), (0, 0, 0))
        _, size = worker.model_image(self.image, 0, 2352)
        self.assertEqual(size, (56, 42))
        self.assertEqual(self.image.size, (112, 84))

    def test_only_connection_errors_retry(self):
        for error, count, label in ((ConnectionError, 3, 'Spark 连接中断'),
                                    (Timeout, 1, '超时'), (HttpError, 1, 'HTTP 错误')):
            with self.subTest(error=error):
                create, wait = Mock(side_effect=error), Mock()
                with self.assertRaisesRegex(RuntimeError, label):
                    worker.request_prediction(create, self.config, [], wait, self.check,
                                              (Timeout, ConnectionError, HttpError))
                self.assertEqual(create.call_count, count)
                self.assertEqual(wait.call_args_list, [call(3)] * (count - 1))

    def test_retry_success_and_thinking_toggle(self):
        create = Mock(side_effect=[ConnectionError(), SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content='finished()'))])])
        self.config['thinking'] = False
        self.assertEqual(worker.request_prediction(create, self.config, [], self.wait, self.check,
                                                  (Timeout, ConnectionError, HttpError)), 'finished()')
        self.wait.assert_called_once_with(3)
        self.assertNotIn('extra_body', create.call_args.kwargs)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.trace = Mock()
        self.trace.clipboard.paste.return_value = '原剪贴板'
        self.trace.gui.FAILSAFE = True
        self.trace.gui.KEYBOARD_KEYS = ('ctrl', 'v', 'win', 'a')

    def perform(self, name, params=None, point=None, end=None):
        return worker.execute(uitars.Action(name, params or {}), point, end, self.trace.gui,
                              self.trace.clipboard, self.trace.wait, self.trace.check, 4)

    def test_type_restore_before_enter(self):
        self.perform('type', {'content': '中文，"x"\n'})
        self.assertEqual(self.trace.mock_calls, [
            call.clipboard.paste(), call.clipboard.copy('中文，"x"'),
            call.gui.keyDown('ctrl', _pause=False), call.gui.keyDown('v', _pause=False),
            call.wait(0.05), call.gui.keyUp('v', _pause=False), call.gui.keyUp('ctrl', _pause=False),
            call.wait(0.05), call.clipboard.copy('原剪贴板'), call.check(),
            call.gui.keyDown('enter', _pause=False), call.gui.keyUp('enter', _pause=False),
        ])

    def test_type_recovers_clipboard_and_keys_on_stop_or_input_failure(self):
        for error in (worker.Stopped, OSError):
            with self.subTest(error=error):
                self.trace.reset_mock()
                self.trace.gui.keyDown.side_effect = [None, error]
                with self.assertRaises(error):
                    self.perform('type', {'content': '正文\n'})
                self.assertEqual(self.trace.gui.keyUp.call_args_list,
                                 [call('v', _pause=False), call('ctrl', _pause=False)])
                self.trace.clipboard.copy.assert_called_with('原剪贴板')
                self.assertEqual(self.trace.gui.keyDown.call_args_list,
                                 [call('ctrl', _pause=False), call('v', _pause=False)])

    def test_click_drag_scroll_hotkey_and_wait(self):
        for name, button, count in [('click', 'left', 1), ('left_double', 'left', 2),
                                    ('right_single', 'right', 1)]:
            self.trace.reset_mock()
            self.assertEqual(self.perform(name, point=(120, 50)), (120, 50))
            self.assertEqual(self.trace.mock_calls, [call.gui.moveTo(120, 50, _pause=False),
                             call.wait(0.1), call.gui.click(button=button, clicks=count, _pause=False)])
        self.trace.reset_mock()
        self.assertEqual(self.perform('drag', point=(0, 15), end=(15, 16)), (0, 15))
        self.trace.gui.mouseUp.assert_called_once_with(button='left', _pause=False)
        self.trace.reset_mock()
        self.perform('scroll', {'direction': 'down'}, (2, 3))
        self.perform('scroll', {'direction': 'right'}, (2, 3))
        self.trace.gui.scroll.assert_called_once_with(-4, _pause=False)
        self.perform('hotkey', {'hotkey': 'meta+A bogus'})
        self.assertEqual(self.trace.gui.keyDown.call_args_list, [call('win', _pause=False), call('a', _pause=False)])
        self.perform('wait')
        self.trace.wait.assert_called_once_with(5)


if __name__ == '__main__':
    unittest.main()
