"""书房场景和镜头的离屏测试：曲线端点、模式切换、房间里点不到界面、拉满后不缩放。"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from agent import desktop  # noqa: F401  让 PySide6 的本地路径先就位
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from agent.demos.test_computer_session import ListenerStub, WorkerStub
from agent.ui import main_window, storybook


class CurveTests(unittest.TestCase):
    def test_ease_endpoints_and_symmetry(self):
        self.assertEqual(storybook.camera_ease(0), 0)
        self.assertEqual(storybook.camera_ease(1), 1)
        self.assertAlmostEqual(storybook.camera_ease(.5), .5, places=3)
        self.assertAlmostEqual(storybook.camera_ease(.2) + storybook.camera_ease(.8), 1, places=3)

    def test_motion_ends_in_place(self):
        start, end = storybook.motion_at(0, True), storybook.motion_at(10, True)
        self.assertEqual((start['portion'], start['fade'], start['active']), (0, 0, False))
        self.assertEqual((end['portion'], end['fade'], end['active']), (1, 1, False))
        back = storybook.motion_at(10, False)
        self.assertEqual((back['portion'], back['fade']), (0, 0))

    def test_reduced_motion_is_a_short_fade(self):
        middle = storybook.motion_at(storybook.FADE / 2, True, cross=True)
        self.assertTrue(middle['cross'])
        self.assertAlmostEqual(middle['fade'], .5)

    def test_spring_settles(self):
        self.assertAlmostEqual(storybook.spring(0), 1)
        self.assertEqual(storybook.spring(1), 0)


class RoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.ui = QWidget()
        self.clicked = []
        button = QPushButton('界面按钮', self.ui)
        QVBoxLayout(self.ui).addWidget(button)
        button.clicked.connect(lambda: self.clicked.append(True))
        self.button = button
        self.view = storybook.RoomView(self.ui, reduced=lambda: False)
        self.addCleanup(self.view.deleteLater)
        self.view.resize(1100, 650)
        self.view.show()
        self.app.processEvents()

    def click_button(self):
        center = self.button.mapTo(self.ui, self.button.rect().center())
        pos = self.view.mapFromScene(self.view.proxy.mapToScene(QPointF(center)))
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        self.app.processEvents()

    def test_room_blocks_ui_and_screen_is_one_to_one(self):
        self.assertEqual(self.view.mode, 'room')
        self.view.show_screen(animated=False)
        self.app.processEvents()
        self.assertEqual(self.view.mode, 'screen')
        transform = self.view.proxy.sceneTransform()
        self.assertAlmostEqual(transform.m11(), 1, places=6)
        self.assertAlmostEqual(transform.m22(), 1, places=6)
        self.click_button()
        self.assertEqual(self.clicked, [True])
        self.view.show_room(animated=False)
        self.app.processEvents()
        self.assertEqual(self.view.mode, 'room')
        self.click_button()
        self.assertEqual(self.clicked, [True])

    def test_click_on_monitor_zooms_in(self):
        monitor = self.view.room.transform().mapRect(storybook.MONITOR).center()
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=monitor.toPoint())
        QTest.qWait(int((storybook.PREP + storybook.ZOOM_IN) * 1000) + 300)
        self.assertEqual(self.view.mode, 'screen')

    def test_drag_springs_back(self):
        start = self.view.viewport().rect().bottomLeft() + QPointF(60, -60).toPoint()
        QTest.mousePress(self.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.view.viewport(), start + QPointF(200, 0).toPoint())
        self.assertGreater(abs(self.view.offset.x()), 1)
        QTest.mouseRelease(self.view.viewport(), Qt.MouseButton.LeftButton, pos=start + QPointF(200, 0).toPoint())
        QTest.qWait(1000)
        self.assertTrue(self.view.offset.isNull())
        self.assertEqual(self.view.mode, 'room')


class WindowRoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tray_computer_skips_room(self):
        with patch.object(main_window.trigger, 'load_allowed_mail', return_value=[]), \
             patch.object(main_window.history, 'handoff_details', return_value=None):
            window = desktop.MainWindow([('测试联系人', False)], self.app, None,
                                        listener=ListenerStub(False), worker=WorkerStub(), show_tray=False)
            self.addCleanup(window.action_overlay.finish)
            window.open_window()
            self.assertEqual(window.room.mode, 'room')
            window.open_computer()
            self.assertEqual(window.room.mode, 'screen')
            self.assertEqual(window.pages.currentWidget().objectName(), 'computerPage')
            window.room_button.click()
            QTest.qWait(int(storybook.ZOOM_OUT * 1000) + 300)
            self.assertEqual(window.room.mode, 'room')
            window.hide()


if __name__ == '__main__':
    unittest.main()
