"""绘本纸面风格：配色、样式表、纸纹底、硬投影、手绘标题和卡片式列表项。

颜色和尺寸照搬已定稿的预览（docs/托盘视觉预览/第十七轮/转场.html 里的 #desk 变量）。
"""

import random

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QLabel, QStyle, QStyledItemDelegate, QWidget,
)

DESK = '#f6e6cd'
PAPER = '#f3eadb'
LIGHT = '#fffbf2'
INK = '#30271f'
NIGHT = '#5e514a'
MOSS = '#365d52'
PINK = '#f1d3c7'
ROSE = '#e6a999'
WOOD = '#d7a874'

STYLE = f"""
QWidget {{ color: {INK}; font-family: 'Microsoft YaHei UI'; font-size: 14px; }}
QDialog, QMainWindow {{ background: {DESK}; }}
QLabel {{ background: transparent; }}
QLabel#brand {{ font-size: 15px; font-weight: bold; letter-spacing: 1px; padding: 0 6px 12px 6px;
               border-bottom: 1px solid #8a725a66; }}
QLabel#title {{ font-size: 22px; font-weight: bold; }}
QLabel#heading {{ font-size: 21px; font-weight: 900; letter-spacing: 1px; }}
QLabel#secondary, QLabel#recent {{ color: {NIGHT}; }}
QLabel#recent {{ font-size: 12px; }}
QLabel#run {{ font-weight: bold; }}
QLabel#state {{ background: {PINK}; border: 1.5px solid {INK}; border-radius: 4px; padding: 3px 8px; }}
QLabel#badge {{ background: {ROSE}; border: 1.5px solid {INK}; border-radius: 5px; padding: 0 6px;
               font-weight: bold; min-width: 14px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QTextBrowser {{ background: {LIGHT}; border: 1.5px solid {INK}; border-radius: 6px; padding: 12px; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox {{
    background: {LIGHT}; border: 1.5px solid {INK}; border-radius: 5px; padding: 7px 9px;
    selection-background-color: {MOSS}; selection-color: {LIGHT}; }}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border: 2px solid {MOSS}; }}
QLineEdit:disabled {{ color: {NIGHT}; background: #efe6d6; }}
QPushButton {{ background: {MOSS}; color: {LIGHT}; border: 1.5px solid {INK}; border-radius: 5px;
               border-right-width: 3px; border-bottom-width: 3px; padding: 7px 14px; font-weight: bold; }}
QPushButton:hover {{ background: #2c4f45; }}
QPushButton:pressed {{ border-right-width: 1.5px; border-bottom-width: 1.5px; padding: 8px 15px 7px 16px; }}
QPushButton:disabled {{ background: #cfc3b1; color: #6d6257; border-color: #9a8b7a; }}
QPushButton#plain {{ background: {LIGHT}; color: {INK}; font-weight: normal; }}
QPushButton#plain:hover {{ background: #f3e7d2; }}
QPushButton#danger {{ background: {LIGHT}; color: #8b3a2e; border-color: #8b3a2e; }}
QPushButton#roomReturn {{ background: {LIGHT}; padding: 0; border-radius: 7px; }}
QCheckBox {{ spacing: 8px; font-weight: bold; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1.5px solid {INK}; border-radius: 3px;
                        background: {LIGHT}; }}
QCheckBox::indicator:checked {{ background: {MOSS}; }}
QCheckBox:disabled {{ color: {NIGHT}; }}
QTreeWidget {{ background: {LIGHT}; border: 1.5px solid {INK}; border-radius: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #c7b69c; border-radius: 4px; min-height: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; height: 0; }}
QMenu {{ background: {LIGHT}; border: 1.5px solid {INK}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {PINK}; color: {INK}; }}
QToolTip {{ background: {LIGHT}; color: {INK}; border: 1px solid {INK}; padding: 4px 6px; }}
"""


