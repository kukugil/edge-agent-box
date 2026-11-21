"""用假监听和假 S3 进程核对切换和退出顺序。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from agent.s3.session import Session


class TestSession(unittest.TestCase):
    def setUp(self):
        self.listener = Mock(last_extra=['--mail', '--tag', '保留 原参数'])
        self.listener.running.return_value = True
        self.worker = Mock()
        self.completed = Mock()
        self.session = Session(self.listener, self.worker, Mock(), self.completed)
        self.finish_worker = self.worker.finished.connect.call_args.args[0]

    def listener_exits(self):
        """模拟监听进程退出后交回停止回调。"""
        self.listener.running.return_value = False
        self.listener.stop.call_args.kwargs['then']()

    def test_wait_for_listener_exit_and_restore_original_arguments_once(self):
        self.session.start('任务')
        self.assertEqual(self.session.state, 'waiting')
        self.worker.start.assert_not_called()
        self.listener.last_extra[:] = ['changed']
        self.listener_exits()
        self.worker.start.assert_called_once_with('任务')
        self.listener.start.assert_not_called()
        self.finish_worker('DONE', '')
        self.finish_worker('DONE', '')
        self.listener.start.assert_called_once_with(['--mail', '--tag', '保留 原参数'])
        self.completed.assert_called_once()
        self.assertFalse(self.session.busy)

    def test_no_listener_starts_immediately_without_restoring(self):
        self.listener.running.return_value = False
        self.session.start('任务')
        self.worker.start.assert_called_once_with('任务')
        self.listener.stop.assert_not_called()
        self.finish_worker('STEP_LIMIT', '')
        self.listener.start.assert_not_called()
        self.assertFalse(self.session.busy)

    def test_cancel_while_waiting_restores_only_after_listener_exits(self):
        self.session.start('任务')
        self.session.stop()
        self.assertEqual(self.session.state, 'cancelled')
        self.listener.start.assert_not_called()
        self.listener_exits()
        self.worker.start.assert_not_called()
        self.worker.stop.assert_not_called()
        self.listener.start.assert_called_once()
        self.assertEqual(self.completed.call_args.args[:2], ('STOPPED', '已取消启动'))

    def test_tray_quit_waiting_cancels_without_start_or_restore(self):
        quit_ = Mock()
        self.session.start('任务')
        self.session.quit(quit_)
        quit_.assert_not_called()
        self.listener_exits()
        quit_.assert_called_once()
        self.worker.start.assert_not_called()
        self.listener.start.assert_not_called()
        self.completed.assert_not_called()

    def test_tray_quit_running_waits_for_worker_exit(self):
        quit_ = Mock()
        self.session.start('任务')
        self.listener_exits()
        self.session.quit(quit_)
        self.worker.stop.assert_called_once()
        quit_.assert_not_called()
        self.finish_worker('STOPPED', '')
        self.finish_worker('ERROR', '迟到的结果')
        quit_.assert_called_once()
        self.listener.start.assert_not_called()
        self.completed.assert_not_called()

    def test_failed_listener_stop_does_not_launch_or_restart(self):
        self.listener.stop.side_effect = OSError
        self.session.start('任务')
        self.worker.start.assert_not_called()
        self.listener.start.assert_not_called()
        self.assertFalse(self.session.busy)
        self.assertEqual(self.completed.call_args.args[0], 'ERROR')


class TestRunnerExit(unittest.TestCase):
    def test_qprocess_exit_paths_only_restore_once(self):
        from agent import desktop_runner  # 由现有入口添加本机 Qt 依赖路径。
        from agent.s3 import runner

        qt = desktop_runner.QProcess
        normal = qt.ExitStatus.NormalExit
        crash = qt.ExitStatus.CrashExit
        cases = [('DONE', normal, False, 'DONE'), ('FAIL', normal, False, 'FAIL'),
                 ('STEP_LIMIT', normal, False, 'STEP_LIMIT'), ('ERROR', normal, False, 'ERROR'),
                 ('DONE', crash, False, 'ERROR'), ('', normal, False, 'ERROR'),
                 ('', crash, True, 'STOPPED'), ('', None, False, 'ERROR')]
        for result, status, stopped, expected in cases:
            with self.subTest(result=result, status=status, stopped=stopped), TemporaryDirectory() as tmp:
                process, killer = Mock(), Mock()
                process.state.return_value = qt.ProcessState.Running
                process.processId.return_value = 1820
                process.readAllStandardOutput.return_value = b''
                process.readAllStandardError.side_effect = [b'with sk-fa', b'ke\n', b'']
                listener = Mock(last_extra=[])
                completed = Mock()
                with (patch.object(runner, 'QTimer'), patch.object(runner, 'QProcess') as factory,
                      patch.object(runner, 'validated_key', return_value='sk-fake'),
                      patch.dict('os.environ', {'LOCALAPPDATA': tmp})):
                    factory.side_effect = [process, killer]
                    factory.ProcessError = qt.ProcessError
                    factory.ProcessState = qt.ProcessState
                    factory.ExitStatus = qt.ExitStatus
                    worker = runner.S3Runner()
                    session = Session(listener, worker, Mock(), completed)
                    session.start('找 “深圳湾”')
                    listener.stop.call_args.kwargs['then']()
                    process.start.assert_called_once()
                    self.assertEqual(process.start.call_args.args[1][-2:], ['--task', '找 “深圳湾”'])
                    environment = process.setProcessEnvironment.call_args.args[0]
                    self.assertEqual(environment.value('DEEPSEEK_API_KEY'), 'sk-fake')
                    if result:
                        process.readAllStandardOutput.return_value = (
                            f'{{"event":"done","result":"{result}","detail":""}}\n'.encode())
                        worker._read()
                        process.readAllStandardOutput.return_value = b''
                    worker._read_error()
                    worker._read_error()
                    listener.start.assert_not_called()
                    if stopped:
                        session.stop()
                        self.assertEqual(killer.start.call_args.args[1], ['/T', '/F', '/PID', '1820'])
                        worker._kill_worker()
                        process.kill.assert_called_once()
                    if status is None:
                        worker._error(qt.ProcessError.FailedToStart)
                        worker._error(qt.ProcessError.FailedToStart)
                    else:
                        worker._exited(1 if result == 'ERROR' else 0, status)
                    listener.start.assert_called_once_with([])
                    self.assertEqual(completed.call_args.args[0], expected)
                    log = next(Path(tmp).rglob('*.log')).read_bytes()
                    self.assertEqual(log, b'with [REDACTED]\n')


if __name__ == '__main__':
    unittest.main()
