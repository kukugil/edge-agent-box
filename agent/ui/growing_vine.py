"""从实际纸片边框生成藤茎、卷须和逐叶生长的场景项。"""

from math import atan2, ceil, cos, exp, pi, sin
from random import Random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem

PROFILES = (
    ((47, 20, 35, 15, 30, 13), (18, 10, 16, 7), (1, 0, 2, 0, 3, 1, 0, 2)),
    ((32, 15, 50, 29, 18, 15), (12, 19, 11, 13), (0, 2, 1, 3, 0, 2, 3, 1)),
    ((51, 36, 15, 44, 34, 11), (19, 7, 15, 9), (2, 3, 0, 2, 1, 0, 3, 2)),
)
GROWTH = .56
SETTLE = .13
INK = QColor('#304436')


def passive(item):
    """让场景装饰放行鼠标、触摸及拖放事件，返回原项。"""
    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    item.setAcceptHoverEvents(False)
    item.setAcceptTouchEvents(False)
    item.setAcceptDrops(False)
    return item


def route(page, toolbar, card, bounds):
    """按页型和卡片矩形生成中心线路径，坐标与场景一致。"""
    rise, sweep, _ = PROFILES[page]
    x, right, y, bottom = toolbar.left(), toolbar.right(), toolbar.top(), toolbar.bottom()
    rail = min(right + 18, bounds.right() - 9)
    bend = (bottom + card.top()) / 2
    points = [(x - 17, y - 9), (x + 13, max(bounds.top() + 16, y - rise[0])),
              *((x + p * toolbar.width(), max(bounds.top() + 16, y - r))
                for p, r in zip((.14, .32, .51, .69, .85), rise[1:])),
              (right - 9, y - 8), (rail, y + 8), (rail + 2, bottom - 6),
              (rail - 2, max(bottom + 4, bend)), (right + 4, card.top() - 4),
              *((card.right() - p * card.width(), max(bottom + 4, card.top() - r))
                for p, r in zip((.10, .29, .54, .78), sweep)),
              (card.left() + card.width() * (.07 + .04 * page), max(bottom + 4, card.top() - 5))]
    return spline([QPointF(*point) for point in points])


def spline(points):
    """把锚点插值为平滑三次贝塞尔路径。"""
    path = QPainterPath(points[0])
    for i, (start, end) in enumerate(zip(points, points[1:])):
        before, after = points[max(0, i - 1)], points[min(len(points) - 1, i + 2)]
        path.cubicTo(start + (end - before) * .13, end - (after - start) * .13, end)
    if hasattr(path, 'setCachingEnabled'):
        path.setCachingEnabled(True)
    return path


def stroke(path, base, tip, jitter, phase):
    """沿弧长每约 2 像素采样，返回变宽墨线的中心、方向和半宽。"""
    length = path.length()
    count = max(3, ceil(length / 2))
    points = [path.pointAtPercent(path.percentAtLength(length * i / count)) for i in range(count + 1)]
    samples = []
    for i, point in enumerate(points):
        direction = points[min(i + 1, count)] - points[max(i - 1, 0)]
        angle = atan2(direction.y(), direction.x())
        normal = QPointF(-sin(angle), cos(angle))
        p, distance = i / count, length * i / count
        rough = jitter * sin(pi * p) * (.56 * sin(distance * .092 + phase) + .44 * sin(distance * .217))
        width = (tip + (base - tip) * (1 - p)**.58)
        width *= 1 + .16 * sin(distance * .069 + phase) + .075 * sin(distance * .183)
        samples.append((point + normal * rough, angle, normal * (width / 2)))
    return samples


def outline(samples, progress):
    """返回截至给定弧长比例的实心笔触，供 Qt 一次填充。"""
    path = QPainterPath()
    if progress > 0:
        shown = samples[:1 + ceil(min(1, progress) * (len(samples) - 1))]
        left = [point + edge for point, _, edge in shown]
        right = [point - edge for point, _, edge in reversed(shown)]
        path.addPolygon(QPolygonF(left + right))
        path.closeSubpath()
    return path


def clear_path(bounds, holes):
    """返回屏幕范围扣除控件矩形后的可绘制区域。"""
    clip = QPainterPath()
    clip.addRect(bounds)
    for rect in holes:
        hole = QPainterPath()
        hole.addRect(rect)
        clip = clip.subtracted(hole)
    return clip


