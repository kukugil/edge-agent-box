"""探测：微信来新消息时，程序能从哪些信号发现它、要多久（风险 1 的快速探测，为 D9 触发器选方案）。

同时盯四种信号：
  闪烁    任务栏按钮闪烁。系统 shell 钩子的 HSHELL_FLASH 事件，不用截图，窗口被挡住也照样收到
  标题    微信窗口标题有没有变（HSHELL_REDRAW 事件 + 轮询 GetWindowText）
  导航角标 左侧导航栏“聊天”图标上的总未读数。分别用两种截图看：
           截屏      ImageGrab，截的是屏幕上看得见的像素，窗口被挡住就截到挡住它的东西
           后台截图  PrintWindow，让窗口自己画一份，被挡住也可能截得到——要测的就是这个
  行角标  “文件传输助手”那一行头像上的未读数。用 OCR 只核对这一行是不是“文件传输助手”，别的行名不打印不保存

真实账号上本来就有未读，所以一律看“变化”，不看“有没有红点”。不存截图，结果只打印并写一份 JSON 到系统临时目录。

用法（在项目根目录）：
  python -m agent.demos.probe_new_msg --calibrate          先校准：看红色角标能不能被找到，后台截图是不是黑的
  python -m agent.demos.probe_new_msg --state covered      微信被别的窗口挡住（不要最小化），盯 90 秒
  python -m agent.demos.probe_new_msg --state front        微信在前台（但停在别的会话），盯 90 秒
  可选：--seconds 90，--cues（屏幕右上角到点提示什么时候发）
运行期间用手机往文件传输助手发消息，脚本按时间顺序列出每个信号的每次变化。每条消息之间隔十几秒，才分得清谁触发了谁。
"""
import argparse
import ctypes
import hashlib
import json
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from agent.tools import activate, find_windows, norm, ocr, screenshot, window_rect

# 用独立的 WinDLL 对象：下面要给函数设 argtypes，和 agent.tools 共用 ctypes.windll.user32 会互相影响
u32 = ctypes.WinDLL('user32', use_last_error=True)
gdi = ctypes.WinDLL('gdi32')
k32 = ctypes.WinDLL('kernel32')

HSHELL_REDRAW = 6
HSHELL_FLASH = 0x8006  # = HSHELL_REDRAW | HSHELL_HIGHBIT
LEFT_W = 300  # 导航栏 + 会话列表大约的宽度（截图内坐标），角标只在这块里找
NAV_W = 70    # 导航栏宽度：x 小于它的红点算导航栏角标
POLL = 0.3

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int), ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HBRUSH),
                ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]


# 64 位下句柄是 8 字节，不写 argtypes/restype 会被截成 4 字节，出现莫名其妙的失败
k32.GetModuleHandleW.restype = wintypes.HMODULE
u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.DefWindowProcW.restype = LRESULT
u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
u32.CreateWindowExW.restype = wintypes.HWND
u32.RegisterShellHookWindow.argtypes = [wintypes.HWND]
u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetWindowDC.argtypes = [wintypes.HWND]
u32.GetWindowDC.restype = ctypes.c_void_p
u32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
u32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
u32.GetForegroundWindow.restype = wintypes.HWND
gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi.CreateCompatibleDC.restype = ctypes.c_void_p
gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi.SelectObject.restype = ctypes.c_void_p
gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi.DeleteObject.argtypes = [ctypes.c_void_p]
gdi.DeleteDC.argtypes = [ctypes.c_void_p]


def pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def title_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


# ---------- 信号 1、2：shell 钩子 ----------

