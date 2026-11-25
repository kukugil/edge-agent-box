"""第二十三轮落叶的薄片积分、出界定标和飞行避让。此模块不依赖 Qt。"""

from dataclasses import dataclass
from math import atan2, ceil, cos, floor, hypot, pi, sin, tau

STEP = 1 / 120
MAX_LIFE = 3.44
MAX_DELAY = .4
SPACING_STEP = .006


@dataclass(frozen=True)
class Air:
    mass: float
    inertia: float
    wind: float
    damping: float
    rate: float
    gust_rate: float
    gust_phase: float


def rates(u, v, roll, spin, age, air):
    """返回屏幕坐标下的线加速度、滚角速率和角加速度。"""
    c, s = cos(roll), sin(roll)
    gust = 1 + .20 * sin(air.gust_rate * age + air.gust_phase)
    x, y = u + air.wind * gust, v - .08 * gust
    a, b = c * x + s * y, -s * x + c * y
    speed = max(hypot(a, b), 1e-9)
    circulation = 2 / pi * (-1.2 * a * b / speed + pi * spin)
    drag = (.4 * a * a + 2.4 * b * b) / (pi * speed)
    along = ((spin - circulation) * b + s - drag * a) / air.mass
    normal = ((spin + circulation) * a + c - drag * b) / (air.mass + 1)
    damping = .20 + (air.damping - .20) * max(0, 1 - age / 1.25)**2
    torque = -a * b - damping * (1 + abs(spin)) * spin - .0125 * sin(2 * roll)
    return (air.rate * (c * along - s * normal), air.rate * (s * along + c * normal),
            air.rate * spin, air.rate * 4 * torque / (air.inertia + .5))


def integrate(initial, air, duration):
    """以 RK2 中点法积分，返回状态表和位置插值的最大速率。"""
    u, v, roll, spin = initial
    x = y = max_speed = 0
    path = [(x, y, u, v, roll)]
    half = STEP / 2
    for i in range(ceil(duration / STEP)):
        age = i * STEP
        du, dv, dr, ds = rates(u, v, roll, spin, age, air)
        mid_u, mid_v = u + du * half, v + dv * half
        au, av, turn, accel = rates(mid_u, mid_v, roll + dr * half,
                                    spin + ds * half, age + half, air)
        max_speed = max(max_speed, hypot(mid_u, mid_v))
        x, y = x + mid_u * STEP, y + mid_v * STEP
        u, v, roll, spin = u + au * STEP, v + av * STEP, roll + turn * STEP, spin + accel * STEP
        path.append((x, y, u, v, roll))
    return path, max_speed


def point(path, age):
    """从积分表插值得到指定时刻的位置、速度和滚角。"""
    i = min(len(path) - 2, max(0, int(age / STEP)))
    p = min(1, max(0, age / STEP - i))
    a, b = path[i], path[i + 1]
    return tuple(x + (y - x) * p for x, y in zip(a, b))


def fit(initial, air, target, dx, dy):
    """按累计最远点缩放轨迹，让叶片首次到达左边或底边时离场。"""
    path, speed = integrate(initial, air, MAX_LIFE)
    reached = life = 0

    def record(state, age):
        """记录轨迹相对离场边最远的进度及首次到达时刻。"""
        nonlocal reached, life
        x, y, *_ = state
        progress = max(-x / dx, y / dy)
        if progress > reached:
            reached, life = progress, age

    index = 1
    while index * STEP < target:
        record(path[index], index * STEP)
        index += 1
    record(point(path, target), target)
    while index * STEP <= MAX_LIFE and life < 2.22:
        record(path[index], index * STEP)
        index += 1
    if life < 2.22:
        record(point(path, MAX_LIFE), MAX_LIFE)
    return path, life, 1 / reached, speed / reached


@dataclass
class Leaf:
    picture: int
    depth: float
    size: float
    radius: float
    start: float
    life: float
    x: float
    y: float
    scale: float
    speed: float
    path: list

    def pose(self, age):
        """返回指定飞行时刻的叶心和 roll、pitch、yaw。"""
        x, y, u, v, roll = point(self.path, age)
        _, _, u0, v0, _ = point(self.path, max(0, age - .09))
        _, _, u1, v1, _ = point(self.path, min(self.life, age + .09))
        yaw = atan2(v0 + 2 * v + v1 + .24, u0 + 2 * u + u1) + pi / 2
        pitch = .24 * sin(age * (2.5 + .45 * self.depth) + self.depth * tau)
        pitch += .07 * sin(age * 1.35 + self.picture)
        return self.x + self.scale * x, self.y + self.scale * y, roll, pitch, yaw


