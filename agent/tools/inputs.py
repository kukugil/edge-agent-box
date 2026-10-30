"""鼠标键盘：点击、按键、清空后粘贴、滚动。坐标一律是屏幕坐标。

现在是电脑本机用 Win32 模拟输入；以后换成盒子的 USB 键鼠时，只要换掉这个文件里的实现，函数签名不变。
"""
import ctypes
import time

import uiautomation as auto

user32 = ctypes.windll.user32

_MOUSE = {  # 按下、抬起的事件码
    'left': (0x0002, 0x0004),
    'right': (0x0008, 0x0010),
}

# 按键名 → Windows 虚拟键码。只收常用的，不认识的名字直接报错，免得按错键
_VK = {
    'ctrl': 0x11, 'shift': 0x10, 'alt': 0x12, 'win': 0x5B,
    'enter': 0x0D, 'tab': 0x09, 'esc': 0x1B, 'backspace': 0x08, 'delete': 0x2E, 'space': 0x20,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
    'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
    **{f'f{i}': 0x6F + i for i in range(1, 13)},
    **{c: ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'},
}
_EXTENDED = {0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22, 0x2E}  # 方向键等要带“扩展键”标志


def click(x: int, y: int, button: str = 'left', double: bool = False) -> None:
    """在屏幕坐标 (x, y) 点一下。button 是 'left' 或 'right'；double=True 双击。"""
    down, up = _MOUSE[button]
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    for _ in range(2 if double else 1):
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.05)


def press(keys: str) -> None:
    """按一个键或组合键，例如 'enter'、'ctrl+f'、'ctrl+shift+a'。

    组合键的做法和人一样：依次按下，再倒序抬起。
    """
    names = [k.strip().lower() for k in keys.split('+')]
    unknown = [k for k in names if k not in _VK]
    if unknown:
        raise ValueError(f'不认识的按键：{unknown}')
    codes = [_VK[k] for k in names]
    for vk in codes:
        user32.keybd_event(vk, 0, 0x1 if vk in _EXTENDED else 0, 0)
        time.sleep(0.02)
    for vk in reversed(codes):
        user32.keybd_event(vk, 0, (0x1 if vk in _EXTENDED else 0) | 0x2, 0)
        time.sleep(0.02)


def paste_text(text: str, clear: bool = True) -> None:
    """把 text 输入到当前光标所在的输入框。

    走剪贴板粘贴（ctrl+v），不逐字打，这样不受中文输入法影响。
    clear=True 时先 ctrl+a 全选，粘贴会覆盖掉框里原有的内容（比如残留的草稿）。
    往搜索框这种“不确定光标在哪”的地方输入时，用 clear=False 更稳：万一光标在别人的聊天框，也不会抹掉草稿。
    注意：会覆盖系统剪贴板原来的内容。
    """
    auto.SetClipboardText(text)
    time.sleep(0.05)
    if clear:
        press('ctrl+a')
        time.sleep(0.1)
    press('ctrl+v')
    time.sleep(0.1)


def scroll(x: int, y: int, ticks: int) -> None:
    """鼠标移到屏幕坐标 (x, y) 后滚轮滚动 ticks 格。正数向下，负数向上。"""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0800, 0, 0, -120 * ticks, 0)
