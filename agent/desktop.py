"""端侧盒子 Windows 托盘和人工接管窗口。"""
import argparse
import sqlite3
from datetime import datetime
from importlib.util import find_spec
import os
import sys
from pathlib import Path

if find_spec('PySide6') is None:
    local_lib = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'edge-agent' / 'desktop-deps'
    if (local_lib / 'PySide6').is_dir():
        sys.path.insert(0, str(local_lib))

from PySide6.QtCore import QLockFile, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QPushButton, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
)

from agent.core import reply, trigger
from agent.core.mail_flow import MAIL
from agent.desktop_runner import ModelDialog, Runner, open_mail_window
from agent.core.wechat_reply_flow import database_path
from agent.tools.reply_store import ReplyStore
from agent.tools import history
from agent.s3.panel import Panel
from agent.s3.runner import S3Runner
from agent.s3.session import Session


STYLE = """
QWidget { color: #25282e; font-family: 'Microsoft YaHei UI'; font-size: 13px; }
QMainWindow, QWidget#details { background: white; }
QWidget#sidebar { background: #f5f6f8; border-right: 1px solid #e6e8ec; }
QLabel#brand { font-size: 17px; font-weight: bold; }
QLabel#page { font-size: 26px; font-weight: bold; }
QLabel#state { color: #73521e; background: #fff4df; border-radius: 6px; padding: 7px 11px; }
QLabel#title { font-size: 20px; font-weight: bold; }
QLabel#secondary { color: #606875; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 11px 10px; border-radius: 8px; margin: 2px 0; }
QListWidget::item:hover { background: #e9edf4; }
QListWidget::item:selected { background: #e5edff; color: #2149ac; }
QTextBrowser { border: 1px solid #e6e8ec; border-radius: 9px; background: #fafbfd; padding: 13px; }
QPushButton { background: #2f5cdd; color: white; border: 1px solid #2f5cdd;
              border-radius: 7px; padding: 9px 14px; font-weight: bold; }
QPushButton:hover { background: #234dc5; }
QPushButton:focus { border: 2px solid #152d76; }
QPushButton:disabled { background: #edf0f5; color: #6c7380; border-color: #edf0f5; }
QPushButton#plain { background: white; color: #2f5cdd; border: 1px solid #c9d3ea; }
QPushButton#plain:hover { background: #eef2fb; }
QLabel#run { font-weight: bold; }
QLabel#recent { color: #606875; font-size: 12px; }
"""


def box_icon(count=0):
    """绘制任务栏图标，待处理时加一个醒目的角标。"""
    canvas = QPixmap(64, 64)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#2f5cdd'))
    painter.drawRoundedRect(QRectF(7, 7, 50, 50), 12, 12)
    painter.setPen(QPen(QColor('white'), 3))
    painter.drawRoundedRect(QRectF(18, 20, 28, 21), 3, 3)
    painter.drawLine(24, 47, 40, 47)
    if count:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#f0a845'))
        painter.drawEllipse(QRectF(44, 4, 17, 17))
    painter.end()
    return QIcon(canvas)


def text_label(text, style=None):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if style:
        label.setObjectName(style)
    return label