def sized(font, delta):
    """返回按像素加减字号后的字体。样式表给的是像素字号，pointSize 会是 -1，不能直接加减。"""
    font = QFont(font)
    if font.pixelSize() > 0:
        font.setPixelSize(max(9, font.pixelSize() + round(delta)))
    else:
        font.setPointSizeF(max(7, font.pointSizeF() + delta * .75))
    return font


class Slip(QFrame):
    """纸片框：自己画右下错位实色投影。
    不用 QGraphicsDropShadowEffect：界面整体放进书房画面缩放时，效果层不跟着缩放，会错位。"""

    def __init__(self, sidebar=False):
        """sidebar 为 True 时用木色渐变底。"""
        super().__init__(objectName='sidebar' if sidebar else 'slip')
        self.sidebar = sidebar

    def paintEvent(self, event):
        """先画墨色投影，再画纸面和描边。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -SHADOW - 1, -SHADOW - 1)
        radius = 6 if self.sidebar else 7
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(INK))
        painter.drawRoundedRect(rect.translated(SHADOW, SHADOW), radius, radius)
        if self.sidebar:
            fill = QLinearGradient(rect.topLeft(), rect.topRight())
            fill.setColorAt(0, QColor('#e1c7a9'))
            fill.setColorAt(1, QColor('#ecd9bd'))
            painter.setBrush(fill)
        else:
            painter.setBrush(QColor(PAPER))
        painter.setPen(QPen(QColor(INK), 1.5))
        painter.drawRoundedRect(rect, radius, radius)


SHADOW = 4


def slip(name='slip'):
    """返回带硬投影的纸片框；name 为 sidebar 时是侧栏木条。"""
    return Slip(name == 'sidebar')


def grain(size=144, seed=7):
    """生成一张可平铺的细纸纹，固定种子保证每次一样。"""
    rng = random.Random(seed)
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    for y in range(size):
        for x in range(size):
            value = rng.random()
            if value > .62:
                image.setPixelColor(x, y, QColor(48, 39, 31, int((value - .62) * 34)))
    return QPixmap.fromImage(image)


# 空中小光点的位置（占宽高比例）和大小，照预览。
DUST = ((.17, .07, 4), (.64, .09, 6), (.82, .06, 4), (.955, .45, 5), (.03, .68, 5), (.69, .91, 4), (.92, .87, 6))


class Desk(QWidget):
    """主界面底板：米色屏幕底、纸纹和几颗静止的小光点。"""

    def __init__(self):
        """创建底板并预先生成纸纹。"""
        super().__init__(objectName='desk')
        self.tile = grain()

    def paintEvent(self, event):
        """先铺底色和纸纹，再画光点。"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(DESK))
        painter.drawTiledPixmap(self.rect(), self.tile)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(177, 138, 91, 117), 1))
        painter.setBrush(QColor('#fff9dd'))
        for x, y, size in DUST:
            painter.drawEllipse(QPointF(x * self.width(), y * self.height()), size / 2, size / 2)


class Heading(QLabel):
    """粗体页标题，下面一道手绘笔触。"""

    def __init__(self, text):
        """用纯文本创建标题。"""
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setObjectName('heading')
        self.setContentsMargins(0, 0, 0, 10)

    def paintEvent(self, event):
        """画字，再在字下画一道两笔的不规则下划线。"""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#705b49'), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        width = min(self.width() - 4, self.fontMetrics().horizontalAdvance(self.text()) + 10)
        base = self.height() - 7
        sx = width / 150
        path = QPainterPath(QPointF(2 + 3 * sx, base + 3))
        path.quadTo(QPointF(2 + 32 * sx, base - 2), QPointF(2 + 69 * sx, base + 1))
        path.quadTo(QPointF(2 + 106 * sx, base + 4), QPointF(2 + 144 * sx, base - 1))
        path.moveTo(QPointF(2 + 36 * sx, base + 4))
        path.quadTo(QPointF(2 + 60 * sx, base + 2), QPointF(2 + 93 * sx, base + 3))
        painter.drawPath(path)


