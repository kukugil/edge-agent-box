"""屏幕模式的窗光、插画、切页、飞叶与藤蔓。"""

from math import atan2, cos, hypot, pi, sin
from random import Random

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QPolygonF, QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QGraphicsItem, QGraphicsRectItem, QLabel, QLineEdit, QListWidget,
    QTextBrowser, QWidget,
)
from shiboken6 import isValid

from . import leaf_flight, paper
from .growing_vine import GROWTH, SETTLE, Vine, clear_path, passive
from .storybook import ASSETS

LEAF_ART = ((63, 390, 452, 5, 415, 394), (75, 389, 452, 4, 415, 388),
            (23, 335, 452, 4, 353, 390), (26, 352, 451, 4, 359, 395),
            (32, 357, 452, 5, 376, 394), (5, 287, 452, 4, 299, 381),
            (52, 315, 451, 4, 367, 366))
RATIOS = tuple(art[4] / 456 for art in LEAF_ART)
PAGE_ART = ('wechat', 'mail', 'assistant')
STAMPS = ('letter-stamp', 'postbox-stamp', 'keyboard-stamp')
GEOMETRY_EVENTS = {QEvent.Type.LayoutRequest, QEvent.Type.Resize, QEvent.Type.Move,
                   QEvent.Type.Show, QEvent.Type.Hide}


def svg_pixmap(name):
    """把藤叶 SVG 按素材坐标系预渲染成供场景重复使用的位图。"""
    renderer = QSvgRenderer(str(ASSETS / 'vine-leaves' / (name + '.svg')))
    pixmap = QPixmap(renderer.defaultSize())
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


class Layer(QGraphicsRectItem):
    """以屏幕场景矩形为边界、允许输入穿透的绘制层。"""

    def __init__(self, parent, z):
        """将空绘制层放在 parent 下的指定堆叠高度。"""
        super().__init__(parent)
        passive(self)
        self.setPen(Qt.PenStyle.NoPen)
        self.setZValue(z)