class Inbox(QMainWindow):
    """显示当前接管事项。隐藏窗口后仍可从托盘打开。"""

    def __init__(self, contacts, app, reply_db=None):
        super().__init__()
        self.app = app
        self.contacts = contacts
        self.reply_db = reply_db
        self.items = {}
        self.current = None
        self.tray_count = None
        self.transcript_version = None
        self.setStyleSheet(STYLE)
        self.setWindowTitle('端侧盒子 · 待我处理')
        self.setWindowIcon(box_icon())
        self.resize(910, 610)
        self.setMinimumSize(640, 460)
        main = QWidget()
        layout = QHBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(main)

        sidebar = QWidget(objectName='sidebar')
        sidebar.setFixedWidth(238)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(20, 25, 16, 20)
        nav.setSpacing(16)
        nav.addWidget(text_label('端侧盒子', 'brand'))
        # 监听开关放最上面：托盘是唯一入口，开始/停止、看状态都在这里（11/19 用户定）
        self.run_state = text_label('未在监听', 'run')
        self.run_state.setWordWrap(True)
        nav.addWidget(self.run_state)
        self.toggle = QPushButton('开始自动回复')
        self.toggle.clicked.connect(self.toggle_listen)
        nav.addWidget(self.toggle)
        self.mail = QCheckBox('同时回复 QQ 邮箱')
        self.mail.setChecked(bool(trigger.load_allowed_mail()))
        self.mail.setEnabled(bool(trigger.load_allowed_mail()))
        self.mail.setToolTip('要先点"打开邮箱窗口"并登录测试邮箱；改了要重新开始才生效')
        nav.addWidget(self.mail)
        mail_window = QPushButton('打开邮箱窗口')
        mail_window.setObjectName('plain')
        mail_window.clicked.connect(lambda: self.feedback.setText(open_mail_window()))
        nav.addWidget(mail_window)
        self.recent = text_label('', 'recent')
        self.recent.setWordWrap(True)
        nav.addWidget(self.recent)
        self.s3_button = QPushButton('电脑助手')
        self.s3_button.setObjectName('plain')
        self.s3_button.clicked.connect(self.open_s3)
        nav.addWidget(self.s3_button)
        settings = QPushButton('模型设置')
        settings.setObjectName('plain')
        settings.clicked.connect(self.open_settings)
        nav.addWidget(settings)
        self.count = text_label('待我处理', 'secondary')
        nav.addWidget(self.count)
        self.list = QListWidget()
        self.list.setAccessibleName('待处理会话')
        self.list.currentItemChanged.connect(lambda *_: self.render())
        nav.addWidget(self.list, 1)
        self.errors = text_label('', 'secondary')
        self.errors.setWordWrap(True)
        nav.addWidget(self.errors)
        layout.addWidget(sidebar)

        details = QWidget(objectName='details')
        content = QVBoxLayout(details)
        content.setContentsMargins(32, 26, 32, 24)
        content.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(text_label('待我处理', 'page'))
        heading.addStretch()
        self.status = text_label('', 'state')
        heading.addWidget(self.status)
        content.addLayout(heading)
        self.title = text_label('', 'title')
        self.title.setWordWrap(True)
        content.addWidget(self.title)
        self.reason = text_label('', 'secondary')
        self.reason.setWordWrap(True)
        content.addWidget(self.reason)
        self.transcript = QTextBrowser()
        self.transcript.setAccessibleName('最近聊天截图、触发消息和后续消息')
        content.addWidget(self.transcript, 1)
        self.feedback = text_label('', 'secondary')
        self.feedback.setWordWrap(True)
        content.addWidget(self.feedback)
        row = QHBoxLayout()
        row.addStretch()
        self.resume = QPushButton('已处理，恢复自动回复')
        self.resume.clicked.connect(self.restore)
        row.addWidget(self.resume)
        content.addLayout(row)
        layout.addWidget(details, 1)

        self.tray = QSystemTrayIcon(box_icon(), self)
        self.menu = QMenu(self)
        self.menu.addAction('打开端侧盒子', self.open_window)
        self.listen_action = self.menu.addAction('开始自动回复', self.toggle_listen)
        self.s3_action = self.menu.addAction('电脑助手', self.open_s3)
        self.s3_stop = self.menu.addAction('停止电脑助手', self.stop_s3)
        self.menu.addAction('退出端侧盒子', self.quit)
        self.runner = Runner(self)
        self.runner.changed.connect(self.show_runner)
        self.s3_runner = S3Runner(self)
        self.s3_panel = Panel(self.start_s3, self.stop_s3)
        self.s3_panel.setStyleSheet(STYLE)
        self.s3_runner.event.connect(self.s3_panel.apply_event)
        self.s3 = Session(self.runner, self.s3_runner, self.show_s3_state, self.s3_panel.show_result)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray)
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(900)
        self.refresh()
        self.show_s3_state()

    def toggle_listen(self):
        if self.s3.busy:
            return
        if self.runner.running():
            self.runner.stop()
        else:
            self.runner.start(['--mail'] if self.mail.isChecked() else [])

    def show_runner(self):
        running = self.runner.running()
        self.run_state.setText(self.runner.status)
        label = '停止自动回复' if running else '开始自动回复'
        self.toggle.setText(label)
        can_toggle = not (self.runner.stopping or self.s3.busy)
        self.toggle.setEnabled(can_toggle)
        self.mail.setEnabled(not running and not self.s3.busy and bool(trigger.load_allowed_mail()))
        self.listen_action.setText(label)
        self.listen_action.setEnabled(can_toggle)
        self.recent.setText(chr(10).join(self.runner.recent))
        self.update_tray_tooltip()

    def start_s3(self, task):
        """隐藏会遮挡操作目标的窗口，再提交任务。"""
        if not self.s3.busy:
            self.hide()
            self.s3_panel.hide()
            self.s3.start(task)

    def stop_s3(self):
        """停止当前电脑助手任务或取消待启动的任务。"""
        self.s3.stop()

    def open_s3(self):
        """只在空闲时打开电脑助手窗口。"""
        if not self.s3.busy:
            self.s3_panel.open()

    def show_s3_state(self):
        """互斥期间锁住监听入口和窗口入口。"""
        busy = self.s3.busy
        for widget in (self.s3_button, self.s3_action, self.s3_panel.task, self.s3_panel.start_button):
            widget.setEnabled(not busy)
        can_stop = self.s3.state in ('waiting', 'running')
        self.s3_stop.setEnabled(can_stop)
        self.s3_panel.stop_button.setEnabled(can_stop)
        self.show_runner()

    def update_tray_tooltip(self):
        """刷新当前任务状态和总用时，空闲时展示监听状态。"""
        if self.s3.busy:
            status = {
                'waiting': '正在停下微信自动回复…',
                'cancelled': '已取消启动，等待监听退出…',
                'running': self.s3_panel.progress or '正在启动…',
                'stopping': '正在停止…',
            }[self.s3.state]
            elapsed = f'{self.s3.elapsed():.1f} 秒'
            self.s3_panel.status.setText(f'{status} · 总用时 {elapsed}')
            self.tray.setToolTip(f'电脑助手运行中 · {status} · {elapsed}')
        else:
            self.tray.setToolTip(f'端侧盒子 · {self.runner.status} · {self.tray_count or 0} 项待处理')

    def open_settings(self):
        ModelDialog(self).exec()

    def quit(self):
        """先结束电脑助手，再走监听退出流程。"""
        self.s3.quit(self.quit_listen)

    def quit_listen(self):
        """先让监听停下（正在发的那条发完），再退出托盘，不留下没人管的监听进程。"""
        if self.runner.running():
            self.open_window()
            self.runner.stop(then=self.app.quit)
        else:
            self.app.quit()

    def on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_window()

    def open_window(self):
        if self.s3.busy:
            return
        self.refresh()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.render)

    def refresh(self):
        items = {}
        failed = {}
        for name, simulate in self.contacts:
            marker = history.handoff_path(trigger.history_folder(name, simulate), name)
            try:
                info = history.handoff_details(marker, reply.loan_request)
            except (OSError, ValueError, TypeError, KeyError):
                failed[marker] = name + ('（模拟）' if simulate else '')
                if marker in self.items:
                    items[marker] = self.items[marker]
                continue
            if info is not None:
                items[marker] = (name + ('（模拟）' if simulate else ''), info)
        db_path = self.reply_db or database_path('send')
        if db_path.is_file():
            try:
                store = ReplyStore(db_path)
                for name, simulate in self.contacts:
                    if not simulate:
                        items.update(self.reply_items(store, name))
            except (OSError, sqlite3.Error, ValueError):
                failed['reply-db'] = '回复账本'
                for key, value in self.items.items():
                    if value[1].get('store'):
                        items[key] = value
                        failed[key] = value[0]
        self.read_failed = failed
        self.errors.setText('暂无法读取：' + '、'.join(failed.values()) if failed else '')
        self.errors.setVisible(bool(failed))
        if items != self.items:
            previous = self.current
            self.items = items
            self.list.blockSignals(True)
            self.list.clear()
            for marker, (name, info) in sorted(items.items(), key=lambda item: item[1][1]['started'], reverse=True):
                item = QListWidgetItem(f"{name}\n{len(info['followups'])} 条后续消息")
                item.setData(Qt.ItemDataRole.UserRole, marker)
                self.list.addItem(item)
                if marker == previous:
                    self.list.setCurrentItem(item)
            if self.list.count() and self.list.currentItem() is None:
                self.list.setCurrentRow(0)
            self.list.blockSignals(False)
        count = len(items)
        self.count.setText(f'待我处理  ·  {count}')
        if count != self.tray_count:
            self.tray_count = count
            self.tray.setIcon(box_icon(count))
        self.update_tray_tooltip()
        self.render()

    def reply_items(self, store, name):
        """把新的事项和发送核对项适配成托盘已有的详情结构。"""
        items = {}
        for issue in store.open_issues(name):
            key = f'issue:{issue["turn_id"]}:{issue["part"]}'
            items[key] = (name, {'store': store.path, 'id': issue['turn_id'], 'part': issue['part'],
                                 'reason': '需要本人处理', 'request': {'text': issue['text']},
                                 'started': datetime.fromtimestamp(issue['created']),
                                 'source': 'uia', 'aligned': True, 'followups': [], 'preview': b''})
        for turn in store.review_items(name):
            key = f'review:{turn["id"]}'
            lines = [row['text'] for row in turn['incoming'] if row['who'] == 'them']
            items[key] = (name, {'store': store.path, 'id': turn['id'], 'part': None,
                                 'reason': turn['reason'], 'request': {'text': '\n'.join(lines) or '请核对微信来信'},
                                 'started': datetime.fromtimestamp(turn['created']),
                                 'source': 'uia', 'aligned': True, 'followups': [], 'preview': b''})
        for turn in store.preview_items(name):
            key = f'preview:{turn["id"]}'
            lines = '\n'.join(row['text'] for row in turn['incoming'])
            transcript = f'{lines}\n\n回复草稿 · 未发送\n{turn["draft"] or "本轮无发送草稿"}'
            items[key] = (name, {'store': store.path, 'id': turn['id'], 'part': None, 'readonly': True,
                                 'reason': turn['reason'], 'request': {'text': transcript},
                                 'started': datetime.fromtimestamp(turn['created']),
                                 'source': 'uia', 'aligned': True, 'followups': [], 'preview': b''})
        return items

    def render(self):
        item = self.list.currentItem()
        self.current = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        selected = self.items.get(self.current)
        self.status.setVisible(bool(selected))
        self.transcript.setVisible(bool(selected))
        self.resume.setEnabled(bool(selected) and self.current not in self.read_failed)
        if not selected:
            self.title.setText('没有待处理的会话')
            self.reason.setText('需要你接手时，事项会留在这里。')
            return
        name, info = selected
        self.status.setText('预览 · 未发送' if info.get('readonly') else '等待人工处理')
        if info.get('readonly'):
            self.resume.setEnabled(False)
        self.resume.setText('已核对，结束此项' if info.get('store') else '已处理，恢复自动回复')
        self.title.setText(name)
        suffix = '' if info.get('store') else ' · 自动回复已暂停'
        self.reason.setText(f"{info['reason']} · {info['started']:%m-%d %H:%M}{suffix}")
        request = info['request']
        source = '（OCR 识别）' if info['source'] == 'ocr' else ''
        lines = ['触发消息' + source, request['text'] if request else '未保存原文', '', '后续消息']
        if info['aligned']:
            lines += [row['text'] for row in info['followups']] or ['暂无']
        else:
            lines.append('暂时无法对齐历史消息。请到微信查看完整会话。')
        body = '\n'.join(lines)
        self.render_transcript(body, info['preview'])

    def render_transcript(self, body, preview):
        width = max(1, self.transcript.viewport().width() - 36)
        version = body, preview, width
        if self.transcript_version == version:
            return
        self.transcript_version = version
        image = QImage.fromData(preview, 'PNG') if preview else QImage()
        if image.isNull():
            self.transcript.setPlainText(body)
            return
        self.transcript.clear()
        url = QUrl('handoff:/recent.png')
        self.transcript.document().addResource(QTextDocument.ResourceType.ImageResource, url, image)
        cursor = self.transcript.textCursor()
        cursor.insertText('最近聊天 · 微信截图\n')
        display = QTextImageFormat()
        display.setName(url.toString())
        display.setWidth(min(width, image.width()))
        display.setHeight(image.height() * min(width, image.width()) / image.width())
        cursor.insertImage(display)
        cursor.insertText('\n\n' + body)
        self.transcript.verticalScrollBar().setValue(0)

    def restore(self):
        if self.current is None or self.current in self.read_failed:
            return
        marker = self.current
        name, info = self.items[marker]
        if info.get('readonly'):
            return
        try:
            if info.get('store'):
                if not ReplyStore(info['store']).resolve(name, info['id'], info['part']):
                    raise history.HandoffChanged('事项已经变化')
            else:
                history.resume_handoff(marker, reply.loan_request, expected=info['stamp'])
        except history.HandoffChanged:
            self.feedback.setText('待处理内容已更新，请查看后再点恢复。')
            self.refresh()
            return
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
            self.feedback.setText(f'恢复未完成，请稍后再试（{type(error).__name__}）。')
            self.refresh()
            return
        self.feedback.setText('已结束当前事项。' if info.get('store') else f'{name}已恢复自动回复。')
        self.refresh()


