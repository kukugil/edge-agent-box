"""用假监听和假 Qt 进程验证互斥、退出及停止回退。"""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from agent import desktop
from PySide6.QtCore import QEventLoop, QProcess, QTimer
from PySide6.QtWidgets import QApplication

from agent.computer.runner import ComputerRunner
from agent.computer.session import Session


class SignalStub:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)

    def emit(self, *args):
        for callback in self.callbacks[:]:
            callback(*args)


class ListenerStub:
    def __init__(self, running=True):
        self.changed = SignalStub()
        self.last_extra = ['--mail']
        self.starts = []
        self.stopping = False
        self.status = '监听中' if running else '未在监听'
        self.recent = ['最近动态']
        self.active = running
        self.then = None

    def running(self):
        return self.active

    def start(self, args):
        self.starts.append(list(args))
        self.active = True
        self.status = '监听中'
        self.changed.emit()

    def stop(self, then=None):
        self.then = then
        self.stopping = self.active
        self.changed.emit()
        if not self.active:
            self.exit()

    def exit(self):
        self.active = self.stopping = False
        self.status = '已停止'
        callback, self.then = self.then, None
        self.changed.emit()
        if callback is not None:
            callback()


class WorkerStub:
    def __init__(self):
        self.finished = SignalStub()
        self.event = SignalStub()
        self.start = Mock()
        self.stop = Mock()