def start_shell_hook(on_event):
    """开一个隐藏窗口接收系统 shell 事件，每来一个 HSHELL_FLASH / HSHELL_REDRAW 就调用 on_event(类型, hwnd)。

    Windows 只把 shell 事件发给注册过的窗口，而且要在创建它的线程里跑消息循环，所以单开一个线程。
    """
    ready = threading.Event()

    def run():
        hinst = k32.GetModuleHandleW(None)
        msg_id = u32.RegisterWindowMessageW('SHELLHOOK')

        @WNDPROC
        def proc(hwnd, msg, wp, lp):
            if msg == msg_id and wp in (HSHELL_FLASH, HSHELL_REDRAW):
                on_event('flash' if wp == HSHELL_FLASH else 'redraw', lp)
            return u32.DefWindowProcW(hwnd, msg, wp, lp)

        wc = WNDCLASSW(lpfnWndProc=proc, hInstance=hinst, lpszClassName='agent-probe-shellhook')
        u32.RegisterClassW(ctypes.byref(wc))
        hwnd = u32.CreateWindowExW(0, wc.lpszClassName, 'agent-probe', 0, 0, 0, 0, 0, None, None, hinst, None)
        ok = bool(hwnd) and bool(u32.RegisterShellHookWindow(hwnd))
        ready.ok = ok
        ready.set()
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=run, daemon=True).start()
    ready.wait(3)
    return getattr(ready, 'ok', False)


# ---------- 信号 3、4：两种截图 + 找红色角标 ----------

def print_window(hwnd) -> tuple[Image.Image | None, bool]:
    """让窗口自己把画面画到内存里（PrintWindow），窗口被挡住时也可能截得到。

    输出：(图, PrintWindow 是否返回成功)。图按 window_rect 裁好，坐标和 screenshot() 的一致。
    PW_RENDERFULLCONTENT=2：用 GPU 合成的窗口（Qt、Chrome）不加这个标志通常截出来是黑的。
    """
    wr = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    if w <= 0 or h <= 0:
        return None, False
    hdc = u32.GetWindowDC(hwnd)
    mdc = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    old = gdi.SelectObject(mdc, bmp)
    try:
        ok = bool(u32.PrintWindow(hwnd, mdc, 2))
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,  # 负数=从上往下
                               biPlanes=1, biBitCount=32, biCompression=0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    finally:
        gdi.SelectObject(mdc, old)
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(mdc)
        u32.ReleaseDC(hwnd, hdc)
    im = Image.frombuffer('RGB', (w, h), buf, 'raw', 'BGRX', 0, 1)
    # GetWindowRect 含看不见的拖拽边框，裁成和 window_rect 一样的范围
    l, t, r, b = window_rect(hwnd)
    return im.crop((l - wr.left, t - wr.top, r - wr.left, b - wr.top)), ok


def _runs(idx, gap=2):
    # 把排好序的下标分成连续的几段，例如 [3,4,5,9,10] → [[3,4,5],[9,10]]
    out = []
    for i in idx:
        if out and i - out[-1][-1] <= gap:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def red_blobs(im: Image.Image) -> list[tuple[int, int, int, int]]:
    """在左侧（导航栏 + 会话列表）找红色未读角标，返回外接框 (x1, y1, x2, y2) 列表，截图内坐标。

    微信角标是 #FA5151 左右的红底白字小圆，直径约 16px。先按行分段再按列分段，近似“连通块”；
    小于 8px 的丢掉，过滤头像里零星的红色像素。
    """
    a = np.asarray(im.crop((0, 0, min(LEFT_W, im.width), im.height)))
    m = (a[..., 0] > 220) & (a[..., 1] < 110) & (a[..., 2] < 110)
    blobs = []
    for ys in _runs(np.where(m.any(1))[0]):
        sub = m[ys[0]:ys[-1] + 1]
        for xs in _runs(np.where(sub.any(0))[0]):
            # 同一行带里可能有好几个红点，高度要各算各的
            inner = np.where(sub[:, xs[0]:xs[-1] + 1].any(1))[0]
            y1, y2 = ys[0] + inner[0], ys[0] + inner[-1] + 1
            if len(xs) >= 8 and y2 - y1 >= 8:
                blobs.append((int(xs[0]), int(y1), int(xs[-1]) + 1, int(y2)))
    return blobs


def _sig(im, box):
    # 角标里的数字变了，像素就变了；用哈希比较，不保存图
    return hashlib.md5(im.crop(box).tobytes()).hexdigest()[:8]