def main():
    parser = argparse.ArgumentParser(description='端侧盒子 Windows 托盘应用')
    parser.add_argument('--tray', action='store_true', help='启动时只显示托盘图标')
    parser.add_argument('--file-transfer-only', action='store_true', help='只显示文件传输助手的模拟事项')
    parser.add_argument('--reply-db', type=Path, help='显示另一个测试回复账本中的事项')
    parser.add_argument('--listen', action='store_true', help='打开托盘后立刻开始自动回复')
    args = parser.parse_args()
    if args.listen and args.reply_db:
        parser.error('--listen 写的是默认账本，不能和 --reply-db 一起用，否则托盘看的和监听写的不是同一本')
    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        parser.exit(1, 'Windows 托盘不可用。\n')
    local = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'edge-agent'
    local.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(local / 'desktop.lock'))
    if not lock.tryLock(0):
        parser.exit(1, '端侧盒子窗口已在运行，请从托盘打开。\n')
    contacts = [(name, False) for name in trigger.WHITELIST] if not args.file_transfer_only else []
    if trigger.SIM_NAME in trigger.WHITELIST:
        contacts.append((trigger.SIM_NAME, True))
    contacts.append((MAIL, False))     # 邮件的转人工和待核对也记在同一本账里
    window = Inbox(contacts, app, args.reply_db)
    if not args.tray:
        window.open_window()
    if args.listen:
        window.runner.start(['--mail'] if window.mail.isChecked() else [])
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