class Vine(QGraphicsPathItem):
    """裁切整棵藤，墨线递进揭开，叶柄固定在缩放原点。"""

    def __init__(self, parent, page, toolbar, card, bounds, holes, pixmaps):
        """从当前页布局和四种叶图创建一根未开始生长的藤。"""
        super().__init__(clear_path(bounds, holes), parent)
        passive(self)
        self.setPen(Qt.PenStyle.NoPen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        rng = Random(29 + page)
        self.main = stroke(route(page, toolbar, card, bounds), 4.25, .24, 1.35, rng.random() * 6)
        self.ink = passive(QGraphicsPathItem(self))
        self.ink.setPen(Qt.PenStyle.NoPen)
        self.ink.setBrush(INK)
        self.shoots = []
        self.leaves = []
        self._plant(page, bounds, holes, pixmaps)
        self._curls(page)

    def _plant(self, page, bounds, holes, pixmaps):
        """沿主茎挑选八处叶柄位置，按回弹最大外框避开控件。"""
        positions = (.055, .142, .248, .348, .459, .602, .763, .903)
        sizes = (43, 30, 48, 34, 44, 28, 43, 32)
        for i, (at, size, kind) in enumerate(zip(positions, sizes, PROFILES[page][2])):
            pixmap = pixmaps[kind]
            hand = (-1 if page == 1 else 1) * (-1 if i % 2 else 1)
            branch = 9 if i in (0, 3, 6) else 0
            placement = self._find_leaf(at, hand, branch, size, pixmap, bounds, holes)
            if placement is None:
                continue
            progress, node, anchor, transform = placement
            if branch:
                middle = node * .4 + anchor * .6 + QPointF(0, hand * 2)
                self.shoots.append((progress, .027, stroke(spline([node, middle, anchor]), 2.35, .62, .12, 0)))
            leaf = passive(QGraphicsPixmapItem(pixmap, self))
            leaf.setOffset(-2, -30)
            leaf.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            leaf.setTransform(transform)
            leaf.setPos(anchor)
            self.leaves.append((progress * GROWTH + (.024 if branch else 0), leaf))

    def _find_leaf(self, at, hand, branch, size, pixmap, bounds, holes):
        """返回能容纳叶片的锚点和变换，空间收窄时逐步缩小。"""
        for scale in (1, .8, .6, .42):
            for offset in (0, -.018, .018, -.038, .038, -.065, .065):
                index = round((at + offset) * (len(self.main) - 1))
                index = min(len(self.main) - 1, max(0, index))
                node, tangent, _ = self.main[index]
                for side in (hand, -hand):
                    for turn in (.55, .95, .18, 1.35):
                        angle = tangent + side * turn
                        anchor = node + QPointF(cos(angle), sin(angle)) * branch
                        transform = QTransform()
                        transform.rotateRadians(angle)
                        factor = size / 53 * scale
                        transform.scale(factor, side * factor)
                        footprint = QRectF(-2, -30, pixmap.width(), pixmap.height())
                        footprint = transform.mapRect(footprint).translated(anchor)
                        reach = footprint.united(QRectF(anchor, anchor))
                        # 变换以叶柄为原点，扩大四边可覆盖从零到回弹峰值。
                        extra = max(reach.width(), reach.height()) * .13
                        reach.adjust(-extra, -extra, extra, extra)
                        if bounds.contains(reach) and not any(reach.intersects(hole) for hole in holes):
                            return index / (len(self.main) - 1), node, anchor, transform
        return None

    def _curls(self, page):
        """在三处主茎节点创建逐渐收尖的螺旋卷须。"""
        local = ((0, 0), (7, 6), (10, 16), (17, 20), (24, 15),
                 (23, 8), (17, 6), (13, 10), (16, 14), (19, 12))
        for at, hand in ((.112, -1), (.39, -1), (.82, 1)):
            node, angle, _ = self.main[round(at * (len(self.main) - 1))]
            transform = QTransform().translate(node.x(), node.y()).rotateRadians(angle)
            transform.scale(1, hand if page != 1 else -hand)
            path = spline([transform.map(QPointF(x, y)) for x, y in local])
            self.shoots.append((at, .073, stroke(path, 1.9, .29, .13, 0)))

    def draw_age(self, age):
        """把藤更新到给定秒数，叶项沿叶柄弹出再收稳。"""
        path = outline(self.main, age / GROWTH)
        path.setFillRule(Qt.FillRule.WindingFill)
        for start, duration, shoot in self.shoots:
            path.addPath(outline(shoot, (age - start * GROWTH) / duration))
        self.ink.setPath(path)
        for start, leaf in self.leaves:
            p = max(0, min(1, (age - start) / SETTLE))
            leaf.setVisible(p > 0)
            leaf.setScale(1 if p >= 1 else 1 - exp(-5.5 * p) * cos(9 * p))