class BadgeReader:
    """从一张截图里读出“导航栏角标签名”和“文件传输助手行角标签名”。

    哪个红点属于文件传输助手要靠 OCR 认行名，OCR 慢，所以只在红点位置变了的时候重认。
    """

    def __init__(self):
        self._layout, self._fh = None, None

    def read(self, im: Image.Image) -> dict:
        blobs = red_blobs(im)
        nav = [b for b in blobs if b[0] < NAV_W]
        rows = [b for b in blobs if b[0] >= NAV_W]
        layout = tuple(b[1] // 4 for b in rows)
        if layout != self._layout:
            self._layout, self._fh = layout, None
            for b in rows:
                # 角标在头像右上角，行名在它右边、稍靠下
                strip = im.crop((b[0], max(0, b[1] - 5), min(LEFT_W, im.width), b[1] + 40))
                if any('文件传' in norm(it['text']) for it in ocr(strip)):
                    self._fh = b
                    break
        return {'nav': _sig(im, nav[0]) if nav else None,
                'fh': _sig(im, self._fh) if self._fh else None,
                'n_nav': len(nav), 'n_rows': len(rows), 'fh_found': self._fh is not None}


# ---------- 到点提示人用手机发消息 ----------

# (从第几秒起, 提示文字, 是否响一声)
CUES = [(0, '准备好手机，先别发', False), (6, '现在发 1 条', True), (11, '等一等，别碰电脑', False),
        (26, '现在连发 2 条', True), (32, '等一等，别碰电脑', False), (47, '测完了，可以回来了', True)]


def show_cues(cues, seconds):
    """屏幕右上角放一个总在最上层的小提示框，到点换文字并响一声，seconds 秒后自动关掉。

    微信在前台时会盖住其他窗口，人看不到别处的提示，所以提示框要自己置顶，并且放在微信右边。
    它只在开始时抢一下前台，随后 watch() 里的 activate() 会把前台还给微信。
    """
    def run():
        import tkinter as tk
        import winsound
        root = tk.Tk()
        root.overrideredirect(True)  # 不要标题栏，免得被误点
        root.attributes('-topmost', True)
        root.geometry(f'460x170+{root.winfo_screenwidth() - 490}+40')
        label = tk.Label(root, font=('Microsoft YaHei', 26), bg='#FFF3C4', fg='#633806', wraplength=430)
        label.pack(fill='both', expand=True)
        t0 = time.time()
        shown = [None]

        def tick():
            now = time.time() - t0
            if now >= seconds:
                root.destroy()
                return
            cur = [c for c in cues if c[0] <= now][-1]
            if cur is not shown[0]:
                shown[0] = cur
                label.config(text=cur[1])
                if cur[2]:
                    winsound.Beep(880, 300)
            root.after(200, tick)

        tick()
        root.mainloop()

    threading.Thread(target=run, daemon=True).start()


# ---------- 主流程 ----------


def find_wechat():
    for w in find_windows(title='微信', class_prefix='Qt'):
        if w.visible and w.minimized:
            sys.exit('微信是最小化的，请先点开（可以被别的窗口挡住，但不要最小化）再运行')
        l, t, r, b = window_rect(w.hwnd)
        if w.visible and r - l > 300:
            return w.hwnd
    return None


def calibrate(hwnd):
    shot = screenshot(window_rect(hwnd))
    pw, ok = print_window(hwnd)
    print(f'窗口大小 {shot.image.size}，前台：{pid_of(u32.GetForegroundWindow()) == pid_of(hwnd)}')
    for name, im in (('截屏', shot.image), ('后台截图', pw)):
        if im is None:
            print(f'[{name}] 没截到')
            continue
        mean = float(np.asarray(im).mean())
        blobs = red_blobs(im)
        r = BadgeReader().read(im)
        print(f'[{name}] 平均亮度 {mean:.0f}（接近 0 说明是黑图）')
        print(f'  红点 {len(blobs)} 个：{blobs}')
        print(f'  导航栏角标 {r["n_nav"]} 个，会话行角标 {r["n_rows"]} 个，认出文件传输助手那一行：{r["fh_found"]}')
    print(f'PrintWindow 返回：{ok}')
    print('导航栏角标应该正好 1 个；有未读的会话行应该各 1 个。不对的话把上面的红点坐标发给克老师调 NAV_W / LEFT_W。')


def watch(hwnd, state, seconds, events) -> tuple[list, dict]:
    """盯 seconds 秒，按时间顺序记下每种信号的每一次变化。

    输出：(时间线 [(毫秒, 信号名), ...], 环境情况)。不算“延迟”，只比各信号之间谁先谁后、响几次。
    """
    if state == 'front':
        ok, why = activate(hwnd)
        if not ok:
            sys.exit(f'激活微信失败：{why}')
    readers = {'screen': BadgeReader(), 'printwin': BadgeReader()}

    def snap():
        shot = screenshot(window_rect(hwnd)).image
        pw, _ = print_window(hwnd)
        return {'title': title_of(hwnd), 'screen': readers['screen'].read(shot),
                'printwin': readers['printwin'].read(pw) if pw else {'nav': None, 'fh': None}}

    last = snap()
    env = {'微信在前台': pid_of(u32.GetForegroundWindow()) == pid_of(hwnd)}
    if state == 'covered' and env['微信在前台']:
        sys.exit('微信在最前面，没有被挡住，这次不算。先点一下别的窗口盖住它再运行')
    print(f'开始盯 {seconds:.0f} 秒。{env}', flush=True)
    events.clear()
    t0 = time.time()
    line = []

    def hit(name, t):
        line.append((int((t - t0) * 1000), name))
        print(f'  {(t - t0):5.1f} 秒  {name}', flush=True)

    while time.time() - t0 < seconds:
        time.sleep(POLL)
        now, cur = time.time(), snap()
        if cur['title'] != last['title']:
            hit('标题', now)
        for src, label in (('screen', '截屏'), ('printwin', '后台截图')):
            if cur[src]['nav'] != last[src]['nav']:
                hit(f'导航角标·{label}', now)
            if cur[src]['fh'] != last[src]['fh']:
                hit(f'文件传输助手行·{label}', now)
        last = cur
    wechat_pid = pid_of(hwnd)
    for t, kind, lp in list(events):
        if pid_of(lp) == wechat_pid:
            line.append((int((t - t0) * 1000), '闪烁' if kind == 'flash' else '重绘事件'))
    return sorted(line), env


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser()
    ap.add_argument('--calibrate', action='store_true')
    ap.add_argument('--state', choices=['front', 'covered'], default='covered')
    ap.add_argument('--seconds', type=float, default=90)
    ap.add_argument('--cues', action='store_true', help='到点提示人发消息')
    args = ap.parse_args()

    hwnd = find_wechat()
    if not hwnd:
        sys.exit('没找到可见的微信主窗口（藏在托盘的话，请先点托盘图标）')
    if args.calibrate:
        calibrate(hwnd)
        return

    events = []
    if not start_shell_hook(lambda kind, lp: events.append((time.time(), kind, lp))):
        print('注意：shell 钩子注册失败，“闪烁”这一项会一直是空的')
    if args.cues:
        show_cues(CUES, args.seconds)
        time.sleep(1)  # 等提示框出来，再把前台交给微信
    line, env = watch(hwnd, args.state, args.seconds, events)
    print('\n===== 时间线（秒） =====')
    for ms, name in line:
        print(f'{ms / 1000:6.1f}  {name}')

    out = Path(tempfile.gettempdir()) / 'agent-probe' / f'{datetime.now():%m%d-%H%M%S}-{args.state}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'state': args.state, 'env': env, 'timeline': line}, ensure_ascii=False, indent=2),
                   encoding='utf-8')
    print(f'\n结果存在 {out}')


if __name__ == '__main__':
    main()
