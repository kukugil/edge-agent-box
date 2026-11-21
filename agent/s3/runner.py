"""托盘侧的 S3 子进程管理。"""

from datetime import datetime
import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .protocol import EventLines
from .run_cli import load_config, validated_key


class S3Runner(QObject):
    """转发 worker 事件，并在确认子进程退出后只报告一次结果。"""

    event = Signal(dict)
    finished = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.killer = None
        self.stop_timer = QTimer(self)
        self.stop_timer.setSingleShot(True)
        self.stop_timer.timeout.connect(self._kill_worker)

    def start(self, task):
        """用独立解释器启动任务，凭据只注入子进程环境。"""
        if self.process is not None:
            return
        try:
            config = load_config()
            key = validated_key(config)
            logs = Path(os.environ['LOCALAPPDATA']) / 'edge-agent' / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            self.log = logs / f's3-stderr-{datetime.now():%Y%m%d-%H%M%S-%f}.log'
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            self.finished.emit('ERROR', f'S3 配置或凭据读取失败（{type(exc).__name__}）')
            return

        self.result = None
        self.stopping = False
        self.lines = EventLines()
        self.stderr = b''
        self.key_bytes = key.encode('utf-8')
        self.log_error = False
        process = QProcess(self)
        self.process = process
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONUTF8', '1')
        env.insert('PYTHONIOENCODING', 'utf-8')
        env.insert('PYTHONDONTWRITEBYTECODE', '1')
        env.insert('DEEPSEEK_API_KEY', key)
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
        process.readyReadStandardOutput.connect(self._read)
        process.readyReadStandardError.connect(self._read_error)
        process.finished.connect(self._exited)
        process.errorOccurred.connect(self._error)
        process.started.connect(self._started)
        process.start(config['python'], ['-u', '-m', 'agent.s3.worker', '--task', task])

    def stop(self):
        """请求 taskkill 停止进程树，异步等 QProcess 确认退出。"""
        if self.process is not None and not self.stopping:
            self.stopping = True
            self._started()

    def _started(self):
        """拿到 PID 后停止整个进程树，给 taskkill 留出回退时间。"""
        if not self.stopping or self.killer is not None:
            return
        pid = self.process.processId()
        if not pid or self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.killer = QProcess(self)
        self.killer.setStandardOutputFile(QProcess.nullDevice())
        self.killer.setStandardErrorFile(QProcess.nullDevice())
        self.killer.errorOccurred.connect(self._kill_worker)
        self.killer.finished.connect(self._kill_worker)
        self.stop_timer.start(1000)
        self.killer.start(str(Path(os.environ.get('SystemRoot', r'C:\Windows'))
                              / 'System32' / 'taskkill.exe'), ['/T', '/F', '/PID', str(pid)])

    def _kill_worker(self, *_):
        """taskkill 没来得及结束 worker 时，由 Qt 杀 worker，Job 回收其子进程。"""
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _read(self, *, final=False):
        """接收输出并记住 done 结果，进程退出前不恢复监听。"""
        for event in self.lines.feed(bytes(self.process.readAllStandardOutput()), final=final):
            if event['event'] == 'done':
                self.result = (event['result'], event.get('detail', ''))
            else:
                self.event.emit(event)

    def _read_error(self, *, final=False):
        """逐行去掉可能回显的凭据，再将 stderr 记到本机日志。"""
        pending = self.stderr + bytes(self.process.readAllStandardError())
        end = len(pending) if final else pending.rfind(b'\n') + 1
        data, self.stderr = pending[:end], pending[end:]
        if data:
            try:
                with self.log.open('ab') as stream:
                    stream.write(data.replace(self.key_bytes, b'[REDACTED]'))
            except OSError:
                self.log_error = True

    def _error(self, error):
        """Qt 启动失败只发 errorOccurred，崩溃仍等待 finished。"""
        if error == QProcess.ProcessError.FailedToStart:
            self._finish('ERROR', 'S3 的 Python 没能启动')

    def _exited(self, code, status):
        """读完尾部输出，再按停止、崩溃、任务结果的顺序收尾。"""
        self._read(final=True)
        self._read_error(final=True)
        if self.stopping:
            self._finish('STOPPED', '已停止')
        elif status == QProcess.ExitStatus.CrashExit:
            self._finish('ERROR', 'S3 进程异常退出')
        elif self.result is not None:
            self._finish(*self.result)
        else:
            self._finish('ERROR', f'S3 未返回结果（退出码 {code}）')

    def _finish(self, result, detail):
        """释放当前进程及停止工具，向会话发送一次完结信号。"""
        if self.process is None:
            return
        process, self.process = self.process, None
        process.deleteLater()
        self.stop_timer.stop()
        if self.killer is not None:
            killer, self.killer = self.killer, None
            killer.errorOccurred.disconnect(self._kill_worker)
            killer.finished.disconnect(self._kill_worker)
            killer.kill()
            killer.deleteLater()
        self.key_bytes = b''
        if self.log_error:
            detail = f'{detail} · S3 日志写入失败'.strip(' ·')
        self.finished.emit(result, detail)
