"""截取微信主窗口，作为评测样本。

用法：python capture.py
运行后切到微信，摆好一个界面就按 F9 截一张，按 F10 结束。
截图保存在 shots/01.png、02.png …
"""
import ctypes
import time
from pathlib import Path

import uiautomation as auto
from PIL import ImageGrab

VK_F9, VK_F10 = 0x78, 0x79
user32 = ctypes.windll.user32
ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 按物理像素截图，坐标才和屏幕对得上

out = Path(__file__).parent / 'shots'
out.mkdir(exist_ok=True)


def wechat_rect():
    # 微信 4.x 主窗口：Qt 窗口类，标题是"微信"
    for w in auto.GetRootControl().GetChildren():
        if w.ClassName.startswith('Qt') and w.Name == '微信':
            r = w.BoundingRectangle
            if r.width() > 300:
                return r.left, r.top, r.right, r.bottom
    return None


def pressed(vk):
    return user32.GetAsyncKeyState(vk) & 1


n = len(list(out.glob('*.png')))
print('就绪：F9 截图，F10 结束')
while True:
    if pressed(VK_F10):
        break
    if pressed(VK_F9):
        rect = wechat_rect()
        if not rect:
            print('没找到微信主窗口')
            continue
        n += 1
        path = out / f'{n:02d}.png'
        ImageGrab.grab(bbox=rect, all_screens=True).save(path)
        print(f'已保存 {path.name}  窗口区域={rect}')
    time.sleep(0.05)
