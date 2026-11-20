"""鼠标键盘：点击、按键、粘贴、读输入框、滚动。坐标一律是屏幕坐标。

D11 加了两道底层拦截（反例清单第 18、31 条），拦下时抛 InputRefused，键鼠一下都不动：
  - press 拒绝 Esc、Alt+F4
  - paste_text 默认先读输入框，里面有字就不粘
D12 加了一道（第 32、33 条）：click 拒绝落在窗口右上角关闭、最大化、最小化那一块


现在是电脑本机用 Win32 模拟输入；以后换成盒子的 USB 键鼠时，只要换掉这个文件里的实现，函数签名不变。
"""
import ctypes
import ctypes.wintypes
import time

import uiautomation as auto

user32 = ctypes.windll.user32

_MOUSE = {  # 按下、抬起的事件码
    'left': (0x0002, 0x0004),
    'right': (0x0008, 0x0010),
}


class InputRefused(RuntimeError):
    """底层拒绝执行的键鼠动作。

    press 拒绝时一个键都没发；paste_text 拒绝时可能已经按过 ctrl+a / ctrl+c / ctrl+end（读输入框用的），
    但没有改动框里的字，也没有按回车。

    为什么用抛异常而不是返回 False：调用的人忘了检查返回值时，程序会直接停下，
    而不是带着"没做成"的状态继续往下走、接着按回车。停下比误发好。
    """


# 底层直接拒绝的按键（反例清单第 31 条）。主循环的规矩也会拦，这里是第二道：
# 任何代码、任何模型输出走到这里都按不出去。
#   esc：微信会藏进托盘，强行显示后可能不再响应输入
#   alt+f4：关窗口
_FORBIDDEN = {('esc',): 'Esc 会把微信藏进托盘', ('alt', 'f4'): 'Alt+F4 会关掉窗口'}

# 按键名 → Windows 虚拟键码。只收常用的，不认识的名字直接报错，免得按错键。
# 故意不收 esc：就算绕过 _FORBIDDEN，查表这一步也按不出去
_VK = {
    'ctrl': 0x11, 'shift': 0x10, 'alt': 0x12, 'win': 0x5B,
    'enter': 0x0D, 'tab': 0x09, 'backspace': 0x08, 'delete': 0x2E, 'space': 0x20,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
    'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
    **{f'f{i}': 0x6F + i for i in range(1, 13)},
    **{c: ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'},
}
_EXTENDED = {0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22, 0x2E}  # 方向键等要带“扩展键”标志


# 窗口右上角的禁点区（反例清单第 32、33 条）：关闭、最大化、最小化都在这里。
# 微信 4.x 和 Chrome 这三个按钮加起来约 140px 宽、30–40px 高（物理像素），多留一点余量。
_CORNER_W, _CORNER_H = 200, 40


class _RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


# 单独一份 user32、标好句柄类型：不标时句柄按 32 位 int 传，大于 2^31 的句柄会报 OverflowError，
# 点击直接失败（11/19 新开的 Chrome 窗口就是这样）。单独一份是为了不改动别的模块共用的 user32
_u32 = ctypes.WinDLL('user32')
_u32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
_u32.WindowFromPoint.restype = ctypes.wintypes.HWND
_u32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
_u32.GetAncestor.restype = ctypes.wintypes.HWND
_u32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(_RECT)]


