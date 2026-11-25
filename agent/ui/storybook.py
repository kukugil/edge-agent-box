"""书房场景：主界面放在画里的电脑屏幕上，点电脑拉近，拖动或晃动光标看视差。

镜头参数照搬已定稿的预览（docs/托盘视觉预览/第十七轮/转场.html），改这里前先在预览里试手感。
"""

import ctypes
import math
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (
    QFrame, QGraphicsBlurEffect, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
)

ASSETS = Path(__file__).resolve().parent.parent / 'assets' / 'storybook'
WIDTH, HEIGHT = 1280, 760
# 画中屏幕和整台显示器在 1280×760 原图里的位置。
SCREEN = QRectF(411, 128, 481, 243)
MONITOR = QRectF(399, 114, 507, 317)
PREP, ZOOM_IN, ZOOM_OUT, FADE, LAYER_DELAY = .1, 1.1, 1.0, .2, .05
PAPER = QColor('#f6e6cd')


def clamp(p):
    """把数限制在 0 到 1。"""
    return max(0.0, min(1.0, p))


def mix(a, b, p):
    """在 a、b 间按 p 线性取值。"""
    return a + (b - a) * p


def smooth(p):
    """smoothstep：两端速度为零的 S 形。"""
    return p * p * (3 - 2 * p)


def segment(p, a, b):
    """p 在 a 到 b 间时平滑地从 0 走到 1。"""
    return smooth(clamp((p - a) / (b - a)))


def spring(p):
    """拖动松手后的临界附近阻尼回弹，p 为 0 到 1 的进度，返回剩余比例。"""
    if p >= 1:
        return 0.0
    return math.exp(-6.7 * p) * (math.cos(9 * p) + .30 * math.sin(9 * p))


def camera_ease(p):
    """cubic-bezier(0.65, 0, 0.35, 1)：先按横坐标二分求参数，纵坐标恰好是 smoothstep。"""
    if p <= 0 or p >= 1:
        return clamp(p)
    low, high = 0.0, 1.0
    for _ in range(22):
        u = (low + high) / 2
        v = 1 - u
        if 3 * v * v * u * .65 + 3 * v * u * u * .35 + u * u * u < p:
            low = u
        else:
            high = u
    return smooth((low + high) / 2)


def motion_at(seconds, inward, cross=False):
    """返回镜头在第 seconds 秒的状态。portion 为 0 是房间、1 是全屏；cross 为减少动态效果的淡入淡出。"""
    if cross:
        p = clamp(seconds / FADE)
        fade = p if inward else 1 - p
        return {'portion': fade, 'preparation': 0, 'fade': fade, 'cross': True,
                'near': 0, 'far': 0, 'active': 0 < fade < 1}
    prep = PREP if inward else 0
    duration = ZOOM_IN if inward else ZOOM_OUT
    elapsed = seconds - prep
    progress = 1.0 if elapsed >= duration - 1e-9 else clamp(elapsed / duration)
    portion = progress if inward else 1 - progress
    preparation = smooth(clamp(seconds / prep)) if inward and prep else 0
    fade = segment(elapsed, duration - FADE, duration) if inward else 1 - segment(seconds, 0, FADE)
    # 前景先走半拍，窗外后走半拍，端点仍回到完整图层。
    near = clamp((elapsed + LAYER_DELAY) / duration)
    far = clamp((elapsed - LAYER_DELAY) / duration)
    if not inward:
        near, far = 1 - near, 1 - far
    active = seconds > 0 and progress < 1
    return {'portion': portion, 'preparation': preparation, 'fade': fade, 'cross': False,
            'near': near if active else portion, 'far': far if active else portion, 'active': active}


def reduced_motion():
    """读取 Windows"显示动画"设置；关掉时返回 True。读不到按开启处理。"""
    try:
        enabled = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return not enabled.value
    except (AttributeError, OSError):
        pass
    return False


