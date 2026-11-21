"""托盘侧启动和停止电脑助手子进程。"""

from datetime import datetime
import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .protocol import EventLines


def load_config(path):
    """从给定的 JSON 文件读取并校验 computer 配置。"""
    config = json.loads(path.read_text(encoding='utf-8'))['computer']
    for name in ('python', 'base_url', 'model', 'api_key', 'language'):
        if not isinstance(config[name], str) or not config[name].strip():
            raise ValueError(f'无效的 computer.{name}')
    for name, minimum in (('max_loop', 1), ('loop_interval_ms', 0), ('pad_top', 0),
                          ('max_pixels', 28 * 28), ('scroll_clicks', 1)):
        if type(config[name]) is not int or config[name] < minimum:
            raise ValueError(f'无效的 computer.{name}')
    return config


class ComputerRunner(QObject):
    """转发子进程事件，确认进程退出后报告一次结果。"""

    event = Signal(dict)
    finished = Signal(str, str)

    def __init__(self, parent=None, *, config_path=None, process_factory=QProcess):
        """接收配置路径和进程工厂，初始化一次只能运行一个任务的 Runner。"""
        super().__init__(parent)
        self.config_path = config_path or Path(__file__).resolve().parents[1] / 'config.json'
        self.process_factory = process_factory
        self.process = None
        self.killer = None
        self.stop_timer = QTimer(self)
        self.stop_timer.setSingleShot(True)
        self.stop_timer.timeout.connect(self._force_stop)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self._kill_worker)

    def start(self, task):
        """将任务交给 computer.python，把 computer 配置通过标准输入交给 worker。"""
        if self.process is not None:
            return
        try:
            config = load_config(self.config_path)
            runs = Path(os.environ['LOCALAPPDATA']) / 'edge-agent' / 'computer' / 'runs'
            self.run_dir = runs / f'{datetime.now():%Y%m%d-%H%M%S-%f}'
            self.run_dir.mkdir(parents=True)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.finished.emit('ERROR', f'电脑助手配置或运行目录不可用（{type(exc).__name__}）')
            return

        self.result = None
        self.stopping = False
        self.stop_detail = ''
        self.lines = EventLines(config['max_loop'])
        process = self.process = self.process_factory(self)
        env = QProcessEnvironment.systemEnvironment()
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(self.run_dir))
        process.setStandardErrorFile(str(self.run_dir / 'stderr.log'))
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._exited)
        process.errorOccurred.connect(self._error)
        worker = Path(__file__).resolve().with_name('worker.py')
        interpreter = str(Path(config['python']).expanduser().resolve())
        process.start(interpreter, ['-u', '-B', '-E', '-X', 'utf8', str(worker), '--task=' + task,
                                    '--run-dir', str(self.run_dir)])
        if self.process is process:
            process.write(json.dumps(config).encode('utf-8'))
            process.closeWriteChannel()

    def stop(self):
        """写入当前任务的停止文件，并给 worker 五秒清理时间。"""
        if self.process is None or self.stopping:
            return
        self.stopping = True
        try:
            (self.run_dir / 'stop').touch()
        except OSError as exc:
            self.stop_detail = f'停止标志写入失败（{type(exc).__name__}）'
        self.stop_timer.start(5000)

    def _force_stop(self):
        """启动 taskkill 停止进程树，超时或失败后交给 Qt。"""
        process = self.process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        pid = process.processId()
        self.stop_detail = ' · '.join(filter(None, (self.stop_detail, '停止超时，已请求强制结束')))
        if not pid:
            self._kill_worker()
            return
        killer = self.killer = self.process_factory(self)
        killer.setStandardOutputFile(QProcess.nullDevice())
        killer.setStandardErrorFile(QProcess.nullDevice())
        killer.errorOccurred.connect(self._kill_worker)
        killer.finished.connect(self._kill_worker)
        self.kill_timer.start(1000)
        program = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'taskkill.exe'
        killer.start(str(program), ['/T', '/F', '/PID', str(pid)])

    def _kill_worker(self, *_):
        """由 Qt 结束 worker，Windows Job 回收它的子进程。"""
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _read(self, *, final=False):
        """转发步骤，暂存 done，等待进程真正退出。"""
        if self.process is None:
            return
        for event in self.lines.feed(bytes(self.process.readAllStandardOutput()), final=final):
            if event['event'] == 'done':
                self.result = event['result'], event.get('detail', '')
            else:
                self.event.emit(event)

    def _error(self, error):
        """处理没有 finished 信号的启动失败。"""
        if error == QProcess.ProcessError.FailedToStart:
            self._finish('ERROR', '电脑助手的 Python 没能启动')

    def _exited(self, code, status):
        """读取尾部事件。按停止、异常退出、正常结果的优先级收尾。"""
        if self.process is None:
            return
        self._read(final=True)
        if self.stopping:
            self._finish('STOPPED', self.stop_detail)
        elif status == QProcess.ExitStatus.CrashExit or code:
            self._finish('ERROR', f'电脑助手进程异常退出（退出码 {code}）')
        else:
            self._finish(*(self.result or ('ERROR', '电脑助手未返回结果')))

    def _finish(self, result, detail):
        """释放进程和停止工具，发出一次完结信号。"""
        if self.process is None:
            return
        process, self.process = self.process, None
        process.deleteLater()
        self.stop_timer.stop()
        self.kill_timer.stop()
        if self.killer is not None:
            killer, self.killer = self.killer, None
            killer.errorOccurred.disconnect(self._kill_worker)
            killer.finished.disconnect(self._kill_worker)
            if killer.state() != QProcess.ProcessState.NotRunning:
                killer.kill()
            killer.deleteLater()
        self.finished.emit(result, detail)