class Leaves(Layer):
    """切页时分批筛选不相撞的叶片，绘制阶段读取积分表。"""

    def __init__(self, parent):
        """初始化空飞行队列与懒加载位图缓存。"""
        super().__init__(parent, 4)
        self.rng = Random()
        self.flight = []
        self.last = -float('inf')
        self.pending = None
        self.poses = []
        self.titles = []
        self.art = []
        self.faces = []
        self.petal = QPixmap()

    def load(self):
        """加载预烘焙五档光照和正背面，计算叶柄到叶尖三分之一处的坐标。"""
        self.faces = [tuple(QPixmap(str(ASSETS / 'leaves' / f'{i}-{side}.webp'))
                            for side in ('front', 'back')) for i in range(1, 8)]
        self.petal = QPixmap(str(ASSETS / 'leaves' / 'petal.webp'))
        for bx, by, tx, ty, height, reach in LEAF_ART:
            dx, dy = tx - bx, ty - by
            stem = 38 / hypot(dx, dy)
            pivot_x = bx + dx * (1 - 2 * stem) / 3
            pivot_y = by + dy * (1 - 2 * stem) / 3
            self.art.append((pivot_x, pivot_y, atan2(dy, dx), height, reach))

    def clear(self):
        """取消筛选并清空在途轨迹。"""
        self.flight.clear()
        self.poses.clear()
        self.pending = None
        self.last = -float('inf')
        self.update()

    def gust(self, now):
        """请求一次落叶批次，连切保留在途叶，短间隔最多再加三片。"""
        if not self.faces:
            self.load()
        self.flight = [leaf for leaf in self.flight if now < leaf.start + leaf.life]
        quick = now - self.last < 1.5
        self.last = now
        amount = min(3 if quick else self.rng.randint(6, 10), 14 - len(self.flight))
        w, h = self.rect().width(), self.rect().height()
        paths = [leaf_flight.sample(leaf, now, w, h) for leaf in self.flight]
        self.pending = self._candidates(now, amount, paths, w, h) if amount else None
        if self.pending is not None:
            next(self.pending)

    def _candidates(self, now, amount, paths, w, h):
        """逐次筛选叶子，把积分和避让开销分摊到动画帧。"""
        accepted = 0
        for _ in range(144):
            current = yield
            # 补算的叶保留在本次出发窗口内，并留一帧时间供它从画外进入。
            release = min(leaf_flight.MAX_DELAY, max(0, current - now) + .016)
            leaf = leaf_flight.make_leaf(self.rng, now, w, h, RATIOS, release)
            path = leaf_flight.sample(leaf, now, w, h)
            if leaf_flight.separated(path, paths, w):
                paths.append(path)
                self.flight.append(leaf)
                accepted += 1
                if accepted == 8 and leaf.size <= .045 * w and self.rng.random() < .28:
                    if RATIOS[leaf.picture] >= 379 / 456:
                        leaf.picture = 7
                if accepted == amount:
                    return

    def advance(self, now):
        """推进飞行队列和有限的候选计算，返回是否仍需下一帧。"""
        if self.pending is not None:
            if now - self.last >= leaf_flight.MAX_DELAY:
                self.pending = None
            else:
                budget = QElapsedTimer()
                budget.start()
                try:
                    while budget.elapsed() < 6:
                        self.pending.send(now + budget.elapsed() / 1000)
                except StopIteration:
                    self.pending = None
        self.flight = [leaf for leaf in self.flight if now < leaf.start + leaf.life]
        self.poses = [(leaf, leaf.pose(now - leaf.start)) for leaf in self.flight if now >= leaf.start]
        self.update()
        return bool(self.flight or self.pending)

    def paint(self, painter, option, widget=None):
        """用当前姿态绘制飞叶，擦过标题时把整叶透明度设为 0.6。"""
        painter.setClipRect(self.rect(), Qt.ClipOperation.IntersectClip)
        for leaf, (x, y, roll, pitch, yaw) in self.poses:
            width, height, shear, light = leaf_flight.projection(roll, pitch, yaw)
            position = QPointF(x, y) + self.rect().topLeft()
            painter.save()
            painter.translate(position)
            if leaf.picture < 7:
                px, py, angle, art_height, reach = self.art[leaf.picture]
                scale = .86 / 456 * leaf.size
                radius = max(leaf.radius, reach * scale * 1.074)
                painter.rotate(yaw * 180 / pi)
                painter.setTransform(QTransform(width, 0, shear, height, 0, 0), True)
                painter.rotate((-pi / 2 - angle) * 180 / pi)
                target = QRectF((-48 - px) * scale, (-48 - py) * scale,
                                552 * scale, (art_height + 96) * scale)
                pixmap = self.faces[leaf.picture][width < 0]
                tile = pixmap.width() // 5
                source = QRectF(light * tile, 0, tile, pixmap.height())
            else:
                radius = leaf.radius
                painter.rotate(225 + atan2(.55 * sin(roll), .84) * 180 / pi)
                painter.scale(cos(roll), 1)
                painter.rotate(-51)
                pixmap = self.petal
                source = QRectF(pixmap.rect())
                tall = leaf.size * 379 / 456
                target = QRectF(-leaf.size / 2, -tall / 2, leaf.size, tall)
            guarded = any(hypot(max(0, rect.left() - position.x(), position.x() - rect.right()),
                                max(0, rect.top() - position.y(), position.y() - rect.bottom())) <= radius
                          for rect in self.titles)
            painter.setOpacity(.6 if guarded else .75 + .25 * leaf.depth)
            painter.drawPixmap(target, pixmap, source)
            painter.restore()


