"""会话列表被滚到下面时，等人停手再双击气泡拉回顶部（11/21 真机：滚到底后漏消息）。离线，不碰微信。

python -B -m unittest agent.demos.test_keep_top -v
"""

import unittest
from unittest.mock import patch

from agent.core import wechat_reply_flow as flow


class FakeAdapter:
    def __init__(self, at_top, back_ok=True):
        self.top, self.back_ok, self.backs = at_top, back_ok, 0

    def at_top(self):
        return self.top

    def back_to_top(self):
        self.backs += 1
        if self.back_ok:
            self.top = True
        return self.back_ok


class FakeHuman:
    ok = True

    def __init__(self, last):
        self.last = last


def listener(adapter, human_last, now=1000.0):
    obj = object.__new__(flow.Listener)
    obj.adapter, obj.hwnd = adapter, 1
    obj.next_top, obj.top_waiting = 0.0, False
    obj.human, obj.human_since = FakeHuman(human_last), 0.0
    obj.logs = []
    obj.log = obj.logs.append
    obj.handover = None
    return obj


class TestKeepTop(unittest.TestCase):
    def run_once(self, obj, front, now=1000.0, prepare=True):
        with patch.object(flow.time, 'monotonic', return_value=now), \
                patch.object(flow.trigger, 'prepare', return_value=prepare) as prep, \
                patch.object(flow.ctypes.windll.user32, 'GetForegroundWindow', return_value=1):
            obj._keep_top(front)
        return prep

    def test_at_top_does_nothing(self):
        adapter = FakeAdapter(True)
        self.run_once(listener(adapter, human_last=0), front=True)
        self.assertEqual(adapter.backs, 0)

    def test_unknown_does_nothing(self):
        adapter = FakeAdapter(None)
        self.run_once(listener(adapter, human_last=0), front=True)
        self.assertEqual(adapter.backs, 0)

    def test_waits_while_human_active_and_logs_once(self):
        adapter = FakeAdapter(False)
        obj = listener(adapter, human_last=999)          # 人 1 秒前还碰过键鼠
        self.run_once(obj, front=True, now=1000)
        self.run_once(obj, front=True, now=1006)         # 停手 7 秒，还不够 10 秒
        self.assertEqual(adapter.backs, 0)
        self.assertEqual(len(obj.logs), 1)

    def test_front_pulls_back_after_idle(self):
        adapter = FakeAdapter(False)
        obj = listener(adapter, human_last=985)          # 停手 15 秒
        prep = self.run_once(obj, front=True)
        self.assertEqual(adapter.backs, 1)
        prep.assert_not_called()                         # 已在前台，不用切
        self.assertIn('拉回顶部', obj.logs[-1])

    def test_covered_needs_long_idle_and_prepare(self):
        adapter = FakeAdapter(False)
        obj = listener(adapter, human_last=970)          # 停手 30 秒，不够 60
        self.run_once(obj, front=False)
        self.assertEqual(adapter.backs, 0)
        obj = listener(adapter, human_last=900)          # 停手 100 秒
        prep = self.run_once(obj, front=False)
        prep.assert_called_once()
        self.assertEqual(adapter.backs, 1)

    def test_failed_pull_backs_off(self):
        adapter = FakeAdapter(False, back_ok=False)
        obj = listener(adapter, human_last=0)
        self.run_once(obj, front=True, now=1000)
        self.run_once(obj, front=True, now=1010)         # 30 秒内不再试
        self.assertEqual(adapter.backs, 1)
        self.run_once(obj, front=True, now=1031)
        self.assertEqual(adapter.backs, 2)

    def test_throttled_between_checks(self):
        adapter = FakeAdapter(False)
        obj = listener(adapter, human_last=995)
        self.run_once(obj, front=True, now=1000)
        adapter.top = False
        obj.human.last = 0
        self.run_once(obj, front=True, now=1002)         # 5 秒内不重查
        self.assertEqual(adapter.backs, 0)


if __name__ == '__main__':
    unittest.main()
