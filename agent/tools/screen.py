"""截图。"""
from dataclasses import dataclass

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
