"""端侧盒子 Windows 托盘和人工接管窗口。"""
import argparse
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
    QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QPushButton, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
)

from agent.core import reply, trigger
from agent.tools import history


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

    def __init__(self, contacts, app):
        super().__init__()
        self.contacts = contacts
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
        self.menu.addAction('打开待我处理', self.open_window)
        self.menu.addAction('退出端侧盒子窗口', app.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray)
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(900)
        self.refresh()

    def on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_window()

    def open_window(self):
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
            self.tray.setToolTip(f'端侧盒子 · {count} 项待处理')
        self.render()

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
        self.status.setText('等待人工处理')
        self.title.setText(name)
        self.reason.setText(f"{info['reason']} · {info['started']:%m-%d %H:%M} · 自动回复已暂停")
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
        try:
            history.resume_handoff(marker, reply.loan_request, expected=info['stamp'])
        except history.HandoffChanged:
            self.feedback.setText('待处理内容已更新，请查看后再点恢复。')
            self.refresh()
            return
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.feedback.setText(f'恢复未完成，请稍后再试（{type(error).__name__}）。')
            self.refresh()
            return
        self.feedback.setText(f'{name}已恢复自动回复。')
        self.refresh()


def main():
    parser = argparse.ArgumentParser(description='端侧盒子 Windows 托盘应用')
    parser.add_argument('--tray', action='store_true', help='启动时只显示托盘图标')
    parser.add_argument('--file-transfer-only', action='store_true', help='只显示文件传输助手的模拟事项')
    args = parser.parse_args()
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
    window = Inbox(contacts, app)
    if not args.tray:
        window.open_window()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
