"""离线核对 S3 输出、动作标签和红圈坐标。"""

import json
import unittest

from agent.s3.protocol import EventLines, click_coordinates, describe_action, image_rect, marker_position


class TestEventLines(unittest.TestCase):
    def test_split_at_every_byte_including_chinese(self):
        expected = [{'event': 'start', 'task': '搜索“深圳湾”'}, {'event': 'thinking', 'n': 1},
                    {'event': 'executing', 'n': 1, 'click': [123, 456]},
                    {'event': 'executed', 'n': 1, 'seconds': 1.2},
                    {'event': 'done', 'result': 'DONE', 'detail': '', 'seconds': 8}]
        data = '\r\n'.join(json.dumps(item, ensure_ascii=False) for item in expected).encode()
        reader = EventLines()
        actual = []
        for byte in data:
            actual.extend(reader.feed(bytes([byte])))
        self.assertEqual(actual, expected[:-1])
        self.assertEqual(list(reader.feed(final=True)), expected[-1:])
        self.assertEqual(list(reader.feed(final=True)), [])

    def test_unrecognized_or_incomplete_lines_are_ignored(self):
        data = (b'dependency log\n[]\n\xff\n{"event":"other"}\n{"event":"thinking","n":true}\n'
                b'{"event":"executed","n":16}\n{"event":"done","result":"unknown"}\n{"event":')
        self.assertEqual(list(EventLines().feed(data, final=True)), [])


class TestActions(unittest.TestCase):
    def test_click_uses_first_literal_coordinate_pair(self):
        code = 'import pyautogui; pyautogui.click(123, -4, button="right"); pyautogui.click(10, 20)'
        self.assertEqual(click_coordinates(code), [123, -4])

    def test_type_with_target(self):
        code = ("import pyautogui;\ntry:\n    import pyperclip\nexcept ImportError:\n"
                "    import pyperclip\n\npyautogui.click(310, 220); pyperclip.copy('测试');")
        self.assertEqual(click_coordinates(code), [310, 220])

    def test_scroll_drag_and_other_actions_have_no_click(self):
        for code in ('pyautogui.moveTo(11, 22); pyautogui.vscroll(-3)',
                     'pyautogui.moveTo(11, 22); pyautogui.dragTo(33, 44)',
                     'pyautogui.write("pyautogui.click(11, 22)")',
                     'pyautogui.hotkey("ctrl", "v")', 'pyautogui.click(x, 22)',
                     'pyautogui.click()', 'DONE'):
            with self.subTest(code=code):
                self.assertIsNone(click_coordinates(code))

    def test_plan_action_labels(self):
        actions = {'click': '点击', 'type': '输入', 'hotkey': '快捷键', 'hold_and_press': '快捷键',
                   'scroll': '滚动', 'drag_and_drop': '拖拽', 'open': '打开应用',
                   'wait': '等待', 'done': '完成', 'fail': '未完成', 'next': '继续'}
        for action, label in actions.items():
            with self.subTest(action=action):
                self.assertEqual(describe_action(f'# agent.done()\nagent.{action}("测试")'), label)
        self.assertEqual(describe_action('print("agent.done()")'), '操作')


class TestMarker(unittest.TestCase):
    def test_letterbox_in_tall_region_with_offset(self):
        rect = image_rect((1920, 1080), (10, 20, 960, 640))
        self.assertEqual(rect, (10, 70, 960, 540))
        self.assertEqual(marker_position([960, 540], (1920, 1080), rect), (490, 340))

    def test_letterbox_in_wide_region_and_original_coordinate_space(self):
        rect = image_rect((2400, 1200), (0, 0, 1000, 300))
        self.assertEqual(rect, (200, 0, 600, 300))
        self.assertEqual(marker_position([3200, 800], (4800, 2400), rect), (600, 100))

    def test_missing_and_out_of_bounds_click(self):
        for click in (None, [-1, 0], [1920, 0], [0, 1080]):
            with self.subTest(click=click):
                self.assertIsNone(marker_position(click, (1920, 1080), (10, 70, 960, 540)))


if __name__ == '__main__':
    unittest.main()
