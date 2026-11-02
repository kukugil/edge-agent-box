"""窗口管理：找窗口、激活、确认它真的能响应。

为什么要单独“确认能响应”：微信按 Esc 会藏到托盘；用 Win32 强行显示出来后，
窗口看着正常，但点它、打字都没反应。只看“窗口在不在、可不可见”发现不了，
必须做一次无害的操作（鼠标悬停），看界面有没有跟着变。
"""
import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image

from .screen import Shot, screen_changed, screenshot

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

SW_RESTORE = 9
VK_MENU = 0x12


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    visible: bool    # False = 隐藏（比如微信藏到了托盘）
    minimized: bool


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return pid.value


def _text(fn, hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    fn(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def find_windows(title: str | None = None, class_prefix: str | None = None,
                 pid: int | None = None) -> list[WindowInfo]:
    """列出符合条件的顶层窗口（包括隐藏的）。条件不传就不限制。

    例：find_windows(title='微信', class_prefix='Qt')  → 微信 4.x 主窗口
    用 Win32 枚举而不是 UI Automation，因为 UIA 列不出隐藏窗口，就没法发现“藏在托盘”这种情况。
    """
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        t = _text(user32.GetWindowTextW, hwnd)
        c = _text(user32.GetClassNameW, hwnd)
        if ((title is None or t == title) and (class_prefix is None or c.startswith(class_prefix))
                and (pid is None or _pid(hwnd) == pid)):
            found.append(WindowInfo(hwnd, t, c, _pid(hwnd),
                                    bool(user32.IsWindowVisible(hwnd)), bool(user32.IsIconic(hwnd))))
        return True

    user32.EnumWindows(cb, 0)
    return found


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """窗口在屏幕上看得见的区域 (left, top, right, bottom)，可直接传给 screenshot()。

    Win10 的普通窗口四周有几像素看不见的拖拽边框，GetWindowRect 会把它算进去；
    这里优先用 DWM 给的“实际画出来的边界”，截图就不会带上边框外的桌面。
    """
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))  # DWMWA_EXTENDED_FRAME_BOUNDS 取不到时退回
    return r.left, r.top, r.right, r.bottom


def _foreground_is(hwnd: int) -> bool:
    # 同一进程的弹出窗口（下拉框、菜单）在前台也算，不然一激活就把下拉框关掉了
    fg = user32.GetForegroundWindow()
    return bool(fg) and _pid(fg) == _pid(hwnd)


def activate(hwnd: int, wait: float = 0.5) -> tuple[bool, str]:
    """把窗口切到前台。返回 (是否成功, 原因)。

    - 窗口隐藏（藏在托盘）：不强行显示，直接返回失败，让人去点托盘图标。
      强行显示后的窗口不响应输入，比失败更糟：后面的操作全是空操作，还不报错。
    - 最小化：正常还原。
    - 已经在前台：什么都不做。
    """
    if not user32.IsWindow(wintypes.HWND(hwnd)):
        return False, '窗口已不存在'
    if not user32.IsWindowVisible(wintypes.HWND(hwnd)):
        return False, '窗口是隐藏的（可能藏在托盘），请手动点托盘图标恢复'
    if user32.IsIconic(wintypes.HWND(hwnd)):  # 先还原：最小化的窗口也可能仍被算作“前台”
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        time.sleep(0.5)
    if _foreground_is(hwnd):
        return True, '已在前台'
    # Windows 不许后台程序随便抢前台（“前台锁”），两个常用办法轮流试：
    #   1. 先按一下 Alt（模拟“用户刚操作过”），SetForegroundWindow 才会生效
    #   2. 把自己临时挂到当前前台窗口的线程上（AttachThreadInput），借它的“前台身份”切过去
    for attempt in range(3):
        if attempt == 0:
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, 2, 0)
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
        else:
            fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
            me = ctypes.windll.kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(me, fg_thread, True)
            user32.BringWindowToTop(wintypes.HWND(hwnd))
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
            user32.AttachThreadInput(me, fg_thread, False)
        time.sleep(wait)
        if _foreground_is(hwnd):
            return True, '已切到前台' + (f'（第 {attempt + 1} 次尝试）' if attempt else '')
    return False, '切换前台失败（可能被别的程序或系统拦下）'


