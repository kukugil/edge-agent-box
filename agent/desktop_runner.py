"""托盘里的"开始/停止监听"和"模型设置"。监听跑在子进程里：卡住或出错不会拖死托盘，停不下来时还能结束进程。"""
import os
import sys
import threading
from collections import deque
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path

if find_spec('PySide6') is None:    # 和 desktop.py 一样：PySide6 装在本机数据目录，不进项目环境
    _deps = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'edge-agent' / 'desktop-deps'
    if (_deps / 'PySide6').is_dir():
        sys.path.insert(0, str(_deps))

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from agent.tools import models, secrets

ROOT = Path(__file__).resolve().parent.parent
STOP_GRACE_MS = 90_000      # 一轮最长：等静 + 模型 30 秒 + 发送核对，留足余量再强制结束


def local_dir() -> Path:
    path = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'edge-agent'
    path.mkdir(parents=True, exist_ok=True)
    return path


def status_of(line: str) -> str | None:
    """从监听输出的一行里取托盘顶部要显示的状态；不是状态行交回 None。"""
    line = line.strip()
    if line.startswith('状态：'):
        return line[3:]
    if line.startswith('已停止'):
        return line
    if line.startswith('有待回复，微信') or line.startswith('微信回到前台'):
        return '监听中 · ' + line
    return None