def make_leaf(rng, now, w, h, ratios, release=0):
    """按预览抽取一片叶，出发时间在本批次尚未过去的区间内。"""
    uniform = rng.uniform
    picture = rng.randrange(len(ratios))
    depth = rng.random()
    size = (.03 + .04 * depth) * w
    radius = hypot(size, size * ratios[picture]) / 2 + 1
    target = 3.40 - 1.14 * depth + uniform(-.035, .035)
    top = rng.random() < .7
    x, y = (w * uniform(.20, .99), -radius) if top else (w + radius, h * uniform(.02, .58))
    rolling = rng.random() < .24
    area = uniform(.92, 1.08)
    mass = uniform(1.03, 1.16) / area
    wind = .96 * uniform(.92, 1.08)
    direction = -1 if rng.random() < .5 else 1
    air = Air(mass, (1.55 if rolling else 1) * mass, wind, .065 if rolling else .20,
              uniform(9.3, 10.7), tau / uniform(3.8, 6.4), uniform(0, tau))
    initial = (-wind + direction * uniform(.65, 1.35), .08 + uniform(.85, 1.35),
               direction * (uniform(.65, 1.40) if rolling else uniform(.25, .95)),
               direction * 5.5 if rolling else uniform(-.4, .4))
    dx, dy = x + radius + .01, h + radius - y + .01
    path, life, scale, speed = fit(initial, air, target, dx, dy)
    if life < 2.22:
        air = Air(mass, mass, wind, .20, air.rate, air.gust_rate, air.gust_phase)
        path, life, scale, speed = fit((-wind - .90, 1.25, .55, 0), air, target, dx, dy)
    return Leaf(picture, depth, size, radius, now + uniform(release, MAX_DELAY),
                life, x, y, scale, speed, path)


def sample(leaf, now, w, h):
    """将叶心投到统一时间网格，返回首索引、采样位置和半步位移余量。"""
    half = SPACING_STEP / 2
    padding = leaf.speed * half
    begin = max(0, ceil((leaf.start - now - half) / SPACING_STEP))
    end = floor((leaf.start + leaf.life - now + half) / SPACING_STEP) + 1
    radius = leaf.radius + padding
    points = []
    for i in range(begin, end):
        t = min(leaf.life, max(0, now + i * SPACING_STEP - leaf.start))
        index = min(len(leaf.path) - 2, int(t / STEP))
        p = t / STEP - index
        ax, ay, *_ = leaf.path[index]
        bx, by, *_ = leaf.path[index + 1]
        x = leaf.x + leaf.scale * (ax + (bx - ax) * p)
        y = leaf.y + leaf.scale * (ay + (by - ay) * p)
        points.append((x, y) if -radius < x < w + radius and -radius < y < h + radius else None)
    return begin, points, padding


def separated(candidate, others, width):
    """仅在同一时刻比较可见叶心，给采样之间的相对位移留余量。"""
    start, points, pad = candidate
    for first, peers, extra in others:
        limit = max(.123 * width, .12 * width + pad + extra)**2
        for i in range(max(start, first), min(start + len(points), first + len(peers))):
            a, b = points[i - start], peers[i - first]
            if a is not None and b is not None and (a[0] - b[0])**2 + (a[1] - b[1])**2 < limit:
                return False
    return True


def projection(roll, pitch, yaw):
    """由三维朝向返回投影宽高、错切和最近的五档受光索引。"""
    width, height = cos(roll), cos(pitch)
    shear = .24 * sin(roll) * sin(pitch)
    nx, ny, nz = sin(roll) * height, -sin(pitch), width * height
    c, s = cos(yaw), sin(yaw)
    light = (.50 * (c * nx - s * ny) - .53 * (s * nx + c * ny) + .685 * nz)
    light /= hypot(.50, .53, .685)
    return width, height, shear, min(4, round(4 * light * light))
