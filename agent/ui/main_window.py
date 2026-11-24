"""端侧盒子主窗口、待处理事项和托盘。"""

from datetime import datetime
import sqlite3
from stat import S_ISREG

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QPushButton, QStackedWidget, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
)

from agent.computer.runner import ComputerRunner
from agent.computer.session import Session
from agent.core import reply, trigger
from agent.core.mail_flow import MAIL
from agent.core.wechat_reply_flow import database_path
from agent.desktop_runner import ModelDialog, Runner, open_mail_window
from agent.tools import history
from agent.tools.reply_store import ReplyStore
from .action_overlay import ActionOverlay
from .computer_page import ComputerPage


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
    """返回指定文字和可选样式名的纯文本标签。"""
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if style:
        label.setObjectName(style)
    return label


class Handoffs(QWidget):
    """用同一套视图显示微信或邮件的待处理事项。"""

    def __init__(self, contacts, reply_db, heading, can_resolve):
        """接收联系人、可选账本路径、页标题和处理许可回调。"""
        super().__init__()
        self.contacts = contacts
        self.reply_db = reply_db
        self.heading = heading
        self.can_resolve = can_resolve
        self.items = {}
        self.current = None
        self.read_failed = {}
        self.transcript_version = None
        layout = QHBoxLayout(self)
        queue = QVBoxLayout()
        self.count = text_label('待我处理', 'secondary')
        queue.addWidget(self.count)
        self.list = QListWidget(objectName='handoffList')
        self.list.setAccessibleName('待处理会话')
        self.list.currentItemChanged.connect(lambda *_: self.render())
        queue.addWidget(self.list, 1)
        self.errors = text_label('', 'secondary')
        self.errors.setWordWrap(True)
        queue.addWidget(self.errors)
        layout.addLayout(queue, 1)

        details = QWidget(objectName='details')
        content = QVBoxLayout(details)
        content.setContentsMargins(32, 26, 32, 24)
        content.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(text_label(heading, 'page'))
        header.addStretch()
        self.status = text_label('', 'state')
        header.addWidget(self.status)
        content.addLayout(header)
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
        layout.addWidget(details, 3)

    def resizeEvent(self, event):
        """尺寸改变后按新宽度绘制截图。"""
        super().resizeEvent(event)
        QTimer.singleShot(0, self.render)

    def refresh(self):
        """刷新本频道事项。读取失败时保留旧项。"""
        items = {}
        failed = {}
        for name, simulate in self.contacts:
            if name == MAIL:
                continue
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
        try:
            if S_ISREG(db_path.stat().st_mode):
                store = ReplyStore(db_path)
                for name, simulate in self.contacts:
                    if not simulate:
                        items.update(self.reply_items(store, name))
        except FileNotFoundError:
            pass
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            failed['reply-db'] = '回复账本'
            for key, value in self.items.items():
                if value[1].get('store'):
                    items[key] = value
                    failed[key] = value[0]
        if 'reply-db' in failed:
            failed.update({key: value[0] for key, value in items.items() if value[1].get('store')})
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
        self.count.setText(f'{self.heading}  ·  {len(items)}')
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
            fallback = '请核对邮件' if name == MAIL else '请核对微信来信'
            items[key] = (name, {'store': store.path, 'id': turn['id'], 'part': None,
                                 'reason': turn['reason'], 'request': {'text': '\n'.join(lines) or fallback},
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
        """显示选中事项，保护只读草稿及读取失败的事项。"""
        item = self.list.currentItem()
        self.current = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        selected = self.items.get(self.current)
        self.status.setVisible(bool(selected))
        self.transcript.setVisible(bool(selected))
        self.resume.setEnabled(bool(selected) and self.current not in self.read_failed
                               and self.can_resolve())
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
        """显示纯文本和可选 PNG，按预览区宽度缩放。"""
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
        """核对选中事项版本后完成处理。"""
        if not self.can_resolve() or self.current is None or self.current in self.read_failed:
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


class MainWindow(QMainWindow):
    """在同一个可隐藏的窗口中显示微信、邮件和电脑助手。"""

    def __init__(self, contacts, app, reply_db=None, *, listener=None, worker=None, show_tray=True):
        """接收应用、联系人和账本。离屏测试可注入进程替身并关闭托盘。"""
        super().__init__()
        self.app = app
        self.tray_count = None
        self.runner = listener if listener is not None else Runner(self)
        self.worker = worker if worker is not None else ComputerRunner(self)
        self.action_overlay = ActionOverlay(app)
        self.destroyed.connect(self.action_overlay.deleteLater)
        self.session = Session(self.runner, self.worker, self.show_computer_state, self.computer_finished)
        self.setObjectName('mainWindow')
        self.setStyleSheet(STYLE)
        self.setWindowTitle('端侧盒子')
        self.setWindowIcon(box_icon())
        self.resize(1100, 650)
        self.setMinimumSize(640, 460)
        main = QWidget()
        layout = QHBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(main)

        sidebar = QWidget(objectName='sidebar')
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(20, 25, 16, 20)
        nav.addWidget(text_label('端侧盒子', 'brand'))
        self.navigation = QListWidget(objectName='navigation')
        self.navigation.addItems(['微信', '邮件', '电脑助手'])
        nav.addWidget(self.navigation, 1)
        self.settings_button = QPushButton('模型设置', objectName='plain')
        self.settings_button.clicked.connect(self.open_settings)
        nav.addWidget(self.settings_button)
        layout.addWidget(sidebar)

        self.pages = QStackedWidget(objectName='pages')
        layout.addWidget(self.pages, 1)
        self.pages.addWidget(self.build_wechat([(name, simulate) for name, simulate in contacts if name != MAIL],
                                              reply_db))
        self.pages.addWidget(self.build_mail(reply_db))
        self.computer_page = ComputerPage(self.start_computer, self.session.stop, self.restore_listener)
        self.pages.addWidget(self.computer_page)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)

        self.tray = QSystemTrayIcon(box_icon(), self)
        self.menu = QMenu(self)
        self.open_action = self.menu.addAction('打开端侧盒子', self.open_window)
        self.listen_action = self.menu.addAction('开始自动回复', self.toggle_listen)
        self.computer_action = self.menu.addAction('电脑助手', self.open_computer)
        self.computer_stop = self.menu.addAction('停止电脑助手', self.session.stop)
        self.menu.addAction('退出端侧盒子', self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray)
        if show_tray:
            self.tray.show()
        self.runner.changed.connect(self.show_runner)
        self.worker.event.connect(self.apply_computer_event)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(900)
        self.refresh()
        self.computer_page.check_display(self.app.screens())
        self.show_computer_state()

    def build_wechat(self, contacts, reply_db):
        """返回包含监听状态、开关、最近动态和微信事项的页面。"""
        page = QWidget(objectName='wechatPage')
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.run_state = text_label('未在监听', 'run')
        self.run_state.setWordWrap(True)
        row.addWidget(self.run_state, 1)
        self.whitelist_button = QPushButton('回复名单', objectName='plain')
        self.whitelist_button.clicked.connect(self.open_whitelist)
        row.addWidget(self.whitelist_button)
        self.toggle = QPushButton('开始自动回复', objectName='listenToggle')
        self.toggle.clicked.connect(self.toggle_listen)
        row.addWidget(self.toggle)
        layout.addLayout(row)
        self.recent = text_label('', 'recent')
        self.recent.setWordWrap(True)
        layout.addWidget(self.recent)
        self.wechat_items = Handoffs(contacts, reply_db, '待我处理', self.can_resolve)
        layout.addWidget(self.wechat_items, 1)
        return page

    def build_mail(self, reply_db):
        """返回包含 QQ 邮箱开关、打开入口和邮件待核对的页面。"""
        page = QWidget(objectName='mailPage')
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.mail = QCheckBox('同时回复 QQ 邮箱', objectName='mailToggle')
        self.mail.setChecked(bool(trigger.load_allowed_mail()))
        self.mail.setToolTip('要先点"打开邮箱窗口"并登录测试邮箱。改了要重新开始才生效。')
        row.addWidget(self.mail, 1)
        self.mail_window = QPushButton('打开邮箱窗口', objectName='plain')
        self.mail_window.clicked.connect(self.open_mail)
        row.addWidget(self.mail_window)
        layout.addLayout(row)
        self.mail_feedback = text_label('', 'secondary')
        self.mail_feedback.setWordWrap(True)
        layout.addWidget(self.mail_feedback)
        self.mail_items = Handoffs([(MAIL, False)], reply_db, '邮件待核对', self.can_resolve)
        layout.addWidget(self.mail_items, 1)
        return page

    def open_whitelist(self):
        """打开回复名单对话框（非模态：读取时要先藏起来，模态对话框一藏 exec 就返回了）。
        自动回复或电脑助手在跑时，对话框里只能看、不能读取和保存。"""
        from .whitelist_dialog import WhitelistDialog
        if getattr(self, 'whitelist_dialog', None) is not None and self.whitelist_dialog.isVisible():
            self.whitelist_dialog.raise_()
            return
        self.whitelist_dialog = WhitelistDialog(
            lambda: not self.runner.running() and not self.runner.stopping and self.can_resolve(),
            self.hide, self.show, None)
        self.whitelist_dialog.setStyleSheet(STYLE)
        self.whitelist_dialog.show()

    def can_resolve(self):
        """助手忙或托盘正在退出时返回 False。"""
        return not self.session.busy and self.session.quit_callback is None

    def toggle_listen(self):
        """从按钮或托盘菜单开始或停止监听。"""
        if not self.toggle.isEnabled():
            return
        if self.runner.running():
            self.runner.stop()
        else:
            self.runner.start(['--mail'] if self.mail.isChecked() else [])

    def show_runner(self):
        """同步监听状态、按钮、邮箱开关和最近动态。"""
        running = self.runner.running()
        blocked = not self.can_resolve() or self.session.can_restore
        label = '停止自动回复' if running else '开始自动回复'
        self.run_state.setText(self.runner.status)
        self.toggle.setText(label)
        self.listen_action.setText(label)
        can_toggle = not (self.runner.stopping or blocked)
        self.toggle.setEnabled(can_toggle)
        self.listen_action.setEnabled(can_toggle)
        self.mail.setEnabled(not running and not blocked and bool(trigger.load_allowed_mail()))
        self.recent.setText('\n'.join(self.runner.recent))
        self.update_tray_tooltip()

    def start_computer(self, task):
        """隐藏主窗口后提交任务，让它不遮挡截图。"""
        if self.can_resolve():
            self.hide()
            self.session.start(task)

    def open_computer(self):
        """从托盘打开主窗口并切到电脑助手页。"""
        if self.can_resolve():
            self.navigation.setCurrentRow(2)
            self.open_window()

    def show_computer_state(self):
        """同步助手按钮和自动回复入口。"""
        if self.session.state == 'running':
            self.action_overlay.start()
            self.worker.overlay_sync = self.action_overlay.isVisible()
        else:
            self.action_overlay.finish()
        can_open = self.can_resolve()
        self.open_action.setEnabled(can_open)
        self.computer_action.setEnabled(can_open)
        self.computer_page.set_state(can_open, self.session.state in ('waiting', 'running'),
                                     self.session.can_restore)
        self.show_runner()

    def apply_computer_event(self, event):
        """把截图握手交给效果层，把其余步骤交给回看页。"""
        name = event['event']
        was_visible = self.action_overlay.isVisible()
        self.action_overlay.apply_event(event)
        if name == 'capturing':
            n, run_dir = event['n'], self.worker.run_dir
            if was_visible:
                # 等桌面刷新，避免确认文件先于透明窗口消失。
                QTimer.singleShot(60, self, lambda: self.worker.acknowledge_overlay_hidden(n, run_dir))
            else:
                self.worker.acknowledge_overlay_hidden(n, run_dir)
        elif name != 'captured':
            self.computer_page.apply_event(event)
            self.update_tray_tooltip()

    def update_tray_tooltip(self, *_):
        """刷新任务进度和总用时，空闲时显示监听状态和待处理数。"""
        if self.session.busy:
            status = {
                'waiting': '正在停止自动回复…',
                'cancelled': '已取消启动，等待监听退出…',
                'running': self.computer_page.progress or '正在启动…',
                'stopping': '正在停止…',
            }[self.session.state]
            elapsed = f'{self.session.elapsed():.1f} 秒'
            self.computer_page.status.setText(f'{status} · 总用时 {elapsed}')
            self.tray.setToolTip(f'电脑助手运行中 · {status} · {elapsed}')
        else:
            status = '待恢复自动回复' if self.session.can_restore else self.runner.status
            self.tray.setToolTip(f'端侧盒子 · {status} · {self.tray_count or 0} 项待处理')
        self.computer_stop.setEnabled(self.session.state in ('waiting', 'running'))

    def computer_finished(self, result, detail, wait_seconds, task_seconds):
        """显示助手结果和最后一步。"""
        self.computer_page.show_result(result, detail, wait_seconds, task_seconds)
        self.open_computer()

    def restore_listener(self):
        """用户确认后按原参数恢复监听，并显示恢复失败。"""
        try:
            self.session.restore()
            self.computer_page.restore_feedback.clear()
        except OSError as exc:
            self.computer_page.restore_feedback.setText(f'恢复自动回复失败（{type(exc).__name__}）')

    def open_settings(self):
        """显示原有模型设置对话框。"""
        ModelDialog(self).exec()

    def open_mail(self):
        """打开专用邮箱窗口，将结果显示在邮件页。"""
        self.mail_feedback.setText(open_mail_window())

    def quit(self):
        """先结束电脑助手，再停止监听并退出应用。"""
        self.session.quit(self.quit_listen)

    def quit_listen(self):
        """确认监听退出后关闭应用。"""
        self.action_overlay.finish()
        self.runner.stop(then=self.app.quit)

    def on_tray(self, reason):
        """单击或双击托盘时尝试显示主窗口。"""
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_window()

    def open_window(self):
        """助手空闲时刷新并唤起主窗口。"""
        if not self.can_resolve():
            return
        self.refresh()
        self.computer_page.check_display(self.app.screens())
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        """关闭按钮只隐藏窗口，让托盘和后台监听继续运行。"""
        self.hide()
        event.ignore()

    def refresh(self):
        """刷新两个频道的事项和托盘角标。"""
        self.wechat_items.refresh()
        self.mail_items.refresh()
        count = len(self.wechat_items.items) + len(self.mail_items.items)
        if count != self.tray_count:
            self.tray_count = count
            self.tray.setIcon(box_icon(count))
        self.update_tray_tooltip()