class PageTurn(Layer):
    """用新页面快照做 180 毫秒淡入，结束后露出实时页面。"""

    def __init__(self, parent):
        """创建空的切页场景层。"""
        super().__init__(parent, 1)
        self.image = QPixmap()
        self.start = 0
        self.progress = 1
        self.slide = 0

    def begin(self, page, rect, now, reduced):
        """抓取新页面和纸纹，让位移只发生在不接管输入的快照上。"""
        self.setRect(rect)
        self.image = page.grab()
        self.start, self.progress, self.slide = now, 0, 0 if reduced else 8
        self.show()

    def finish(self):
        """丢弃临时快照并恢复实时绘制。"""
        self.hide()
        self.image = QPixmap()

    def advance(self, now):
        """更新时间和缓动进度，返回动画是否尚未完成。"""
        if not self.isVisible():
            return False
        self.progress = min(1, (now - self.start) / .18)
        if self.progress >= 1:
            self.finish()
            return False
        self.update()
        return True

    def paint(self, painter, option, widget=None):
        """在纸色底上淡入快照并向左平移至正文位置。"""
        rect = self.rect()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        painter.fillRect(rect, QColor(paper.DESK))
        p = 1 - (1 - self.progress)**3
        painter.setOpacity(p)
        painter.drawPixmap(rect.translated(self.slide * (1 - p), 0), self.image, QRectF(self.image.rect()))


