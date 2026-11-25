"""装饰的离屏验收，包括实际托盘页面、叶片统计和切页边界。"""

import math
import os
import unittest
from itertools import combinations
from random import Random
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from agent import desktop
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractButton, QGraphicsItem

from agent.demos.test_computer_session import ListenerStub, WorkerStub
from agent.ui import leaf_flight, main_window, paper, storybook
from agent.ui.ornaments import Leaves, RATIOS


class FlightTests(unittest.TestCase):
    def test_200_leaves_have_r22_flight_statistics(self):
        """按验收报告的可见倾角和横向速度换向口径检查 200 片。"""
        rng = Random(23)
        reversals = rolls = 0
        for _ in range(200):
            leaf = leaf_flight.make_leaf(rng, 0, 1280, 760, RATIOS)
            self.assertLessEqual(2.2, leaf.life)
            self.assertLessEqual(leaf.life, 3.5)
            self.assertGreaterEqual(leaf.start, 0)
            self.assertLessEqual(leaf.start, .4)
            frames = leaf.path[:int(leaf.life / leaf_flight.STEP) + 1]
            velocities = [frame[2] for frame in frames]
            reversals += sum(a * b < 0 for a, b in zip(velocities, velocities[1:])) >= 2
            visible = [roll for x, y, _, _, roll in frames
                       if -leaf.radius < leaf.x + x * leaf.scale < 1280 + leaf.radius
                       and -leaf.radius < leaf.y + y * leaf.scale < 760 + leaf.radius]
            rolls += max(visible) - min(visible) >= math.tau
            x, y, *_ = leaf_flight.point(leaf.path, leaf.life)
            self.assertTrue(leaf.x + leaf.scale * x <= -leaf.radius or
                            leaf.y + leaf.scale * y >= 760 + leaf.radius)
        self.assertGreaterEqual(reversals, 140)
        self.assertIn(rolls, range(20, 61))
        print(f'\n落叶统计 200 片：至少两次折返 {reversals}，可见时完整翻滚 {rolls}。', flush=True)

    def test_projection_changes_face_at_thinnest_point(self):
        """用 90° 两侧的同一姿态验证翻面和五档亮度索引。"""
        before = leaf_flight.projection(math.pi / 2 - .00001, .24, .7)
        after = leaf_flight.projection(math.pi / 2 + .00001, .24, .7)
        self.assertLess(0, before[0])
        self.assertLess(after[0], 0)
        self.assertLess(abs(before[0]) + abs(after[0]), .00003)
        self.assertEqual(before[3], after[3])
        self.assertEqual({leaf_flight.projection(i / 100, 0, -math.atan2(.53, .50))[3] for i in range(628)},
                         {0, 1, 2, 3, 4})