class ProcessStub:
    def __init__(self, parent):
        self.readyReadStandardOutput = SignalStub()
        self.finished = SignalStub()
        self.errorOccurred = SignalStub()
        self.setProcessEnvironment = Mock()
        self.setWorkingDirectory = Mock()
        self.setStandardOutputFile = Mock()
        self.setStandardErrorFile = Mock()
        self.start = Mock()
        self.write = Mock()
        self.closeWriteChannel = Mock()
        self.kill = Mock()
        self.deleteLater = Mock()
        self.data = b''

    def readAllStandardOutput(self):
        data, self.data = self.data, b''
        return data

    def processId(self):
        return 123

    def state(self):
        return QProcess.ProcessState.Running


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.listener = ListenerStub()
        self.worker = WorkerStub()
        self.changed = Mock()
        self.completed = Mock()
        self.now = 0
        self.session = Session(self.listener, self.worker, self.changed, self.completed, lambda: self.now)

    def test_cancel_waiting_requires_manual_restore_with_snapshot(self):
        self.session.start('任务')
        self.listener.last_extra.clear()
        self.now = 3
        self.session.stop()
        self.session.restore()
        self.worker.start.assert_not_called()
        self.listener.exit()
        self.worker.start.assert_not_called()
        self.completed.assert_called_once_with('STOPPED', '已取消启动', 3, 0)
        self.assertEqual(self.listener.starts, [])
        self.session.restore()
        self.session.restore()
        self.assertEqual(self.listener.starts, [['--mail']])

    def test_worker_timing_and_no_automatic_restore(self):
        self.session.start('任务')
        self.now = 3
        self.listener.exit()
        self.worker.start.assert_called_once_with('任务')
        self.now = 12
        self.worker.finished.emit('DONE', '')
        self.completed.assert_called_once_with('DONE', '', 3, 9)
        self.assertEqual(self.listener.starts, [])
        self.session.restore()
        self.assertEqual(self.listener.starts, [['--mail']])

    def test_quit_running_waits_for_worker_and_never_restores(self):
        self.session.start('任务')
        self.listener.exit()
        then = Mock()
        self.session.quit(then)
        self.worker.stop.assert_called_once()
        then.assert_not_called()
        self.worker.finished.emit('STOPPED', '')
        then.assert_called_once_with()
        self.session.restore()
        self.completed.assert_not_called()
        self.assertEqual(self.listener.starts, [])

    def test_quit_waiting_cancels_start(self):
        self.session.start('任务')
        then = Mock()
        self.session.quit(then)
        then.assert_not_called()
        self.listener.exit()
        then.assert_called_once_with()
        self.worker.start.assert_not_called()
        self.assertEqual(self.listener.starts, [])


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = patch.dict(os.environ, {'LOCALAPPDATA': folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(folder.name)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'computer': dict(python=sys.executable, base_url='http://unused',
                          model='fake', api_key='EMPTY', language='zh', max_loop=25, loop_interval_ms=1000,
                          pad_top=0, max_pixels=12845056, scroll_clicks=4), 's3': dict(secret='ignored')}),
                          encoding='utf-8')
        self.runner = ComputerRunner(config_path=config, process_factory=ProcessStub)
        self.finished = Mock()
        self.runner.finished.connect(self.finished)
        self.runner.start('--任务')
        self.process = self.runner.process
        self.addCleanup(self.runner.stop_timer.stop)
        self.addCleanup(self.runner.kill_timer.stop)

    def test_command_uses_configured_python_and_run_directory(self):
        program, args = self.process.start.call_args.args
        self.assertEqual(Path(program), Path(sys.executable))
        self.assertTrue(any(arg.endswith('worker.py') for arg in args))
        self.assertIn('--task=--任务', args)
        self.assertNotIn('--overlay-sync', args)
        self.assertEqual(self.runner.run_dir.parent, self.root / 'edge-agent' / 'computer' / 'runs')
        self.assertNotIn('secret', json.loads(self.process.write.call_args.args[0]))

    def test_overlay_flag_and_ack_stay_with_current_run(self):
        """只为启用效果的进程传参数，迟到确认不能写入下一轮。"""
        old_dir = self.runner.run_dir
        self.process.finished.emit(0, QProcess.ExitStatus.NormalExit)
        self.runner.overlay_sync = True
        self.runner.start('有操作效果的任务')
        self.assertIn('--overlay-sync', self.runner.process.start.call_args.args[1])
        new_dir = self.runner.run_dir
        self.runner.acknowledge_overlay_hidden(1, old_dir)
        self.assertFalse((old_dir / 'overlay-hidden-1').exists())
        self.assertFalse((new_dir / 'overlay-hidden-1').exists())
        self.runner.acknowledge_overlay_hidden(1, new_dir)
        self.assertTrue((new_dir / 'overlay-hidden-1').is_file())

    def test_done_waits_for_exit_and_only_reports_once(self):
        self.process.data = b'{"event":"done","result":"DONE"}\n'
        self.process.readyReadStandardOutput.emit()
        self.finished.assert_not_called()
        self.process.finished.emit(0, QProcess.ExitStatus.NormalExit)
        self.process.finished.emit(0, QProcess.ExitStatus.NormalExit)
        self.finished.assert_called_once_with('DONE', '')

    def test_crash_and_failed_to_start(self):
        self.process.data = b'{"event":"done","result":"DONE"}'
        self.process.finished.emit(9, QProcess.ExitStatus.CrashExit)
        self.finished.assert_called_once_with('ERROR', '电脑助手进程异常退出（退出码 9）')
        self.finished.reset_mock()
        self.runner.start('重试')
        self.runner.process.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
        self.finished.assert_called_once_with('ERROR', '电脑助手的 Python 没能启动')

    def test_early_stop_exits_without_taskkill(self):
        self.runner.stop()
        self.assertTrue((self.runner.run_dir / 'stop').is_file())
        self.process.finished.emit(0, QProcess.ExitStatus.NormalExit)
        self.assertIsNone(self.runner.killer)
        self.assertFalse(self.runner.stop_timer.isActive())
        self.finished.assert_called_once_with('STOPPED', '')


    def test_real_qt_pipe_with_synthetic_child(self):
        payload = """
import json
from pathlib import Path
import sys
import time
config = json.load(sys.stdin)
print(json.dumps(dict(event='done', result='DONE', detail=config['language'])), flush=True)
print(json.dumps(dict(event='executed', n=25)), flush=True)
for _ in range(200):
    if Path('release').exists():
        break
    time.sleep(0.02)
"""

        class SyntheticProcess(QProcess):
            def start(self, program, arguments):
                """保留 QProcess 管道，只替换子进程代码。"""
                super().start(program, ['-u', '-B', '-c', payload])

        runner = ComputerRunner(config_path=self.runner.config_path, process_factory=SyntheticProcess)
        loop, timeout = QEventLoop(), QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        steps, results = [], []

        def receive(event):
            """记录 Qt 收到的步骤并结束等待。"""
            steps.append(event)
            loop.quit()

        def finish(result, detail):
            """记录 Qt 收到的进程结果并结束等待。"""
            results.append((result, detail))
            if steps:
                loop.quit()

        runner.event.connect(receive)
        runner.finished.connect(finish)
        runner.start('任务')
        process = runner.process
        try:
            timeout.start(3000)
            loop.exec()
            self.assertEqual(steps, [{'event': 'executed', 'n': 25}])
            self.assertEqual(results, [])
            self.assertIs(runner.process, process)
            (runner.run_dir / 'release').touch()
            timeout.start(3000)
            loop.exec()
            self.assertEqual(results, [('DONE', 'zh')])
        finally:
            timeout.stop()
            if runner.process is not None:
                process.kill()
                process.waitForFinished(1000)

    def test_after_five_second_timeout_uses_taskkill_then_qt_kill(self):
        self.runner.stop()
        self.assertEqual(self.runner.stop_timer.interval(), 5000)
        self.process.kill.assert_not_called()
        self.runner.stop_timer.timeout.emit()
        killer = self.runner.killer
        args = killer.start.call_args.args
        self.assertTrue(args[0].endswith('taskkill.exe'))
        self.assertEqual(args[1], ['/T', '/F', '/PID', '123'])
        self.runner.kill_timer.timeout.emit()
        self.process.kill.assert_called_once_with()
        self.finished.assert_not_called()
        self.process.finished.emit(1, QProcess.ExitStatus.CrashExit)
        self.finished.assert_called_once_with('STOPPED', '停止超时，已请求强制结束')


if __name__ == '__main__':
    unittest.main()