def check_responsive(hwnd: int, hover_points: list[tuple[int, int]] | None = None,
                     timeout_ms: int = 1000) -> tuple[bool, str]:
    """确认窗口真的能响应。返回 (是否能响应, 原因)。不点击、不打字。

    逐级检查，哪级不过就停在哪级：
      1. 窗口存在、可见、没最小化
      2. 程序没卡死：给窗口发一条空消息（WM_NULL），timeout_ms 内有回音才算活着
      3. 窗口（或它的弹出层）在前台
      4. 悬停测试（传了 hover_points 才做）：鼠标先停在第 1 个点、再移到第 2 个点，
         两次截图要有变化——能响应的窗口会画出悬停高亮，不响应的窗口画面纹丝不动。
         hover_points 是截图内坐标，要选一块“鼠标放上去会变色”的区域，每个应用不同，
         例如微信左侧会话列表里相邻的两行。
    """
    h = wintypes.HWND(hwnd)
    if not user32.IsWindow(h):
        return False, '窗口已不存在'
    if not user32.IsWindowVisible(h):
        return False, '窗口是隐藏的'
    if user32.IsIconic(h):
        return False, '窗口是最小化的'
    result = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(h, 0, 0, 0, 0x2, timeout_ms, ctypes.byref(result)):  # 0x2 = 已挂起就立即返回
        return False, f'程序没有响应（{timeout_ms}ms 内不回消息，可能卡死）'
    if not _foreground_is(hwnd):
        return False, '窗口不在前台，先调用 activate()'
    if not hover_points:
        return True, '程序在运行且在前台（没做悬停测试）'

    rect = window_rect(hwnd)
    (x1, y1), (x2, y2) = hover_points[:2]
    user32.SetCursorPos(rect[0] + x1, rect[1] + y1)
    time.sleep(0.4)
    before = screenshot(rect).image
    user32.SetCursorPos(rect[0] + x2, rect[1] + y2)
    time.sleep(0.4)
    after = screenshot(rect).image
    if screen_changed(before, after, threshold=8):  # 悬停高亮颜色很浅，阈值要比判断“界面变化”低
        return True, '悬停测试通过：鼠标移动后界面有变化'
    return False, '悬停测试失败：鼠标移动后界面没有任何变化，窗口可能不响应输入'


# 用独立的 WinDLL 对象：下面要设 argtypes，和上面共用的 ctypes.windll 分开，互不影响
_u32 = ctypes.WinDLL('user32')
_gdi = ctypes.WinDLL('gdi32')
# 64 位下句柄是 8 字节，不写清类型会被截成 4 字节，出现莫名其妙的失败
_u32.GetWindowDC.argtypes = [wintypes.HWND]
_u32.GetWindowDC.restype = ctypes.c_void_p
_u32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
_u32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi.CreateCompatibleDC.restype = ctypes.c_void_p
_gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
_gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi.SelectObject.restype = ctypes.c_void_p
_gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
_gdi.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi.DeleteDC.argtypes = [ctypes.c_void_p]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]


def capture_window(hwnd: int) -> Shot | None:
    """让窗口自己交一张画面（PrintWindow），窗口被别的窗口挡住也能拿到。

    输入：hwnd 窗口句柄
    输出：Shot，范围和 screenshot(window_rect(hwnd)) 一样；窗口最小化、隐藏或截图失败时返回 None
    和 screenshot() 的区别：screenshot 截的是屏幕上看得见的像素，窗口被挡住就截到挡住它的东西。
    PW_RENDERFULLCONTENT=2：GPU 画的窗口（Qt、Chrome）不加这个标志，截出来通常是黑的。
    """
    h = wintypes.HWND(hwnd)
    if not user32.IsWindowVisible(h) or user32.IsIconic(h):
        return None
    wr = wintypes.RECT()
    _u32.GetWindowRect(h, ctypes.byref(wr))
    w, h_px = wr.right - wr.left, wr.bottom - wr.top
    hdc = _u32.GetWindowDC(h)
    mdc = _gdi.CreateCompatibleDC(hdc)
    bmp = _gdi.CreateCompatibleBitmap(hdc, w, h_px)
    old = _gdi.SelectObject(mdc, bmp)
    try:
        ok = _u32.PrintWindow(h, mdc, 2)
        bmi = _BITMAPINFOHEADER(biSize=ctypes.sizeof(_BITMAPINFOHEADER), biWidth=w, biHeight=-h_px,  # 负数=从上往下
                                biPlanes=1, biBitCount=32, biCompression=0)
        buf = ctypes.create_string_buffer(w * h_px * 4)
        _gdi.GetDIBits(mdc, bmp, 0, h_px, buf, ctypes.byref(bmi), 0)
    finally:
        _gdi.SelectObject(mdc, old)
        _gdi.DeleteObject(bmp)
        _gdi.DeleteDC(mdc)
        _u32.ReleaseDC(h, hdc)
    if not ok:
        return None
    im = Image.frombuffer('RGB', (w, h_px), buf, 'raw', 'BGRX', 0, 1)
    # GetWindowRect 含看不见的拖拽边框，裁成和 window_rect 一样的范围
    l, t, r, b = window_rect(hwnd)
    return Shot(im.crop((l - wr.left, t - wr.top, r - wr.left, b - wr.top)), l, t)
