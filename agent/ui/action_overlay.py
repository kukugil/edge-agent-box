"""用电脑助手的事件显示操作进度和落点。"""

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QWidget


_CLICKS = {
    'click': '点击', 'left_click': '点击', 'left_single': '点击',
    'left_double': '双击', 'double_click': '双击',
    'right_single': '右击', 'right_click': '右击',
}
_LABELS = {
    **_CLICKS, 'drag': '拖动', 'left_click_drag': '拖动', 'select': '拖动选取',
    'scroll': '滚动', 'type': '输入', 'hotkey': '快捷键', 'wait': '等待',
    'finished': '完成', 'call_user': '请求协助', 'error_env': '环境异常',
}
_DRAGS = ('drag', 'left_click_drag', 'select')
_ANIMATION_MS = 560


class ActionOverlay(QWidget):
    """在受支持的主屏显示效果，截图期间暂时隐藏。"""

    def __init__(self, app):
        """接收 Qt 应用以检查屏幕，创建独立于托盘主窗口的透明工具窗。"""
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.app = app
        self.active = False
        self.screen = None
        self.capture_step = None
        self.step = 1
        self.phase = 'thinking'
        self.action_label = ''
        self.animation = None
        self.elapsed = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._advance_animation)
        app.screenAdded.connect(self._disable_display)
        app.screenRemoved.connect(self._disable_display)

    def start(self):
        """开始一轮任务，在单屏 100% 缩放时显示初始状态。"""
        if self.active:
            return
        self.active = True
        self.step, self.phase, self.action_label = 1, 'thinking', ''
        screens = self.app.screens()
        if len(screens) != 1 or screens[0].devicePixelRatio() != 1:
            return
        self.screen = screens[0]
        self.screen.geometryChanged.connect(self._disable_display)
        self.screen.logicalDotsPerInchChanged.connect(self._disable_display)
        self.setGeometry(self.screen.geometry())
        self.show()

    def finish(self):
        """结束本轮并清理画面，后续事件在下次开始前失效。"""
        self.active = False
        self.capture_step = None
        self._disable_display()

    def _disable_display(self, *_):
        """屏幕变化或任务结束时撤下效果，避免使用旧的屏幕坐标。"""
        if self.screen is not None:
            self.screen.geometryChanged.disconnect(self._disable_display)
            self.screen.logicalDotsPerInchChanged.disconnect(self._disable_display)
            self.screen = None
        self._clear_animation()
        self.hide()

    @property
    def status_text(self):
        """返回显示在顶部的状态，只使用固定动作名。"""
        status = '正在思考' if self.phase == 'thinking' else '正在操作'
        action = f' · {self.action_label}' if self.action_label else ''
        return f'第 {self.step} 步 · {status}{action} · 托盘右键可停止'

    def apply_event(self, event):
        """用当前轮的步骤或截图事件更新状态和可见性。"""
        if not self.active:
            return
        name = event['event']
        if name == 'capturing':
            self.capture_step = event['n']
            self._clear_animation()
            self.hide()
        elif name == 'captured' and self.capture_step == event['n']:
            self.capture_step = None
            if self.screen is not None:
                self.show()
        elif name == 'thinking':
            self.step, self.phase, self.action_label = event['n'], 'thinking', ''
            self._clear_animation()
        elif name == 'executing':
            kind = event.get('kind')
            self.step, self.phase = event['n'], 'executing'
            self.action_label = _LABELS.get(kind, '动作')
            if self.isVisible():
                self._start_animation(kind, event)
        self.update()

    def _start_animation(self, kind, event):
        """从落点、终点或滚动方向建立短动画，缺少有效参数时只更新状态。"""
        self._clear_animation()
        point = event.get('target')
        direction = event.get('direction')
        end = event.get('end')
        if kind == 'scroll' and direction in ('up', 'down') and point is None:
            cursor = QCursor.pos()
            point = (cursor.x(), cursor.y())
        if point is None or not (kind in _CLICKS or (kind in _DRAGS and end is not None)
                                 or (kind == 'scroll' and direction in ('up', 'down'))):
            return
        origin = QPointF(self.geometry().topLeft())
        start = QPointF(*point) - origin
        finish = QPointF(*end) - origin if kind in _DRAGS else None
        self.animation = kind, start, finish, direction
        self.elapsed.start()
        self.timer.start()

    def _clear_animation(self):
        """丢弃当前标记并停止动画计时器。"""
        self.timer.stop()
        self.animation = None

    def _advance_animation(self):
        """请求下一帧，达到时长后清除动画并停止重绘计时。"""
        if self.elapsed.elapsed() >= _ANIMATION_MS:
            self._clear_animation()
        self.update()

    def paintEvent(self, event):
        """在透明画布画渐淡边框、顶部状态条和短动画。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        strength = 1 if self.phase == 'executing' else 0.55
        for inset in range(18, -1, -1):
            alpha = int((18 - inset) * 11 * strength)
            painter.setPen(QColor(57, 214, 255, alpha))
            painter.drawRect(self.rect().adjusted(inset, inset, -inset - 1, -inset - 1))

        label = self.status_text
        width = min(self.width(), painter.fontMetrics().horizontalAdvance(label) + 32)
        bar = QRectF((self.width() - width) / 2, 12, width, 34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(13, 29, 48, 230))
        painter.drawRoundedRect(bar, 9, 9)
        painter.setPen(QColor('#eafaff'))
        painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, label)
        if self.animation is not None:
            self._paint_animation(painter)

    def _paint_animation(self, painter):
        """按已过时间画一次圆圈、拖动轨迹或滚动箭头。"""
        kind, start, end, direction = self.animation
        progress = min(self.elapsed.elapsed() / _ANIMATION_MS, 1)
        color = QColor('#ffd375' if _CLICKS.get(kind) == '右击' else '#77eaff')
        color.setAlphaF(1 - progress)
        painter.setPen(QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind in _CLICKS:
            radius = 9 + 25 * progress
            painter.drawEllipse(start, radius, radius)
            if _CLICKS[kind] == '双击':
                painter.drawEllipse(start, radius + 8, radius + 8)
        elif kind in _DRAGS:
            tip = start + (end - start) * min(1, 2 * progress)
            painter.drawLine(start, tip)
            painter.drawEllipse(tip, 5, 5)
        else:
            sign = -1 if direction == 'up' else 1
            tip = start + QPointF(0, sign * (14 + 24 * progress))
            painter.drawLine(start, tip)
            for side in (-1, 1):
                painter.drawLine(tip, tip + QPointF(side * 8, -sign * 9))