class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """复用离屏 QApplication，避免创建真实托盘。"""
        cls.app = QApplication.instance() or QApplication([])

    def check_spacing(self, flight):
        """独立以 2ms 采样，检查同时露出屏幕的叶心间隔。"""
        for first, second in combinations(flight, 2):
            begin = max(first.start, second.start)
            end = min(first.start + first.life, second.start + second.life)
            for step in range(max(0, math.ceil((end - begin) / .002))):
                now = begin + step * .002
                points = []
                for leaf in (first, second):
                    x, y, *_ = leaf_flight.point(leaf.path, now - leaf.start)
                    x, y = leaf.x + x * leaf.scale, leaf.y + y * leaf.scale
                    if -leaf.radius < x < 1280 + leaf.radius and -leaf.radius < y < 760 + leaf.radius:
                        points.append((x, y))
                if len(points) == 2:
                    self.assertGreaterEqual(math.dist(*points), 1280 * .12)

    def test_wind_keeps_visible_leaf_centers_apart(self):
        """首阵风和第二次补叶都应用真实避让。"""
        leaves = Leaves(None)
        leaves.setRect(QRectF(0, 0, 1280, 760))
        leaves.rng.seed(19)
        for now in (0, .6, 1.2):
            previous = leaves.flight[:]
            leaves.gust(now)
            for i in range(19):
                leaves.advance(now + .016 * i)
            if previous:
                self.assertLessEqual(len(leaves.flight) - len(previous), 3)
                self.assertTrue(all(leaf in leaves.flight for leaf in previous))
            else:
                self.assertIn(len(leaves.flight), range(1, 11))
            self.check_spacing(leaves.flight)

    def test_quick_turn_hard_cap_even_if_every_candidate_fits(self):
        """单独隔离补叶配额，确保在最易通过避让时也不会超过 14。"""
        leaves = Leaves(None)
        leaves.setRect(QRectF(0, 0, 1280, 760))
        leaves.rng.seed(20)
        with patch.object(leaf_flight, 'separated', return_value=True):
            for turn in range(7):
                now = turn * .18
                before = len(leaves.flight)
                leaves.gust(now)
                for i in range(6):
                    leaves.advance(now + .016 * i)
                self.assertLessEqual(len(leaves.flight), 14)
                if turn:
                    self.assertLessEqual(len(leaves.flight) - before, 3)
                else:
                    self.assertIn(len(leaves.flight), range(6, 11))
        self.assertEqual(len(leaves.flight), 14)

    def window(self, reduced=False):
        """打开真实 MainWindow，注入进程替身和减少动态效果设置。"""
        for module, name, value in ((main_window.trigger, 'load_allowed_mail', []),
                                    (main_window.history, 'handoff_details', None)):
            mock = patch.object(module, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        win = desktop.MainWindow([], self.app, listener=ListenerStub(False), worker=WorkerStub(), show_tray=False)
        self.addCleanup(self.dispose, win)
        win.room.reduced = lambda: reduced
        win.resize(1280, 760)
        win.open_window()
        win.room.show_screen(animated=False)
        self.wait_for(lambda: win.ornaments.root.isVisible())
        return win

    def wait_for(self, ready):
        """等待实际 Qt 布局事件完成，超时让测试失败。"""
        for _ in range(50):
            if ready():
                return
            QTest.qWait(10)
        self.assertTrue(ready())

    def dispose(self, window):
        """关闭假进程和窗口，及时派发 Qt 的延迟删除。"""
        window.hide()
        window.action_overlay.finish()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_vine_avoids_controls_on_all_pages_and_after_resize(self):
        """用实际窗口尺寸检查三条可见藤路及各片回弹后的外框。"""
        win = self.window(reduced=True)
        o = win.ornaments
        for size in ((1280, 760), (900, 580), (640, 460)):
            win.resize(*size)
            shapes = []
            for index in range(3):
                win.navigation.setCurrentRow(index)
                self.wait_for(lambda: o.geometry is not None and o.geometry[1] == index
                              and o.geometry[0] == win.room.sceneRect())
                o.advance(o.started + 1)
                self.assertGreaterEqual(len(o.vine.leaves), 6)
                self.assertLessEqual(len(o.vine.leaves), 10)
                visible_ink = o.vine.ink.path().intersected(o.vine.path())
                self.assertFalse(visible_ink.isEmpty())
                shapes.append(visible_ink)
                toolbar, _, heading, _ = o.anchors[index]
                buttons = [w for w in win.pages.currentWidget().findChildren(QAbstractButton) if w.isVisible()]
                for control in (toolbar, heading, win.room_button, *buttons):
                    protected = o.scene_rect(control)
                    self.assertFalse(visible_ink.intersects(protected), (size, index, control.objectName()))
                    for _, leaf in o.vine.leaves:
                        self.assertFalse(leaf.sceneBoundingRect().intersects(protected))
            self.assertEqual(len({path.elementCount() for path in shapes}), 3)

    def test_timer_stops_and_proxy_cache_releases_after_last_leaf(self):
        """验证真实计时器在切页后启动，并在最后一片离场时自动停。"""
        win = self.window()
        o = win.ornaments
        o.leaves.rng.seed(23)
        win.navigation.setCurrentRow(1)
        self.wait_for(lambda: o.geometry[1] == 1)
        self.assertTrue(o.timer.isActive())
        self.assertEqual(win.room.proxy.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache)
        QTest.qWait(400)
        self.assertTrue(o.leaves.flight)
        QTest.qWait(3650)
        self.assertFalse(o.leaves.flight)
        self.assertFalse(o.timer.isActive())
        self.assertFalse(o.layout_timer.isActive())
        self.assertEqual(win.room.proxy.cacheMode(), QGraphicsItem.CacheMode.NoCache)

    def test_reduced_motion_and_no_decor_in_travel_or_room(self):
        """减少动态效果时只淡入，退回房间时立刻隐藏所有场景装饰。"""
        win = self.window(reduced=True)
        o = win.ornaments
        win.navigation.setCurrentRow(1)
        self.wait_for(lambda: o.geometry[1] == 1)
        self.assertTrue(o.turn.isVisible())
        self.assertEqual(o.turn.slide, 0)
        self.assertFalse(o.leaves.flight or o.leaves.pending)
        o.advance(o.started + .08)
        self.assertGreater(o.vine.opacity(), 0)
        self.assertLess(o.vine.opacity(), 1)
        self.assertTrue(all(leaf.scale() == 1 for _, leaf in o.vine.leaves))
        o.advance(o.started + .4)
        self.assertFalse(o.timer.isActive())
        win.room.render_frame(storybook.motion_at(.1, False, cross=True))
        self.assertEqual(win.room.mode, 'travel')
        self.assertFalse(o.root.isVisible())
        win.room.show_room(animated=False)
        self.assertFalse(o.root.isVisible())
        self.assertFalse(o.turn.isVisible())

    def test_light_protects_controls_and_dark_text_has_contrast(self):
        """验证按钮受裁切保护，暗色正文在预览中最暗纸底也达到 4.5∶1。"""
        win = self.window()
        o = win.ornaments
        self.assertFalse(o.decor.light_clip.intersects(o.scene_rect(win.toggle)))
        nav = win.navigation
        item = QRectF(nav.visualItemRect(nav.item(0))).translated(o.scene_rect(nav.viewport()).topLeft())
        self.assertFalse(o.decor.light_clip.intersects(item.adjusted(1, 1, -1, -1)))
        for label in (win.wechat_items.title, win.wechat_items.reason):
            center = QPointF(label.fontMetrics().horizontalAdvance(label.text()) / 2, label.height() / 2)
            self.assertFalse(o.decor.light_clip.contains(o.scene_rect(label).topLeft() + center))

        def luminance(value):
            """把 sRGB 三通道转成 WCAG 相对亮度。"""
            color = QColor(value)
            rgb = (color.redF(), color.greenF(), color.blueF())
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055)**2.4 for v in rgb]
            return sum(c * v for c, v in zip((.2126, .7152, .0722), linear))

        self.assertGreaterEqual((luminance('#e1c7a9') + .05) / (luminance(paper.NIGHT) + .05), 4.5)


if __name__ == '__main__':
    unittest.main()
