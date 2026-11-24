"""从主窗口入口做离屏回归，使用临时账本和假进程。"""

from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from agent import desktop
from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from agent.computer.runner import ComputerRunner
from agent.demos.test_computer_session import ListenerStub, ProcessStub, WorkerStub
from agent.tools import history
from agent.tools.reply_store import ReplyStore
from agent.ui import main_window
from agent.ui.computer_page import image_rect, marker_position


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.db = self.path / 'reply.db'
        self.store = ReplyStore(self.db, mode='send')
        self.listener, self.worker = ListenerStub(False), WorkerStub()
        self.allowed = self.mock(main_window.trigger, 'load_allowed_mail', return_value=['test@example.test'])
        self.mock(main_window.trigger, 'history_folder', return_value=self.path)
        self.read = self.mock(history, 'handoff_details', return_value=None)
        self.window = self.make_window(self.worker)

    def make_window(self, worker):
        """为指定 worker 创建离屏主窗口，注册窗口清理。"""
        window = desktop.MainWindow([('测试联系人', False), ('模拟对象', True)], self.app, self.db,
                                    listener=self.listener, worker=worker, show_tray=False)
        self.addCleanup(self.clean_window, window)
        return window

    def mock(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def clean_window(self, window):
        """关闭指定测试窗口及其效果层。"""
        window.action_overlay.finish()
        window.timer.stop()
        window.hide()
        window.deleteLater()
        self.app.processEvents()

    def make_runner_window(self):
        """用临时目录和假进程创建走完整 Runner 路径的主窗口。"""
        env = patch.dict(os.environ, {'LOCALAPPDATA': str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        config = self.path / 'computer.json'
        config.write_text(json.dumps({'computer': dict(
            python=sys.executable, base_url='http://unused', model='fake', api_key='EMPTY',
            language='zh', max_loop=25, loop_interval_ms=0, pad_top=0, max_pixels=2352, scroll_clicks=4,
        )}), encoding='utf-8')
        runner = ComputerRunner(config_path=config, process_factory=ProcessStub)
        return self.make_window(runner), runner

    def test_navigation_tray_and_window_close(self):
        w = self.window
        w.open_window()
        for i, name in enumerate(('wechatPage', 'mailPage', 'computerPage')):
            w.navigation.setCurrentRow(i)
            self.assertEqual(w.pages.currentWidget().objectName(), name)
        self.assertEqual(w.timer.interval(), 900)
        self.assertFalse(w.tray.isVisible())
        for reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            w.close()
            self.assertFalse(w.isVisible())
            w.on_tray(reason)
            self.assertTrue(w.isVisible())
        self.assertFalse(w.computer_stop.isEnabled())
        self.assertIn('0 项待处理', w.tray.toolTip())

    def test_mail_option_listener_and_original_buttons(self):
        w = self.window
        opened = self.mock(main_window, 'open_mail_window', return_value='已打开邮箱')
        settings = self.mock(main_window, 'ModelDialog')
        self.assertTrue(w.mail.isChecked())
        self.assertTrue(w.mail.isEnabled())
        w.mail_window.click()
        opened.assert_called_once_with()
        self.assertEqual(w.mail_feedback.text(), '已打开邮箱')
        w.settings_button.click()
        settings.return_value.exec.assert_called_once_with()
        w.listen_action.trigger()
        self.assertEqual(self.listener.starts, [['--mail']])
        self.assertEqual(w.listen_action.text(), '停止自动回复')
        self.assertFalse(w.mail.isEnabled())
        self.assertEqual(w.recent.text(), '最近动态')
        w.toggle.click()
        self.assertTrue(self.listener.stopping)
        self.assertFalse(w.toggle.isEnabled())
        self.listener.exit()
        w.mail.setChecked(False)
        w.toggle.click()
        self.assertEqual(self.listener.starts, [['--mail'], []])
        self.listener.exit()
        self.allowed.return_value = []
        w.show_runner()
        self.assertFalse(w.mail.isEnabled())

    def test_mail_review_and_wechat_readonly_draft_from_one_store(self):
        for title, status in ((main_window.MAIL, 'review'), ('测试联系人', 'preview')):
            turn = self.store.enqueue(title, [{'who': 'them', 'text': '原文', 'kind': 'text', 'at': None}])
            self.store.finish(turn, status, '等待核对', draft='测试草稿')
        w = self.window
        w.refresh()
        self.assertEqual((w.wechat_items.list.count(), w.mail_items.list.count()), (1, 1))
        self.assertIn('2 项待处理', w.tray.toolTip())
        self.assertIn('测试草稿', w.wechat_items.transcript.toPlainText())
        self.assertFalse(w.wechat_items.resume.isEnabled())
        w.wechat_items.restore()
        self.assertEqual(len(self.store.preview_items('测试联系人')), 1)
        self.assertIn('原文', w.mail_items.transcript.toPlainText())

        with patch.object(main_window, 'ReplyStore', side_effect=sqlite3.OperationalError):
            w.refresh()
            self.assertEqual(w.mail_items.list.count(), 1)
            self.assertFalse(w.mail_items.resume.isEnabled())
        w.refresh()
        self.assertTrue(w.mail_items.resume.isEnabled())
        w.mail_items.resume.click()
        self.assertEqual(self.store.review_items(main_window.MAIL), [])
        w.refresh()
        self.assertIn('1 项待处理', w.tray.toolTip())

    def test_handoff_simulation_screenshot_and_stamp(self):
        info = dict(started=datetime.now(), reason='等待处理', request={'text': '触发词'},
                    source='ocr', aligned=False, followups=[{'text': '后续'}], stamp='版本')
        png = self.path / 'synthetic.png'
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        self.assertTrue(image.save(str(png)))
        info['preview'] = png.read_bytes()
        expected_marker = history.handoff_path(self.path, '模拟对象')
        self.read.side_effect = lambda marker, _: info if marker == expected_marker else None
        w = self.window
        w.refresh()
        view = w.wechat_items
        self.assertIn('（模拟）', view.title.text())
        self.assertIn('触发词', view.transcript.toPlainText())
        self.assertIn('微信截图', view.transcript.toPlainText())
        self.assertIn('无法对齐', view.transcript.toPlainText())
        marker = view.current
        self.read.side_effect = OSError
        w.refresh()
        self.assertEqual(view.current, marker)
        self.assertFalse(view.resume.isEnabled())
        self.read.side_effect = lambda key, _: info if key == marker else None
        w.refresh()
        resume = self.mock(history, 'resume_handoff')
        view.resume.click()
        resume.assert_called_once_with(marker, main_window.reply.loan_request, expected='版本')

    def test_result_reopens_last_step_then_user_resumes_once(self):
        w = self.window
        w.toggle.click()
        w.computer_action.trigger()
        page = w.computer_page
        page.task.setText('测试任务')
        page.start_button.click()
        self.assertFalse(w.isVisible())
        self.assertFalse(w.listen_action.isEnabled())
        self.assertFalse(w.mail.isEnabled())
        self.assertTrue(w.computer_stop.isEnabled())
        w.on_tray(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertFalse(w.isVisible())
        self.listener.exit()
        self.worker.start.assert_called_once_with('测试任务')
        self.assertTrue(w.action_overlay.isVisible())
        for n in range(1, 26):
            self.worker.event.emit(dict(event='executed', n=n, thought='测试', action='click',
                                        screen=[1920, 1080], point=[960, 540], seconds=1))
        self.assertIn('第 25 步', w.tray.toolTip())
        self.worker.finished.emit('DONE', '')
        self.assertFalse(w.action_overlay.isVisible())
        self.assertTrue(w.isVisible())
        self.assertIs(w.pages.currentWidget(), page)
        self.assertEqual(page.list.currentRow(), 24)
        self.assertEqual(page.preview.click, [960, 540])
        self.assertIn('任务用时', page.status.text())
        self.assertIn('等监听退出', page.status.text())
        self.assertEqual(self.listener.starts, [['--mail']])
        page.restore_button.click()
        page.restore_button.click()
        self.assertEqual(self.listener.starts, [['--mail'], ['--mail']])

    def test_image_marker_uses_drawn_rect_and_original_screen(self):
        rect = image_rect((1920, 1080), (10, 20, 400, 300))
        self.assertEqual(rect, (10, 57.5, 400, 225))
        self.assertEqual(marker_position((960, 540), (1920, 1080), rect), (210, 170))
        self.assertIsNone(marker_position((-1, 0), (1920, 1080), rect))

    def test_quit_orders_worker_before_listener(self):
        w = self.window
        app_quit = self.mock(w, 'app', quit=Mock())
        w.start_computer('测试任务')
        self.assertTrue(w.action_overlay.isVisible())
        w.menu.actions()[-1].trigger()
        self.assertFalse(w.action_overlay.isVisible())
        self.worker.stop.assert_called_once_with()
        app_quit.quit.assert_not_called()
        self.worker.finished.emit('STOPPED', '')
        app_quit.quit.assert_called_once_with()
        self.assertFalse(w.action_overlay.isVisible())

    def test_waiting_cancellation_never_displays_overlay(self):
        """等待监听退出时取消，效果始终隐藏且 worker 未启动。"""
        self.listener.start(['--mail'])
        w = self.window
        w.start_computer('测试任务')
        self.assertFalse(w.action_overlay.isVisible())
        w.session.stop()
        self.assertFalse(w.action_overlay.isVisible())
        self.listener.exit()
        self.assertFalse(w.action_overlay.isVisible())
        self.worker.start.assert_not_called()

    def test_process_exit_paths_hide_overlay_and_stop_animation(self):
        """走 Runner 的停止、强杀、崩溃、启动失败和配置错误路径。"""
        w, runner = self.make_runner_window()
        for reason in ('stop', 'kill', 'crash', 'start_error', 'config_error'):
            with self.subTest(reason=reason):
                if reason == 'config_error':
                    runner.config_path.unlink()
                w.start_computer('测试任务')
                if reason != 'config_error':
                    self.assertTrue(w.action_overlay.isVisible())
                    process = runner.process
                    runner.event.emit(dict(event='executing', n=1, kind='click', target=[40, 40]))
                    self.assertTrue(w.action_overlay.timer.isActive())
                if reason in ('stop', 'kill'):
                    w.session.stop()
                    self.assertFalse(w.action_overlay.isVisible())
                    if reason == 'kill':
                        runner.stop_timer.timeout.emit()
                        runner.kill_timer.timeout.emit()
                        process.kill.assert_called_once_with()
                    process.finished.emit(0 if reason == 'stop' else 1, QProcess.ExitStatus.NormalExit)
                elif reason == 'crash':
                    process.finished.emit(9, QProcess.ExitStatus.CrashExit)
                elif reason == 'start_error':
                    process.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
                self.assertEqual(w.session.state, 'idle')
                self.assertFalse(w.action_overlay.isVisible())
                self.assertFalse(w.action_overlay.timer.isActive())

    def test_capture_writes_ack_after_hiding_and_restores_only_when_running(self):
        """从进程输出跑一遍握手，验证步骤文件、回看隔离和迟到事件。"""
        w, runner = self.make_runner_window()
        w.start_computer('测试任务')
        process = runner.process
        self.assertIn('--overlay-sync', process.start.call_args.args[1])
        process.data = b'{"event":"capturing","n":1}\n'
        process.readyReadStandardOutput.emit()
        self.assertFalse(w.action_overlay.isVisible())
        ack = runner.run_dir / 'overlay-hidden-1'
        self.assertFalse(ack.exists())
        for _ in range(50):
            if ack.is_file():
                break
            QTest.qWait(10)
        self.assertTrue(ack.is_file())
        self.assertEqual(w.computer_page.list.count(), 0)
        process.data = b'{"event":"captured","n":1,"hidden":true}\n'
        process.readyReadStandardOutput.emit()
        self.assertTrue(w.action_overlay.isVisible())

        w.session.stop()
        process.data = b'{"event":"capturing","n":2}\n{"event":"captured","n":2,"hidden":true}\n'
        process.readyReadStandardOutput.emit()
        self.assertTrue((runner.run_dir / 'overlay-hidden-2').is_file())
        self.assertFalse(w.action_overlay.isVisible())
        process.finished.emit(0, QProcess.ExitStatus.NormalExit)


if __name__ == '__main__':
    unittest.main()
