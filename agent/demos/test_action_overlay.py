"""用离屏 Qt 验证演示窗口的状态、输入标志和短动画。"""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from agent.ui.action_overlay import ActionOverlay


class ActionOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """使用离屏平台创建或复用应用。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """为每个用例创建独立的效果层。"""
        self.overlay = ActionOverlay(self.app)
        self.addCleanup(self.overlay.deleteLater)
        self.addCleanup(self.overlay.finish)

    def test_fullscreen_tool_is_input_transparent_and_does_not_activate(self):
        """检查主屏范围、置顶、穿透、不激活和透明背景标志。"""
        overlay = self.overlay
        overlay.start()
        self.assertTrue(overlay.isVisible())
        self.assertEqual(overlay.geometry(), self.app.primaryScreen().geometry())
        for flag in (Qt.WindowType.Tool, Qt.WindowType.WindowStaysOnTopHint,
                     Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowTransparentForInput,
                     Qt.WindowType.WindowDoesNotAcceptFocus):
            self.assertEqual(overlay.windowFlags() & flag, flag)
        self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertFalse(overlay.timer.isActive())

    def test_phase_action_label_and_transient_animation(self):
        """思考和操作状态随事件变化，动作动画结束时停止计时。"""
        overlay = self.overlay
        overlay.start()
        overlay.apply_event(dict(event='thinking', n=3))
        self.assertEqual(overlay.status_text, '第 3 步 · 正在思考 · 托盘右键可停止')
        for kind, label, target, end, direction in (
                ('click', '点击', [50, 70], None, ''),
                ('left_double', '双击', [50, 70], None, ''),
                ('right_single', '右击', [50, 70], None, ''),
                ('drag', '拖动', [50, 70], [80, 100], ''),
                ('scroll', '滚动', [50, 70], None, 'up'),
                ('scroll', '滚动', None, None, 'down')):
            with self.subTest(kind=kind, direction=direction):
                overlay.apply_event(dict(event='executing', n=3, kind=kind,
                                         target=target, end=end, direction=direction))
                self.assertEqual(overlay.status_text, f'第 3 步 · 正在操作 · {label} · 托盘右键可停止')
                self.assertTrue(overlay.timer.isActive())
                self.assertEqual(overlay.animation[0], kind)
                self.assertFalse(overlay.grab().isNull())
        with patch.object(overlay, 'elapsed', Mock(**{'elapsed.return_value': 600})):
            overlay.timer.timeout.emit()
        self.assertFalse(overlay.timer.isActive())
        self.assertIsNone(overlay.animation)
        overlay.apply_event(dict(event='executing', n=3, kind='type', action='私密原文'))
        self.assertEqual(overlay.status_text, '第 3 步 · 正在操作 · 输入 · 托盘右键可停止')
        self.assertFalse(overlay.timer.isActive())

    def test_capture_visibility_and_finish_ignore_late_events(self):
        """截图握手只恢复对应步骤，完成后的迟到事件无法显示窗口。"""
        overlay = self.overlay
        overlay.start()
        overlay.apply_event(dict(event='executing', n=2, kind='click', target=[50, 70]))
        overlay.start()
        self.assertEqual(overlay.step, 2)
        self.assertTrue(overlay.timer.isActive())
        overlay.apply_event(dict(event='capturing', n=3))
        self.assertFalse(overlay.isVisible())
        self.assertFalse(overlay.timer.isActive())
        overlay.apply_event(dict(event='captured', n=2, hidden=True))
        self.assertFalse(overlay.isVisible())
        overlay.apply_event(dict(event='captured', n=3, hidden=False))
        self.assertTrue(overlay.isVisible())
        overlay.apply_event(dict(event='capturing', n=4))
        overlay.finish()
        snapshot = overlay.status_text
        overlay.finish()
        for event in (dict(event='captured', n=4, hidden=True), dict(event='thinking', n=9),
                      dict(event='executing', n=9, kind='click', target=[50, 70])):
            overlay.apply_event(event)
        self.assertFalse(overlay.isVisible())
        self.assertFalse(overlay.timer.isActive())
        self.assertEqual(overlay.status_text, snapshot)
        overlay.start()
        self.assertTrue(overlay.isVisible())
        self.assertEqual(overlay.step, 1)

    def test_unsupported_screens_and_screen_change_disable_effects(self):
        """多屏或缩放时不开效果，运行时屏幕变化后也不恢复。"""
        screen = self.app.primaryScreen()
        for screens in ([screen, screen], [Mock(**{'devicePixelRatio.return_value': 1.25})]):
            with self.subTest(screens=screens), patch.object(self.app, 'screens', return_value=screens):
                self.overlay.start()
                self.overlay.apply_event(dict(event='executing', n=1, kind='click', target=[50, 70]))
                self.assertFalse(self.overlay.isVisible())
                self.assertFalse(self.overlay.timer.isActive())
                self.overlay.finish()
        self.overlay.start()
        self.assertTrue(self.overlay.isVisible())
        screen.geometryChanged.emit(screen.geometry())
        self.overlay.apply_event(dict(event='capturing', n=2))
        self.overlay.apply_event(dict(event='captured', n=2, hidden=True))
        self.assertFalse(self.overlay.isVisible())


if __name__ == '__main__':
    unittest.main()