def draw_clip(painter, x, y, angle):
    """在 (x, y) 画一枚小木夹，angle 为倾斜角度。"""
    painter.save()
    painter.translate(x, y)
    painter.rotate(angle)
    painter.setPen(QPen(QColor(INK), 1.3))
    painter.setBrush(QColor(WOOD))
    painter.drawRoundedRect(QRectF(-15, -7, 30, 13), 3, 3)
    painter.setBrush(QColor('#b98455'))
    painter.drawRect(QRectF(-4, -9, 8, 17))
    painter.restore()


class CardDelegate(QStyledItemDelegate):
    """把列表项画成纸片。kind：tab 为侧栏书签，ticket 为带木夹的签条，step 为步骤便签。"""

    def __init__(self, kind, parent=None):
        """记下列表项的样式种类。"""
        super().__init__(parent)
        self.kind = kind

    def sizeHint(self, option, index):
        """书签和签条用固定高度；步骤沿用列表项自己给的高度。"""
        if self.kind == 'tab':
            return QSize(0, 52)
        if self.kind == 'ticket':
            return QSize(0, 96)
        hint = index.data(Qt.ItemDataRole.SizeHintRole)
        return QSize(0, (hint.height() if hint else 90) + 12)

    def paint(self, painter, option, index):
        """画硬投影、纸片、选中色和文字。"""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        top = 14 if self.kind == 'ticket' else 5
        right = 4 if self.kind == 'tab' else 8
        rect = QRectF(option.rect).adjusted(2, top, -right, -7)
        radius = 8 if self.kind == 'tab' else 6
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(INK))
        shadow = 2 if self.kind == 'tab' else 3
        painter.drawRoundedRect(rect.translated(shadow, shadow), radius, radius)
        if selected:
            fill = MOSS if self.kind == 'tab' else PINK
        else:
            fill = '#f7eedd' if hovered else LIGHT
        painter.setBrush(QColor(fill))
        painter.setPen(QPen(QColor(INK), 1.5))
        painter.drawRoundedRect(rect, radius, radius)
        lines = (index.data(Qt.ItemDataRole.DisplayRole) or '').split('\n')
        text = rect.adjusted(16, 8, -14, -8)
        if self.kind == 'tab':
            font = QFont(option.font)
            font.setBold(selected)
            painter.setFont(font)
            painter.setPen(QColor(LIGHT if selected else INK))
            painter.drawText(text, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, lines[0])
        elif self.kind == 'ticket':
            # 签条左侧一道虚线，像撕下来的票根。
            painter.setPen(QPen(QColor(NIGHT), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(rect.left() + 10, rect.top() + 4), QPointF(rect.left() + 10, rect.bottom() - 4))
            draw_clip(painter, rect.left() + 30, rect.top() - 2, -8 if index.row() % 2 == 0 else 6)
            self._lines(painter, option.font, text.adjusted(8, 8, 0, 0), lines, bold_first=True)
        else:
            self._lines(painter, option.font, text, lines, small_first=True)
        painter.restore()

    def _lines(self, painter, base, rect, lines, bold_first=False, small_first=False):
        """逐行画文字：首行加粗或缩小，其余次要行用灰色。"""
        y = rect.top()
        for number, line in enumerate(lines):
            font = QFont(base)
            color = QColor(INK)
            if number == 0 and bold_first:
                font = sized(base, 2)
                font.setBold(True)
            elif number == 0 and small_first or (number and bold_first) or number == len(lines) - 1 and small_first:
                color = QColor(NIGHT)
                font = sized(base, -1)
            painter.setFont(font)
            painter.setPen(color)
            height = painter.fontMetrics().lineSpacing()
            painter.drawText(QRectF(rect.left(), y, rect.width(), height),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, int(rect.width())))
            y += height + (4 if number == 0 else 1)
