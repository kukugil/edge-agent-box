"""分辨"真人碰了键鼠"还是"程序自己发的键鼠"。

为什么：GetLastInputInfo 和"鼠标位置有没有变"都分不清是谁动的。11/20、11/21 真机读通讯录时，
没人用电脑也会差 1～9 像素，程序误以为人来了就停下；反过来程序自己的点击又会把空闲时间清零。
做法（微软 MSLLHOOKSTRUCT 文档）：低级鼠标/键盘钩子里，程序用 SendInput / mouse_event / keybd_event
发出的事件带 LLMHF_INJECTED / LLKHF_INJECTED 标志，真人的没有。

坑：钩子回调必须很快返回，超时会被系统悄悄摘掉；所以放在专用线程里，回调只记一个时间。
"""

import ctypes
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x0012
LLMHF_INJECTED, LLKHF_INJECTED = 0x01, 0x10

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('pt', wintypes.POINT), ('mouseData', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', wintypes.DWORD), ('scanCode', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class HumanInput:
    """后台记下最近一次真人键鼠输入的时刻。

    用法：with HumanInput() as human: ... if human.since(t): 人在 t 之后碰过键鼠
    ok 为 False 表示钩子没装上，调用方要退回别的判断方法。
    """

    def __init__(self):
        self.last = 0.0          # time.monotonic() 时刻；0 表示还没有真人输入
        self.count = 0
        self.ok = False
        self._thread = None
        self._tid = 0
        self._ready = threading.Event()

    def since(self, moment: float) -> bool:
        """moment 之后有没有真人输入。"""
        return self.last > moment

    def _run(self):
        def on_mouse(code, wparam, lparam):
            if code >= 0 and not (ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents.flags & LLMHF_INJECTED):
                self.last = time.monotonic()
                self.count += 1
            return user32.CallNextHookEx(None, code, wparam, lparam)

        def on_key(code, wparam, lparam):
            if code >= 0 and not (ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.flags & LLKHF_INJECTED):
                self.last = time.monotonic()
                self.count += 1
            return user32.CallNextHookEx(None, code, wparam, lparam)

        # 回调对象要一直被引用着，不然被回收后系统回调到野指针直接崩
        self._procs = (HOOKPROC(on_mouse), HOOKPROC(on_key))
        self._tid = kernel32.GetCurrentThreadId()
        module = kernel32.GetModuleHandleW(None)
        hooks = [user32.SetWindowsHookExW(WH_MOUSE_LL, self._procs[0], module, 0),
                 user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._procs[1], module, 0)]
        self.ok = all(hooks)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        for hook in hooks:
            if hook:
                user32.UnhookWindowsHookEx(hook)

    def start(self):
        self._thread = threading.Thread(target=self._run, name='human-input-hook', daemon=True)
        self._thread.start()
        self._ready.wait(2)
        return self

    def stop(self):
        if self._thread and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(2)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