class Runner(QObject):
    """管一个监听子进程。changed 在状态或动态变化时发出，托盘据此刷新。"""

    changed = Signal()

    def __init__(self, parent=None, program=None, args=None):
        super().__init__(parent)
        self.program = program or sys.executable
        self.args = args or ['-u', '-m', 'agent.core.listen']
        self.stop_file = local_dir() / 'listen.stop'
        self.log_dir = local_dir() / 'logs'
        self.process = None
        self.status = '未在监听'
        self.recent = deque(maxlen=6)
        self.stopping = False
        self.after_stop = []
        self.last_extra = []
        self.buffer = b''
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self._kill)

    def running(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, extra=()) -> None:
        """extra：附加给监听的参数，比如 ['--mail']。"""
        if self.running():
            return
        self.stop_file.unlink(missing_ok=True)
        self.last_extra = list(extra)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # 日志不放项目里：和别的微信相关输出一样，只留在本机数据目录
        self.log_path = self.log_dir / f'listen-{datetime.now():%m%d-%H%M%S}.log'
        self.stopping = False
        self.buffer = b''
        self.recent.clear()
        process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONUTF8', '1')
        env.insert('PYTHONIOENCODING', 'utf-8')
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        self.process = process
        self.status = '正在启动'
        process.start(self.program, [*self.args, '--stop-file', str(self.stop_file), *self.last_extra])
        self.changed.emit()

    def stop(self, then=None) -> None:
        """请监听停下。then：停下后要做的事（比如退出托盘）。"""
        if self.running() and not self.stopping:
            self.stop_file.write_text('stop', encoding='utf-8')
            self.stopping = True
            self.status = '正在停止：等这一轮做完'
            self.kill_timer.start(STOP_GRACE_MS)
            self.changed.emit()
        if then is not None:
            self.after_stop.append(then)
        if not self.running():
            self._drain()

    def _kill(self):
        if self.running():
            self._line('监听没有按时停下，已强制结束（看一下微信输入框里有没有留下的字）')
            self.process.kill()

    def _read(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        *lines, self.buffer = self.buffer.split(b'\n')
        for raw in lines:
            self._line(raw.decode('utf-8', errors='replace').rstrip('\r'))

    def _line(self, line):
        if not line.strip():
            return
        try:
            with self.log_path.open('a', encoding='utf-8') as stream:
                stream.write(f'{datetime.now():%H:%M:%S} {line}\n')
        except OSError:
            pass
        self.recent.append(f'{datetime.now():%H:%M} {line.strip()}')
        status = status_of(line)
        if status:
            self.status = status
        self.changed.emit()

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.status = '已停止：监听程序没能启动'
            self._finished()

    def _finished(self, *_):
        self._read()
        if self.buffer:
            self._line(self.buffer.decode('utf-8', errors='replace'))
            self.buffer = b''
        self.kill_timer.stop()
        self.stop_file.unlink(missing_ok=True)
        if not self.status.startswith('已停止'):
            self.status = '已停止'
        self.stopping = False
        self.changed.emit()
        self._drain()

    def _drain(self):
        callbacks, self.after_stop = self.after_stop, []
        for callback in callbacks:
            callback()


def open_mail_window() -> str:
    """用专用配置打开 Chrome 里的 QQ 邮箱。交回一句给人看的结果。

    为什么单独一个配置：网页内容要带 --force-renderer-accessibility 才读得到，日常用的 Chrome 没带；
    另两个参数让窗口被挡住时也照常刷新。登录状态留在这个配置里，下次打开不用再登录。
    """
    chrome = None
    for base in (os.getenv('LOCALAPPDATA'), os.getenv('PROGRAMFILES'), os.getenv('PROGRAMFILES(X86)')):
        path = Path(base or '') / 'Google' / 'Chrome' / 'Application' / 'chrome.exe'
        if base and path.is_file():
            chrome = path
            break
    if chrome is None:
        return '没找到 Chrome，请先安装 Chrome'
    profile = local_dir() / 'chrome-agent'
    ok = QProcess.startDetached(str(chrome), [f'--user-data-dir={profile}', '--force-renderer-accessibility',
                                              '--disable-features=CalculateNativeWinOcclusion',
                                              '--disable-backgrounding-occluded-windows', '--no-first-run',
                                              'https://mail.qq.com'])
    return '已打开邮箱窗口：第一次要登录测试邮箱' if ok else '邮箱窗口没能打开'


class Bridge(QObject):
    """把后台线程的结果送回界面线程。"""
    done = Signal(str)


class ModelDialog(QDialog):
    """填模型接口地址、模型名和 API Key。Key 进 Windows 凭据管理器，界面上不回显。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('模型设置')
        self.setMinimumWidth(460)
        current = models.load_config()
        saved = models.load_user_config()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url = QLineEdit(current.get('llm_url', ''))
        self.model = QLineEdit(current.get('llm_model', ''))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        try:
            has_key = bool(saved) and bool(secrets.get_key())
        except OSError:
            has_key = False
        self.key.setPlaceholderText('已保存（不显示），留空表示不改' if has_key else '内网 Spark 不用填')
        form.addRow('接口地址', self.url)
        form.addRow('模型名', self.model)
        form.addRow('API Key', self.key)
        layout.addLayout(form)
        note = QLabel('改完要重新"开始监听"才生效。回复是按 Qwen3.8-27B 调的，换别的模型后效果还没验证。')
        note.setWordWrap(True)
        note.setObjectName('secondary')
        layout.addWidget(note)
        self.result = QLabel('')
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        row = QHBoxLayout()
        self.test = QPushButton('测试连接')
        self.test.clicked.connect(self.try_connect)
        reset = QPushButton('恢复默认')
        reset.clicked.connect(self.reset)
        save = QPushButton('保存')
        save.clicked.connect(self.save)
        row.addWidget(self.test)
        row.addWidget(reset)
        row.addStretch()
        row.addWidget(save)
        layout.addLayout(row)
        self.bridge = Bridge(self)
        self.bridge.done.connect(self._tested)

    def _key_for_test(self, url):
        """测试时用输入框里的 key；没填就用已保存的，且只在地址没变时用（不把 key 发到别的地址）。"""
        typed = self.key.text().strip()
        if typed:
            return typed
        if url == models.load_user_config().get('llm_url'):
            try:
                return secrets.get_key()
            except OSError:
                return ''
        return ''

    def try_connect(self):
        try:
            url, model = models.check_model(self.url.text(), self.model.text())
        except ValueError as exc:
            self.result.setText(str(exc))
            return
        key = self._key_for_test(url)
        self.test.setEnabled(False)
        self.result.setText('正在连接…')

        def work():
            try:
                reply = models.chat(url, model, [{'role': 'user', 'content': '只回一个字：好'}],
                                    max_tokens=8, timeout=20, key=key)
                text = f'连上了，模型回复了 {len(reply or "")} 个字'
            except Exception as exc:    # 网络、鉴权、格式错误都只报类型，不回显可能带 key 的内容
                text = f'没连上：{type(exc).__name__}'
                code = getattr(exc, 'code', None)
                if code:
                    text += f'（HTTP {code}）'
            self.bridge.done.emit(text)
        threading.Thread(target=work, daemon=True).start()

    def _tested(self, text):
        self.test.setEnabled(True)
        self.result.setText(text)

    def save(self):
        try:
            models.save_user_config(self.url.text(), self.model.text())
            if self.key.text().strip():
                secrets.put_key(self.key.text().strip())
        except (ValueError, OSError) as exc:
            self.result.setText(f'没保存：{exc}')
            return
        self.accept()

    def reset(self):
        try:
            models.user_config_path().unlink(missing_ok=True)
            secrets.delete_key()
        except OSError as exc:
            self.result.setText(f'没恢复：{type(exc).__name__}')
            return
        current = models.load_config()
        self.url.setText(current.get('llm_url', ''))
        self.model.setText(current.get('llm_model', ''))
        self.key.clear()
        self.key.setPlaceholderText('内网 Spark 不用填')
        self.result.setText('已恢复成仓库里的默认模型，key 已删除')