class Decor(Layer):
    """用实际界面空隙定位常驻的窗光、插画、水印和叶丛。"""

    def __init__(self, parent):
        """创建空装饰层，首次进入屏幕时再读素材。"""
        super().__init__(parent, 2)
        self.images = {}
        self.drawings = []
        self.light_clip = self.corner_clip = None

    def arrange(self, bounds, index, title, title_end, limit, card, empty, protect, corner_protect):
        """更新常驻装饰，empty 表示可用的空状态插画区。"""
        self.setRect(bounds)
        if not self.images:
            self.images = {name: QPixmap(str(ASSETS / (name + '.webp')))
                           for name in (*PAGE_ART, *STAMPS, 'foliage')}
        art = PAGE_ART[index]
        self.drawings = []
        if limit - title_end >= 28:
            self.drawings.append((art, QRectF(title_end, title.top() - 3, min(53, limit - title_end),
                                              min(40, title.height() + 6)), .95))
        if not empty.isEmpty():
            self.drawings.append((art, empty, .88))
        w = min(card.width() * .32, 124)
        self.drawings.append((STAMPS[index], QRectF(card.right() - w - 81, card.bottom() - 87, w, 68), 1))
        w = min(188, max(108, bounds.width() * .144))
        self.drawings.append(('foliage', QRectF(bounds.right() - w + 16, bounds.bottom() - 161, w, 177), .94))
        self.light_clip = clear_path(bounds, [rect.adjusted(-.5, -.5, .5, .5) for rect in protect])
        self.corner_clip = clear_path(bounds, corner_protect)
        self.update()

    def paint(self, painter, option, widget=None):
        """按定稿的斜入射方向画窗光，并在控件保护范围外绘制纸面素材。"""
        if self.light_clip is None:
            return
        rect = self.rect()
        painter.save()
        painter.setClipPath(self.light_clip, Qt.ClipOperation.IntersectClip)
        gradient = QLinearGradient(rect.topRight(), rect.bottomLeft())
        gradient.setColorAt(0, QColor(255, 248, 223, 119))
        gradient.setColorAt(.60, QColor(255, 243, 200, 66))
        gradient.setColorAt(1, QColor(255, 246, 211, 42))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        for color, points in ((None, ((.76, 0), (1, 0), (1, .18), (.30, 1), (0, 1), (0, .86))),
                              (QColor(255, 248, 219, 30), ((.94, 0), (1, 0), (1, .05), (.12, 1), (.04, 1)))):
            if color:
                painter.setBrush(color)
            painter.drawPolygon(QPolygonF([rect.topLeft() + QPointF(x * rect.width(), y * rect.height())
                                           for x, y in points]))
        painter.restore()
        for name, box, opacity in self.drawings:
            painter.save()
            if name in STAMPS or name == 'foliage':
                painter.setClipPath(self.corner_clip, Qt.ClipOperation.IntersectClip)
            pixmap = self.images[name]
            size = pixmap.size()
            size.scale(box.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(box.topLeft(), size)
            target.moveCenter(box.center())
            painter.setOpacity(opacity)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
            painter.restore()


class Ornaments(QObject):
    """在屏幕模式管理场景层和按需计时器。"""

    def __init__(self, window):
        """接到托盘窗口实际页面、纸片和各状态控件。"""
        super().__init__(window.room)
        self.view = window.room
        self.desk = self.view.ui
        self.pages = window.pages
        self.return_button = window.room_button
        self.sidebar_controls = (window.navigation, window.settings_button)
        self.brand = self.desk.findChild(QLabel, 'brand')
        wechat, mail, computer = window.wechat_items, window.mail_items, window.computer_page
        self.anchors = (
            (window.run_state.parentWidget(), wechat.details, wechat.heading_widget, wechat.list),
            (window.mail.parentWidget(), mail.details, mail.heading_widget, mail.list),
            (computer.task.parentWidget(), computer.preview.parentWidget(), computer.status, computer.list),
        )
        self.mail, self.preview = mail, computer.preview
        self.root = passive(QGraphicsRectItem())
        self.root.setPen(Qt.PenStyle.NoPen)
        self.root.setZValue(10)
        self.view.scene().addItem(self.root)
        self.turn = PageTurn(self.root)
        self.turn.finish()
        self.decor = Decor(self.root)
        self.decor.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.leaves = Leaves(self.root)
        self.vine = self.fading = None
        self.vine_pixmaps = ()
        self.geometry = None
        self.changed = False
        self.reduced = False
        self.vine_running = False
        self.started = self.fade_started = 0
        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self, interval=16, timerType=Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.advance)
        self.layout_timer = QTimer(self, singleShot=True)
        self.layout_timer.timeout.connect(self.arrange)
        self.view.modeChanged.connect(self.mode_changed)
        self.pages.currentChanged.connect(self.page_changed)
        self.preview.imageChanged.connect(self.queue_layout)
        self.mail.list.currentItemChanged.connect(self.queue_layout)
        for widget in (self.view, self.desk, *self.desk.findChildren(QWidget)):
            if widget in (self.view, self.desk, self.pages) or isinstance(
                widget, (paper.Slip, QLabel, QAbstractButton, QAbstractItemView, QLineEdit)
            ):
                widget.installEventFilter(self)
        self.root.hide()

    def eventFilter(self, watched, event):
        """合并控件布局变化，窗口隐藏时立即停止屏幕动效。"""
        # 场景销毁时会先删除视图，再触发 proxy 内部控件的 Hide。
        if not isValid(self.view):
            return False
        kind = event.type()
        if watched is self.view and kind == QEvent.Type.Hide:
            self.mode_changed('room')
        elif kind in GEOMETRY_EVENTS:
            self.queue_layout()
        return False

    def queue_layout(self, *_):
        """等待 Qt 完成布局后一次计算实际场景坐标。"""
        if self.view.mode == 'screen' and self.view.isVisible() and not self.layout_timer.isActive():
            self.layout_timer.start(0)

    def mode_changed(self, mode):
        """屏幕以外隐藏整层，立即释放快照、落叶和活动计时器。"""
        if mode == 'screen':
            self.queue_layout()
            return
        self.layout_timer.stop()
        self.root.hide()
        self.turn.finish()
        self.leaves.clear()
        self._remove(self.vine)
        self._remove(self.fading)
        self.vine = self.fading = self.geometry = None
        self.changed = False
        self._animate(False)

    def page_changed(self, _index):
        """保留当前切页请求，等新页布局稳定后启动动效。"""
        if self.view.mode == 'screen' and self.view.isVisible():
            self.changed = True
            self.queue_layout()

    def scene_rect(self, widget):
        """把嵌套控件的局部矩形转换到 RoomView 场景坐标。"""
        origin = widget.mapTo(self.desk, QPoint())
        return self.view.proxy.mapRectToScene(QRectF(origin.x(), origin.y(), widget.width(), widget.height()))

    def light_holes(self, controls):
        """保护文字底色和真实截图，透明列表只避开可见条目。"""
        holes = []
        for widget in controls:
            if isinstance(widget, QListWidget):
                viewport = self.scene_rect(widget.viewport())
                if widget is not self.sidebar_controls[0] and widget.count():
                    holes.append(viewport)
                    continue
                for i in range(widget.count()):
                    item = widget.visualItemRect(widget.item(i))
                    rect = QRectF(item).translated(viewport.topLeft()).intersected(viewport)
                    if not rect.isEmpty():
                        holes.append(rect)
            elif isinstance(widget, (QAbstractButton, QLineEdit, QTextBrowser)):
                holes.append(self.scene_rect(widget))
            elif isinstance(widget, QLabel):
                flags = widget.alignment().value
                if widget.wordWrap():
                    flags |= Qt.TextFlag.TextWordWrap.value
                holes.append(self.text_area(widget, flags, widget.text()))
        return holes

    def text_area(self, widget, flags, text):
        """返回文字布局所占的小矩形，避免暖色光叠在正文笔画上。"""
        local = widget.fontMetrics().boundingRect(widget.contentsRect(), flags, text)
        return QRectF(local).translated(self.scene_rect(widget).topLeft()).adjusted(-2, -2, 2, 2)

    def arrange(self):
        """读取当前页的控件边界，重建尺寸相关的场景装饰。"""
        if self.view.mode != 'screen' or not self.view.isVisible():
            return
        index = self.pages.currentIndex()
        page = self.pages.currentWidget()
        toolbar, card, title, column = self.anchors[index]
        bounds = self.view.proxy.mapRectToScene(QRectF(self.desk.rect())).intersected(self.view.sceneRect())
        bar, card_rect, title_rect = map(self.scene_rect, (toolbar, card, title))
        title_end = title_rect.left() + title.contentsMargins().left()
        title_end += min(title_rect.width(), title.fontMetrics().horizontalAdvance(title.text())) + 10
        limit = self.scene_rect(column).right() - 5
        if index == 2:
            limit = title_rect.right() - 4
        else:
            badge = title.parentWidget().findChild(QLabel, 'badge')
            if badge and badge.isVisible():
                title_end = max(title_end, self.scene_rect(badge).right() + 7)
        empty = QRectF()
        if index == 1 and self.mail.current is None:
            text = self.scene_rect(self.mail.title)
            height = min(71, text.top() - card_rect.top() - 18)
            if height > 24:
                empty = QRectF(text.left(), text.top() - height - 5, height * 1.4, height)
        elif index == 2 and self.preview.image.isNull():
            area = self.scene_rect(self.preview)
            size = min(84, area.height() * .30)
            empty = QRectF(area.center().x() - size * .7, area.center().y() - size - 15, size * 1.4, size)
        controls = [widget for widget in page.findChildren(QWidget)
                    if isinstance(widget, (QAbstractButton, QAbstractItemView, QLabel, QLineEdit, QTextBrowser))
                    and widget.isVisible() and (not isinstance(widget, QLabel) or widget.text())]
        light_holes = self.light_holes((*controls, self.brand, self.return_button, *self.sidebar_controls))
        if index == 2:
            if self.preview.image.isNull():
                light_holes.append(self.text_area(self.preview, Qt.AlignmentFlag.AlignCenter.value, '暂无步骤截图'))
            else:
                light_holes.append(self.scene_rect(self.preview))
        vine_holes = [bar, card_rect, self.scene_rect(column), self.scene_rect(self.return_button),
                      *(self.scene_rect(w) for w in controls)]
        if limit - title_end >= 28:
            vine_holes.append(QRectF(title_end, title_rect.top() - 3, min(53, limit - title_end),
                                    min(40, title_rect.height() + 6)))
        vine_holes = tuple(rect.adjusted(-1, -1, 1, 1) for rect in vine_holes if not rect.isEmpty())
        corner_holes = light_holes + [self.scene_rect(w) for w in controls if isinstance(w, QLabel)]
        geometry = bounds, index, bar, card_rect, vine_holes
        self.root.show()
        self.decor.arrange(bounds, index, title_rect, title_end, limit, card_rect, empty, light_holes, corner_holes)
        self.leaves.titles = [title_rect, bar, self.scene_rect(self.return_button)]
        self._arrange_motion(page, bounds, geometry)

    def _arrange_motion(self, page, bounds, geometry):
        """切页调度动效，布局变化时重建同一根藤并保留原进度。"""
        now = self.clock.elapsed() / 1000
        switched = self.changed and (self.geometry is None or self.geometry[1] != geometry[1])
        self.changed = False
        if bounds != self.leaves.rect():
            self.leaves.clear()
            self.leaves.setRect(bounds)
            self.turn.finish()
            self._remove(self.fading)
            self.fading = None
        if self.geometry == geometry:
            return
        _, index, toolbar, card, holes = geometry
        if not self.vine_pixmaps:
            self.vine_pixmaps = tuple(svg_pixmap(name) for name in ('willow', 'heart', 'pointed', 'round'))
        if switched:
            self.reduced = self.view.reduced()
            self.turn.begin(page, self.scene_rect(page), now, self.reduced)
            if self.fading is None:
                self.fading, self.vine = self.vine, None
                self.fade_started = now
            if not self.reduced:
                self.leaves.gust(now)
            else:
                self.leaves.clear()
            self.started = self.fade_started + .2 if self.fading is not None else now
        elif self.vine is None:
            self.reduced = self.view.reduced()
            self.started = now - GROWTH - SETTLE
        if self.fading is not None:
            self.fading.setPath(self.fading.path().intersected(clear_path(bounds, holes)))
        self._remove(self.vine)
        self.vine = Vine(self.root, index, toolbar, card, bounds, holes, self.vine_pixmaps)
        self.vine.setZValue(3)
        self.geometry = geometry
        self.advance(now)

    def _remove(self, item):
        """从场景中释放已结束的藤项及其所有子项。"""
        if item is not None:
            item.setParentItem(None)
            self.view.scene().removeItem(item)

    def _animate(self, active):
        """仅在有动效时运行计时器，并缓存净缩放为 1 的界面。"""
        if active == self.timer.isActive():
            return
        self.view.proxy.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache if active
                                     else QGraphicsItem.CacheMode.NoCache)
        if active:
            self.timer.start()
        else:
            self.timer.stop()

    def advance(self, now=None):
        """推进全部动效，最后一片叶离场后停止帧计时。"""
        now = self.clock.elapsed() / 1000 if now is None else now
        active = self.turn.advance(now)
        if self.fading is not None:
            p = (now - self.fade_started) / .2
            if p >= 1:
                self._remove(self.fading)
                self.fading = None
            else:
                self.fading.setOpacity(1 - p)
                active = True
        if self.vine is not None:
            age = now - self.started
            self.vine.setVisible(age >= 0)
            duration = .15 if self.reduced else GROWTH + SETTLE
            if self.reduced:
                self.vine.setOpacity(min(1, max(0, age / duration)))
            if age < duration or self.vine.ink.path().isEmpty() or self.vine_running:
                self.vine.draw_age(GROWTH + SETTLE if self.reduced else max(0, age))
            self.vine_running = age < duration
            active = active or self.vine_running
        leaves_active = self.leaves.advance(now) if self.leaves.flight or self.leaves.pending else False
        self._animate(active or leaves_active)