def _window_rect_at(x: int, y: int):
    """屏幕上 (x, y) 那一点所属的顶层窗口的 (左, 上, 右, 下)；拿不到交回 None。"""
    hwnd = _u32.WindowFromPoint(ctypes.wintypes.POINT(int(x), int(y)))
    root = _u32.GetAncestor(hwnd, 2) if hwnd else None   # 2 = GA_ROOT：点在子控件上也要找到外面那层窗口
    r = _RECT()
    if not root or not _u32.GetWindowRect(root, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def click_refused_reason(x: int, y: int) -> str:
    """(x, y) 落在窗口右上角关闭/最大化/最小化那一块就交回原因，否则交回 ''。

    为什么在底层拦：规矩只看模型的描述和截图坐标，模型说点"安全按钮"、坐标却落在关闭上时拦不住（第 29 条）。
    这里按点击时屏幕上真正在那一点的窗口算，换了窗口位置也算得对。
    """
    rect = _window_rect_at(x, y)
    if rect is None:
        return ''
    l, t, r, b = rect
    if y - t < _CORNER_H and r - x < _CORNER_W:
        return '点在窗口右上角关闭、最大化、最小化按钮那一块'
    return ''


def click(x: int, y: int, button: str = 'left', double: bool = False) -> None:
    """在屏幕坐标 (x, y) 点一下。button 是 'left' 或 'right'；double=True 双击。

    落在窗口右上角关闭、最大化、最小化那一块时抛 InputRefused，鼠标一下都不动。
    """
    why = click_refused_reason(x, y)
    if why:
        raise InputRefused(f'不许点 ({int(x)}, {int(y)})：{why}')
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
    Esc、Alt+F4 抛 InputRefused（组合里带 esc 也拒绝，比如 'shift+esc'）。
    """
    names = [k.strip().lower() for k in keys.split('+')]
    for combo, why in _FORBIDDEN.items():
        if set(combo) <= set(names):
            raise InputRefused(f'不许按 {keys}：{why}')
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


_SENTINEL = '⁣agent-clipboard-probe⁣'   # 不会出现在正常文字里的记号，用来判断"到底复制到东西没有"


def read_focused_text() -> str:
    """读出当前光标所在输入框里的全部文字，不改动框里的内容。

    做法：先往剪贴板放一个记号 → ctrl+a 全选 → ctrl+c 复制 → 读剪贴板 → ctrl+end 取消选中。
    输出：框里的文字；框是空的，或者复制没发生，都交回 ''。

    坑：
      - 交回 '' 分不清"框是空的"和"焦点不在可复制的地方"。所以只能拿它判断"有没有草稿"，
        不能拿它证明"字已经输进去了"。证明输进去要另外核对（D12 的读回）。
      - 最后一定要取消选中：选中状态留着，用户接着打一个字，草稿就被替换掉了。
      - 会覆盖剪贴板原来的内容（D12 再做保存和恢复）。
    """
    auto.SetClipboardText(_SENTINEL)
    time.sleep(0.05)
    press('ctrl+a')
    time.sleep(0.05)
    press('ctrl+c')
    time.sleep(0.1)
    got = auto.GetClipboardText() or ''
    press('ctrl+end')
    return '' if got == _SENTINEL else got


def paste_text(text: str, mode: str = 'empty_only') -> None:
    """把 text 输入到当前光标所在的输入框。走剪贴板粘贴（ctrl+v），不受中文输入法影响。

    mode 三种（反例清单第 18 条：用户打了一半的字不能被覆盖，也不能被一起发出去）：
      'empty_only'（默认）：先读框里有没有字，有就抛 InputRefused，一个字都不粘。
                   粘完再读一次，框里必须正好是 text，否则也抛 InputRefused。
                   给"马上要按回车发送"的地方用，比如聊天输入框。
      'append'   ：不看不管，直接在光标处追加。给搜索框、收件人栏这种不会被直接发出去的地方用。
      'replace'  ：先 ctrl+a 全选再粘贴，覆盖原有内容；粘完同样读回，必须正好是 text。
                   只在确定框里不是用户的字时用：记事本演示、邮件回信正文（框里是邮箱自动带的引用原文，
                   11/9 实测 130 个字符，用 empty_only 会被当成草稿拒绝）。
    注意：会覆盖系统剪贴板原来的内容。
    """
    if mode not in ('empty_only', 'append', 'replace'):
        raise ValueError(f'不认识的 mode：{mode}')
    if mode == 'empty_only':
        draft = read_focused_text()
        if draft == text:
            # 框里正好是这句：上一步已经粘过（比如核对没看清又让模型重打一次），不再粘第二遍
            return
        if draft:
            # 只有空格、换行也算草稿（不 strip）：那也是用户放进去的，不替他决定。
            # 不在报错里带出草稿内容：那是用户自己的字，可能是聊天内容
            raise InputRefused(f'输入框里已经有 {len(draft)} 个字符（可能是用户打了一半的草稿），不粘贴，交给人处理')
    auto.SetClipboardText(text)
    time.sleep(0.05)
    if mode == 'replace':
        press('ctrl+a')
        time.sleep(0.1)
    press('ctrl+v')
    time.sleep(0.1)
    if mode in ('empty_only', 'replace'):
        # replace 也要读回：全选可能没选中整框（焦点在别处、编辑器只选了一段），剩下的字会跟着发出去（反例清单第 21 条）
        # 粘完读回。为什么要这一步：粘之前读到 '' 分不清"框是空的"和"复制没成功"，
        # 后一种情况下草稿还在，这次就粘到了草稿后面。粘完框里必然有字，复制正常就一定读得到 text。
        #   读到 ''：复制还是不成，或者焦点根本不在输入框 → 不确定，拒绝
        #   读到别的：草稿 + 这句，或者字粘到了别处 → 拒绝
        # 拒绝时不去删框里的字：分不清哪些是用户的，留给人看。坏处是那种情况下草稿后面会多出这句，但不会被发出去
        back = read_focused_text()
        if back != text:
            raise InputRefused('粘贴后读回的不是这句' + ('（读不到输入框，可能焦点不在输入框或复制失败）' if back == '' else
                                                '（框里还有别的字）') + '，不许接着发送，交给人处理')


def scroll(x: int, y: int, ticks: int) -> None:
    """鼠标移到屏幕坐标 (x, y) 后滚轮滚动 ticks 格。正数向下，负数向上。"""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0800, 0, 0, -120 * ticks, 0)