class RoomView(QGraphicsView):
    """房间画面。mode 为 room、travel 或 screen；只有 screen 时界面能点。"""

    modeChanged = Signal(str)

    def __init__(self, ui, reduced=None):
        """ui 是整个主界面控件，会被放进画中屏幕；reduced 可注入"减少动态效果"判断，测试用。"""
        super().__init__()
        self.reduced = reduced or reduced_motion
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setBackgroundBrush(QColor('#668d81'))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('书房：点电脑打开端侧盒子')
        scene = QGraphicsScene(self)
        self.setScene(scene)

        # 三层分开画：窗外在最底下，房间层的窗玻璃是透明的，前景是门和两盆大叶子。
        # 被前景挡住的墙和地板在房间层里补画完整，窗外比窗洞大一圈，所以怎么晃都不露底、不断线。
        self.outside = scene.addPixmap(QPixmap(str(ASSETS / 'far.webp')))
        self.blur = QGraphicsBlurEffect()
        self.blur.setBlurRadius(0)
        self.outside.setGraphicsEffect(self.blur)
        self.room = scene.addPixmap(QPixmap(str(ASSETS / 'room.webp')))
        for item in (self.room, self.outside):
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        # 屏幕纸面：窗口比例和画中屏幕不同时，界面四周用同色补齐。
        self.backing = QGraphicsRectItem(SCREEN)
        self.backing.setBrush(PAPER)
        self.backing.setPen(Qt.PenStyle.NoPen)
        scene.addItem(self.backing)
        self.ui = ui
        self.proxy = scene.addWidget(ui)
        self.near = scene.addPixmap(QPixmap(str(ASSETS / 'near.webp')))
        self.near.setTransformationMode(Qt.TransformationMode.SmoothTransformation)

        self.mode = 'room'
        self.frame = motion_at(0, True)
        self.offset = QPointF()        # 拖动偏移，单位是原图像素
        self.hover = QPointF()         # 光标跟随偏移
        self.hover_target = QPointF()
        self.drag = None
        self.animation = None          # (开始计时器, 时长, 每帧回调)
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.follow_clock = QElapsedTimer()
        self.follow = QTimer(self)
        self.follow.setInterval(16)
        self.follow.timeout.connect(self._follow_tick)
        self._set_mode('room')

    # 外部接口 -------------------------------------------------------------

    def show_room(self, animated=True):
        """拉远回到房间；animated 为 False 时直接切换。"""
        if not animated or self.mode == 'room':
            self._stop_animation()
            self.render_frame(motion_at(0, True))
            return
        self._animate(False)

    def show_screen(self, animated=True):
        """拉近到屏幕；animated 为 False 时直接切换（从托盘菜单打开电脑助手时用）。"""
        if not animated:
            self._stop_animation()
            self.follow.stop()
            self.hover = self.hover_target = QPointF()
            self.offset = QPointF()
            self.render_frame(motion_at(10, True))
            return
        if self.mode != 'room' or self.animation is not None:
            return
        self.follow.stop()
        self.hover_target = QPointF()
        if self.drag is None and (abs(self.hover.x()) + abs(self.hover.y()) > .02 or not self.offset.isNull()):
            # 先把偏移收回，再从原始机位开始拉近，路线和预览一致。
            start_hover, start_offset = QPointF(self.hover), QPointF(self.offset)

            def recenter(seconds):
                factor = 1 - smooth(clamp(seconds / .16))
                self.hover, self.offset = start_hover * factor, start_offset * factor
                self.render_frame(motion_at(0, True))
                if seconds >= .16:
                    self.hover = self.offset = QPointF()
                    self._animate(True)
                    return False
                return True
            self._run(recenter)
            return
        self._animate(True)

    # 渲染 -----------------------------------------------------------------

    def render_frame(self, frame=None):
        """按镜头状态和视差偏移摆放各图层与界面。"""
        if frame is not None:
            self.frame = frame
        frame = self.frame
        vw, vh = self.viewport().width(), self.viewport().height()
        if vw <= 0 or vh <= 0:
            return
        self.setSceneRect(0, 0, vw, vh)
        if self.ui.size().width() != vw or self.ui.size().height() != vh:
            self.ui.resize(vw, vh)
        cross, fade, portion = frame['cross'], frame['fade'], frame['portion']
        shift = self.hover if not self.hover.isNull() else self.offset
        ox, oy = shift.x(), shift.y()
        cx, cy = SCREEN.center().x(), SCREEN.center().y()
        sw, sh = SCREEN.width(), SCREEN.height()
        margin = max(4 * abs(ox) / WIDTH, 4 * abs(oy) / HEIGHT)
        # 预留 2% 画幅，预备动作往回缩时也不露底色。
        fit = max(vw / WIDTH, vh / HEIGHT) / .98 * (1 + margin)
        start_x = (vw - WIDTH * fit) / 2 + ox * fit + cx * fit
        start_y = (vh - HEIGHT * fit) / 2 + oy * fit + cy * fit
        begin = fit * (1 - .02 * frame['preparation'])
        cover = max(vw / sw, vh / sh)
        travel = 0 if cross else camera_ease(portion)
        # 按倍数插值：每帧放大的比例相同，人眼看起来匀速。
        scale = math.exp(mix(math.log(begin), math.log(cover), travel))
        ccx, ccy = mix(start_x, vw / 2, travel), mix(start_y, vh / 2, travel)
        camera = QTransform(scale, 0, 0, scale, ccx - cx * scale, ccy - cy * scale)

        self.room.setTransform(camera)
        front, far = camera_ease(frame['near']), camera_ease(frame['far'])
        zoom = 1 + .7 * front
        self.near.setTransform(QTransform(zoom, 0, 0, zoom, (1 - zoom) * cx + ox * .85,
                                          (1 - zoom) * cy + oy * .85) * camera)
        lag = 1 - .012 * far
        self.outside.setTransform(QTransform(lag, 0, 0, lag, far * 8 - ox * .45, far * 5 - oy * .45) * camera)
        self.blur.setBlurRadius(segment(far, .10, .75) * 1.6 * scale)

        # 界面按窗口尺寸排版，再等比放进画中屏幕；拉满时净缩放正好为 1，字不会糊。
        full = fade == 1 or (cross and fade > 0)
        if full:
            self.backing.setRect(QRectF(0, 0, vw, vh))
            self.backing.setTransform(QTransform())
            self.proxy.setTransform(QTransform())
            self.proxy.setOpacity(fade)
            self.backing.setOpacity(fade)
        else:
            k = min(sw / vw, sh / vh)
            local = QTransform(k, 0, 0, k, cx - vw * k / 2, cy - vh * k / 2)
            self.backing.setRect(SCREEN)
            self.backing.setTransform(camera)
            self.proxy.setTransform(local * camera)
            self.proxy.setOpacity(1)
            self.backing.setOpacity(1)
        if fade == 1 and portion == 1:
            self._set_mode('screen')
        elif portion == 0 and not frame['active'] and fade == 0:
            self._set_mode('room')
        else:
            self._set_mode('travel')

    def _set_mode(self, mode):
        """切换模式：只有屏幕模式下界面可交互、能拿焦点。"""
        changed = mode != self.mode
        self.mode = mode
        interactive = mode == 'screen'
        self.proxy.setEnabled(True)
        self.proxy.setAcceptedMouseButtons(Qt.MouseButton.AllButtons if interactive else Qt.MouseButton.NoButton)
        self.proxy.setAcceptHoverEvents(interactive)
        if not interactive and self.proxy.hasFocus():
            self.proxy.clearFocus()
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        if changed:
            self.modeChanged.emit(mode)

    def resizeEvent(self, event):
        """窗口尺寸变化后重新摆放。"""
        super().resizeEvent(event)
        self.render_frame()

    def showEvent(self, event):
        """首次显示时按实际尺寸摆放。"""
        super().showEvent(event)
        self.render_frame()

    # 动画 -----------------------------------------------------------------

    def _run(self, step):
        """启动逐帧回调；step(秒) 返回 False 时结束。"""
        self.animation = step
        self.clock.start()
        self.timer.start()

    def _tick(self):
        """推进当前动画一帧。"""
        step = self.animation
        if step is None:
            self.timer.stop()
            return
        # 回调里可能接着启动下一段动画（先回正再拉近），那时不能清掉新的。
        if not step(self.clock.elapsed() / 1000) and self.animation is step:
            self.animation = None
            self.timer.stop()

    def _stop_animation(self):
        """停掉正在进行的镜头或回弹。"""
        self.animation = None
        self.timer.stop()

    def _animate(self, inward):
        """播放拉近或拉远。"""
        cross = self.reduced()
        duration = FADE if cross else (PREP + ZOOM_IN if inward else ZOOM_OUT)

        def step(seconds):
            seconds = min(duration, seconds)
            self.render_frame(motion_at(seconds, inward, cross))
            if seconds >= duration:
                self._finish()
                return False
            return True
        self._run(step)

    def _finish(self):
        """动画结束：停计时器；回到房间后恢复光标跟随。"""
        self.animation = None
        self.timer.stop()
        if self.mode == 'room':
            self._update_hover(self.mapFromGlobal(self.cursor().pos()))

    def _follow_tick(self):
        """光标跟随：按经过时间向目标靠近，刷新率变化时速度不变。"""
        if not self._can_follow():
            self.follow.stop()
            return
        dt = self.follow_clock.restart()
        tau = 170 if self.hover_target.isNull() else 130
        alpha = 1 - math.exp(-max(0, dt) / tau)
        self.hover = self.hover + (self.hover_target - self.hover) * alpha
        delta = self.hover - self.hover_target
        if abs(delta.x()) + abs(delta.y()) <= .02:
            self.hover = QPointF(self.hover_target)
            self.follow.stop()
        self.render_frame(motion_at(0, True))

    def _can_follow(self):
        """只在房间里、没拖动、没动画、没开减少动态效果时跟随。"""
        return self.mode == 'room' and self.drag is None and self.animation is None and not self.reduced()

    def _update_hover(self, pos):
        """按光标相对窗口中心的位置设跟随目标，幅度是拖动上限的三分之一。"""
        vw, vh = self.viewport().width(), self.viewport().height()
        if not vw or not vh:
            return
        x, y = 2 * pos.x() / vw - 1, 2 * pos.y() / vh - 1
        inside = -1 <= x <= 1 and -1 <= y <= 1
        self.hover_target = QPointF(x * 13 / 3, y * 9 / 3) if inside else QPointF()
        if self._can_follow() and not self.follow.isActive():
            self.follow_clock.start()
            self.follow.start()

    def _on_monitor(self, pos):
        """视口坐标是否落在画中显示器上。"""
        return self.room.transform().mapRect(MONITOR).contains(QPointF(pos))

    # 鼠标和键盘 -----------------------------------------------------------

    def mousePressEvent(self, event):
        """屏幕模式交给界面；房间里点电脑拉近，点别处开始拖动。"""
        if self.mode == 'screen':
            super().mousePressEvent(event)
            return
        if self.mode != 'room' or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._on_monitor(event.position()):
            self.show_screen()
            return
        if self.reduced():
            return
        self.follow.stop()
        # 拖动从当前看到的位置接着走，避免跟随偏移突然清零。
        if not self.hover.isNull():
            self.offset, self.hover, self.hover_target = QPointF(self.hover), QPointF(), QPointF()
        self._stop_animation()
        self.drag = (event.position(), QPointF(self.offset))
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        """拖动时转视角；否则更新光标跟随和手形光标。"""
        if self.mode == 'screen':
            super().mouseMoveEvent(event)
            return
        pos = event.position()
        if self.drag is not None:
            start, base = self.drag
            fit = max(self.viewport().width() / WIDTH, self.viewport().height() / HEIGHT)
            limit = lambda n, top: max(-top, min(top, n))
            self.offset = QPointF(limit(base.x() + (pos.x() - start.x()) * .11 / fit, 13),
                                  limit(base.y() + (pos.y() - start.y()) * .09 / fit, 9))
            self.render_frame(motion_at(0, True))
            return
        if self.mode == 'room':
            hand = self._on_monitor(pos)
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hand else Qt.CursorShape.ArrowCursor)
            self._update_hover(pos)

    def mouseReleaseEvent(self, event):
        """松手后弹簧回正，回正后恢复跟随。"""
        if self.mode == 'screen':
            super().mouseReleaseEvent(event)
            return
        if self.drag is None:
            return
        self.drag = None
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        start = QPointF(self.offset)

        def settle(seconds):
            self.offset = start * spring(clamp(seconds / .73))
            self.render_frame(motion_at(0, True))
            if seconds >= .73:
                self.offset = QPointF()
                self._finish()
                return False
            return True
        self._run(settle)

    def mouseDoubleClickEvent(self, event):
        """房间里双击当作单击，免得被吞掉。"""
        if self.mode == 'screen':
            super().mouseDoubleClickEvent(event)
        else:
            self.mousePressEvent(event)

    def wheelEvent(self, event):
        """房间里不响应滚轮。"""
        if self.mode == 'screen':
            super().wheelEvent(event)

    def leaveEvent(self, event):
        """光标离开窗口后慢慢回正。"""
        super().leaveEvent(event)
        if self.mode == 'room' and self.drag is None:
            self.hover_target = QPointF()
            if self._can_follow() and not self.follow.isActive():
                self.follow_clock.start()
                self.follow.start()

    def keyPressEvent(self, event):
        """房间里回车或空格打开电脑；屏幕模式交给界面。"""
        if self.mode == 'screen':
            super().keyPressEvent(event)
        elif self.mode == 'room' and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.show_screen()
