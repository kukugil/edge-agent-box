"""截图。"""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageGrab


@dataclass
class Shot:
    """一张截图，外加它在屏幕上的左上角位置。

    为什么要带位置：模型和 OCR 给出的坐标都是“图里的坐标”，
    鼠标要点的是“屏幕坐标”，两者差的就是 left/top。
    """
    image: Image.Image
    left: int
    top: int

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """截图内坐标 → 屏幕坐标。"""
        return int(round(self.left + x)), int(round(self.top + y))


def screenshot(rect: tuple[int, int, int, int]) -> Shot:
    """截取屏幕上的一块矩形区域。

    输入：rect = (left, top, right, bottom)，屏幕坐标，通常来自 window.window_rect(hwnd)
    输出：Shot
    注意：截的是屏幕上看得见的像素，窗口被别的窗口挡住就会截到挡住它的东西，所以先 activate。
    """
    im = ImageGrab.grab(bbox=rect, all_screens=True).convert('RGB')
    return Shot(im, rect[0], rect[1])


def pad_top(image: Image.Image, px: int = 56) -> Image.Image:
    """在图片顶部补一条 px 高的白边，原图整体下移 px。

    用途：UI-TARS 对贴着图片顶边的元素会点偏下约 28px，补 56px 白边后偏差消失
    （实验见 experiments/wechat-vision/pad_test.py，33/38 → 37/38）。
    用补边图得到的 y 坐标要减去 px 才是原图坐标，ground() 已经替你做了。
    """
    padded = Image.new('RGB', (image.width, image.height + px), 'white')
    padded.paste(image.convert('RGB'), (0, px))
    return padded


def screen_changed(a: Image.Image, b: Image.Image, threshold: int = 30) -> bool:
    """两张同尺寸截图有没有肉眼可见的变化（灰度差超过 threshold 的像素存在即算变了）。"""
    if a.size != b.size:
        return True
    diff = ImageChops.difference(a.convert('L'), b.convert('L'))
    return diff.point(lambda v: 255 if v > threshold else 0).getbbox() is not None


def _runs(idx, gap: int = 2) -> list[list[int]]:
    # 把排好序的下标分成连续的几段，例如 [3,4,5,9,10] → [[3,4,5],[9,10]]
    out = []
    for i in idx:
        if out and i - out[-1][-1] <= gap:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def find_red_dots(image: Image.Image, x_max: int | None = None, min_size: int = 8) -> list[tuple[int, int, int, int]]:
    """找图里的红色小圆点（未读角标），从上到下返回外接框 (x1, y1, x2, y2)，截图内坐标。

    输入：x_max 只看图片左边这么宽的一块，None 表示整张图；min_size 比它小的红色碎点丢掉，过滤头像里零星的红色
    注意：只认颜色，不认数字；头像或图片里成块的红色也会被当成红点，所以要限定范围，并且只看“变没变”。
    """
    box = (0, 0, min(x_max, image.width) if x_max else image.width, image.height)
    a = np.asarray(image.convert('RGB').crop(box))
    red = (a[..., 0] > 220) & (a[..., 1] < 110) & (a[..., 2] < 110)  # 微信角标是 #FA5151 左右
    dots = []
    for ys in _runs(np.where(red.any(1))[0]):
        band = red[ys[0]:ys[-1] + 1]
        for xs in _runs(np.where(band.any(0))[0]):
            # 同一行带里可能有好几个红点，高度要各算各的
            inner = np.where(band[:, xs[0]:xs[-1] + 1].any(1))[0]
            y1, y2 = ys[0] + inner[0], ys[0] + inner[-1] + 1
            if len(xs) >= min_size and y2 - y1 >= min_size:
                dots.append((int(xs[0]), int(y1), int(xs[-1]) + 1, int(y2)))
    return sorted(dots, key=lambda d: d[1])
