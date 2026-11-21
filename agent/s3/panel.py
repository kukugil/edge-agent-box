"""电脑助手任务输入、步骤回看和点击位置。"""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from .protocol import describe_action, image_rect, marker_position


class Preview(QWidget):
    """将原始截图和落点画在同一绘制区域。"""

    def __init__(self):
        super().__init__()
        self.image = QPixmap()
        self.click = None
        self.screen = None
        self.setMinimumSize(280, 220)
        self.setAccessibleName('步骤截图')

    def set_step(self, step):
        """载入选中步骤的截图和原屏坐标，并触发重绘。"""
        self.image = QPixmap(step.get('shot', ''))
        self.click = step.get('click')
        self.screen = step.get('screen')
        self.update()

    def paintEvent(self, event):
        """按当前可用区域画截图，缩放后用红圈和中心点标记点击。"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#edf0f5'))
        if self.image.isNull():
            painter.setPen(QColor('#606875'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '暂无步骤截图')
            return
        size = (self.image.width(), self.image.height())
        area = self.contentsRect()
        rect = image_rect(size, (area.x(), area.y(), area.width(), area.height()))
        target = QRectF(*rect)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(target, self.image, QRectF(self.image.rect()))
        point = marker_position(self.click, self.screen or size, rect)
        if point is not None:
            center = QPointF(*point)
            red = QColor('#e22129')
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setClipRect(target)
            painter.setPen(QPen(red, 2))
            painter.drawEllipse(center, 18, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(red)
            painter.drawEllipse(center, 3, 3)


class Panel(QWidget):
    """独立电脑助手窗口。回调负责提交任务及停止，不在界面内管理进程。"""

    def __init__(self, start, stop):
        super().__init__()
        self.setWindowTitle('电脑助手')
        self.resize(1080, 640)
        self.items = {}
        self.steps = {}
        self.progress = ''
        layout = QHBoxLayout(self)
        left = QWidget()
        form = QVBoxLayout(left)
        self.task = QLineEdit()
        self.task.setPlaceholderText('输入要电脑完成的任务')
        self.task.setAccessibleName('电脑任务')
        self.task.returnPressed.connect(self._submit)
        self.start_button = QPushButton('开始')
        self.start_button.clicked.connect(self._submit)
        self.stop_button = QPushButton('停止')
        self.stop_button.clicked.connect(stop)
        self.stop_button.setEnabled(False)
        self.submit = start
        form.addWidget(self.task)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        form.addLayout(buttons)
        self.status = QLabel('等待任务')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        form.addWidget(self.status)
        self.list = QListWidget()
        self.list.setAccessibleName('操作步骤')
        self.list.currentItemChanged.connect(self._select)
        form.addWidget(self.list, 1)
        layout.addWidget(left, 2)
        self.preview = Preview()
        layout.addWidget(self.preview, 3)

    def _submit(self):
        """任务输入有效且空闲时，清空旧步骤并提交。"""
        task = self.task.text().strip()
        if not task or not self.start_button.isEnabled():
            return
        self.list.clear()
        self.steps.clear()
        self.items.clear()
        self.preview.set_step({})
        self.progress = ''
        self.submit(task)

    def apply_event(self, event):
        """把一步的思考、动作和耗时更新到同一张步骤卡。"""
        if event['event'] == 'start':
            return
        n = event['n']
        step = self.steps.setdefault(n, {})
        step.update(event)
        if event['event'] == 'thinking':
            self.progress = f'第 {n} 步 · 正在思考'
        elif event['event'] == 'executing':
            self.progress = f'第 {n} 步 · 正在执行'
        if n not in self.items:
            self.items[n] = QListWidgetItem()
            self.items[n].setData(Qt.ItemDataRole.UserRole, n)
            self.list.addItem(self.items[n])
        item = self.items[n]
        width = max(60, self.list.viewport().width() - 45)
        metrics = self.list.fontMetrics()
        plan = ' '.join(step.get('plan', '').split())
        first = metrics.elidedText(plan, Qt.TextElideMode.ElideRight, width)
        if first != plan and first.endswith('…'):
            first = first[:-1]
        second = metrics.elidedText(plan[len(first):].lstrip(), Qt.TextElideMode.ElideRight, width)
        action = describe_action(step['code']) if 'code' in step else '正在思考'
        if 'seconds' in step:
            action += f' · {step["seconds"]:.1f} 秒'
        item.setText(f'第 {n} 步\n{first}\n{second}\n{action}')
        item.setToolTip(plan)
        item.setSizeHint(QSize(0, metrics.lineSpacing() * 4 + 28))
        if self.list.currentItem() is item:
            self.preview.set_step(step)

    def _select(self, item, previous):
        """按当前高亮步骤加载截图。"""
        self.preview.set_step(self.steps[item.data(Qt.ItemDataRole.UserRole)] if item else {})

    def show_result(self, result, detail, elapsed):
        """显示本轮结果、选中最后一步并把回看窗放到前台。"""
        label = {'DONE': '模型报告完成，待核对实际结果', 'FAIL': '模型报告未完成',
                 'STEP_LIMIT': '已用完步数', 'ERROR': '运行出错', 'STOPPED': '已停止'}[result]
        self.status.setText(' · '.join(part for part in (label, detail if detail != label else '',
                                                        f'总用时 {elapsed:.1f} 秒') if part))
        if self.list.count():
            self.list.setCurrentRow(self.list.count() - 1)
        self.open()

    def open(self):
        """显示并激活电脑助手窗口。"""
        self.showNormal()
        self.raise_()
        self.activateWindow()
