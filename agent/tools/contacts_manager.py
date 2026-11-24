"""读微信"通讯录管理"窗口里的好友列表（昵称、备注、标签），给托盘勾选回复名单用。

11/20 真机实验（docs/白名单选择-方案.md 末尾）定下的做法：
- 窗口要用户自己在微信里打开（通讯录 → 通讯录管理）。程序不去点微信主窗口。
- 每一行的读屏文字是"昵称 备注 标签"，三段之间各一个空格，空字段留空；行没有子控件、没有 AutomationId。
  昵称、备注、标签本身也可能带空格，这种行拆不准，标成 ambiguous，界面上不让勾。
- 列表不支持 ScrollPattern，只能把鼠标放到列表上滚轮。读的过程中如果发现鼠标被人挪走，立刻停下。
- 群聊不在这个窗口里（左栏"最近群聊"不碰），所以这里读出来的都是好友。

只读控件和滚轮；唯一的点击是左栏"标签"没展开时点一下它，好读出标签名。不输入文字。
"""

import ctypes
import re
import time
from ctypes import wintypes

import uiautomation as auto

WINDOW_NAME = '通讯录管理'
LIST_CLASS = 'mmui::ContactsManagerDetailView'
ALL_CLASS = 'mmui::ContactsManagerControlAllCell'
TAG_SPLIT = re.compile(r'[，,、;；]')

user32 = ctypes.windll.user32


class ReadStopped(Exception):
    """读到一半停下：人动了鼠标、窗口没了或被要求停止。message 是给人看的原因。"""


def parse_row(text: str, known_tags=()) -> dict:
    """输入一行读屏文字和已知标签名，输出 {raw, nickname, remark, tags, title, ok}。

    title 是微信会话列表里显示的名字：有备注用备注，没有用昵称。白名单按它匹配会话。
    行的格式是"昵称 备注 标签列"，标签列里多个标签用逗号隔开，空字段留空。
    11/20 真机：有的标签名本身带空格（几个表情中间隔空格），单按空格拆会多出几段。
    做法：从每个空格处试着切出"标签列"，标签列要么为空，要么逗号拆开后每项都是已知标签
    （不知道标签名时只认不带空格的标签列）；前面剩下的要正好是"昵称 备注"一个空格。
    只有一种切法成立才算拆准；多种都成立就分不清，ok=False、title 为空，不猜。
    """
    known = {t for t in known_tags if t}
    found = []
    for i, ch in enumerate(text):
        if ch != ' ':
            continue
        head, field = text[:i], text[i + 1:]
        items = [t.strip() for t in TAG_SPLIT.split(field) if t.strip()] if field else []
        # 标签列成立的条件：为空；或每项都是已知标签；或不带空格（不认识的标签名也能拆，但不能靠它吞空格）
        if field and not field.strip():
            continue   # 标签列只剩空格：其实是名字里的空格落到了这里，不能当成"没有标签"
        if field and not (all(t in known for t in items) or ' ' not in field):
            continue
        parts = head.split(' ')
        if len(parts) != 2 or not parts[0].strip():
            continue
        found.append((parts[0], parts[1], items))
    if len(found) != 1:
        return {'raw': text, 'nickname': '', 'remark': '', 'tags': [], 'title': '', 'ok': False}
    nickname, remark, tags = found[0]
    return {'raw': text, 'nickname': nickname, 'remark': remark, 'tags': tags,
            'title': remark or nickname, 'ok': True}


LABEL_CLASS = 'mmui::ContactsManagerControlLabelCell'


def tag_names(window) -> list[str]:
    """左栏"标签"展开时读出全部标签名（格子文字是"标签名 + 人数"，去掉末尾人数）；收起时交回空列表。"""
    out = []
    for cell in window.ListControl(ClassName='mmui::ContactsManagerControlView').GetChildren():
        if cell.ClassName == LABEL_CLASS:
            m = re.match(r'^(.*?)\s*[\(（]?\d+[\)）]?$', cell.Name or '')
            out.append((m.group(1) if m else cell.Name or '').strip())
    return [t for t in out if t]


class _Point(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


def _cursor() -> tuple[int, int]:
    p = _Point()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def find_window():
    """输出"通讯录管理"窗口控件；没打开或被最小化时交回 None。"""
    window = auto.WindowControl(searchDepth=1, Name=WINDOW_NAME)
    if not window.Exists(1):
        return None
    hwnd = window.NativeWindowHandle
    if not user32.IsWindowVisible(wintypes.HWND(hwnd)) or user32.IsIconic(wintypes.HWND(hwnd)):
        return None
    return window


def _rows(lst) -> list[tuple[int, str]]:
    """当前可见行，按屏幕上下排序（Qt 回收列表的子控件顺序不等于屏幕顺序）。"""
    out = []
    for cell in lst.GetChildren():
        rect = cell.BoundingRectangle
        if rect.height() > 0:
            out.append((rect.top, cell.Name or ''))
    return sorted(out)


def read_all(progress=lambda text: None, stop=lambda: False, ticks: int = 3, max_pages: int = 3000) -> dict:
    """滚完整个列表，输出 {'rows': [parse_row 结果...], 'expected': 窗口显示的总人数或 None, 'gaps': 疑似跳行次数}。

    progress(text)：给界面报进度；stop()：返回 True 时停下。
    人动了鼠标、窗口关掉、列表找不到时抛 ReadStopped。结束时把鼠标放回原处。
    """
    window = find_window()
    if window is None:
        raise ReadStopped('没找到"通讯录管理"窗口：请先在微信里点 通讯录 → 通讯录管理，并保持它打开、不要最小化')
    lst = window.ListControl(ClassName=LIST_CLASS)
    if not lst.Exists(2):
        raise ReadStopped('通讯录管理里没有显示好友列表：请在左栏点"全部"后再试')
    all_cell = window.ListItemControl(ClassName=ALL_CLASS)
    expected = None
    if all_cell.Exists(1):
        digits = re.findall(r'\d+', all_cell.Name or '')
        expected = int(digits[0]) if digits else None

    # 窗口要在最上面，滚轮才落在它身上。11/20 真机：被别的窗口挡住时 SetForegroundWindow 被系统拦下，
    # 滚轮全落到上层窗口，只读到第一屏。用 activate（带前台锁绕法），还不行就停。
    from agent.tools.window import activate
    hwnd = window.NativeWindowHandle
    ok, why = activate(hwnd)
    # activate 把"同一进程的窗口在前台"也算成功（为了不关掉下拉框），微信主窗口压在上面时会误判。
    # 这里要的是通讯录管理本身在最上面（11/20 真机一次因此一开始就停）
    for _ in range(3):
        if user32.GetForegroundWindow() == hwnd:
            break
        user32.BringWindowToTop(wintypes.HWND(hwnd))
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        time.sleep(0.3)
    ok = ok and user32.GetForegroundWindow() == hwnd
    if not ok:
        raise ReadStopped(f'没能把"通讯录管理"切到最前面（{why}），请手动点一下它再读')
    home = _cursor()
    tags = tag_names(window)
    if not tags:
        _expand_tags(window, hwnd)
        tags = tag_names(window)
        if not lst.Exists(2):
            user32.SetCursorPos(*home)
            raise ReadStopped('展开"标签"后右侧不再是好友列表：请在左栏点"全部"后再试')
    rect = lst.BoundingRectangle
    x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2

    def wheel(n):
        # 每次滚之前核对鼠标还在我们放的位置：不在就是人挪走了，让给人
        if stop():
            raise ReadStopped('已停止读取')
        # 先看钩子：真人碰了键鼠就停（程序自己的滚轮、点击带"模拟"标志，不算）
        if human.ok and human.since(started):
            raise ReadStopped('检测到你在用鼠标或键盘，已停下让给你。没读完，可以稍后再点"从微信读取"')
        now = _cursor()
        # 钩子之外的兜底：位置差 40 像素以上才算人挪走了。11/20–21 真机没人用电脑时也差过 1～9 像素
        if moved[0] is not None and max(abs(now[0] - moved[0][0]), abs(now[1] - moved[0][1])) > 40:
            # 带上坐标：11/20 有一次 0.7 秒就停在这里，没法判断是人动了还是检查本身有问题
            raise ReadStopped(f'检测到鼠标被移动（程序放在 {moved[0]}，现在在 {now}），已停下让给你。'
                              '没读完，可以稍后再点"从微信读取"')
        # 滚轮落在鼠标下面的窗口：那里不是通讯录管理（被挡住了）就别滚，免得滚到别的程序
        under = user32.GetAncestor(user32.WindowFromPoint(_Point(x, y)), 2)   # GA_ROOT
        if under != hwnd:
            raise ReadStopped('"通讯录管理"被别的窗口挡住了，已停下。请让它露在最上面再读')
        user32.SetCursorPos(x, y)
        moved[0] = (x, y)
        time.sleep(0.05)
        user32.mouse_event(0x0800, 0, 0, -120 * n, 0)

    moved = [None]
    from agent.tools.human_input import HumanInput
    human = HumanInput().start()
    started = time.monotonic()
    try:
        order, gaps, capped = scan(lambda: _rows(lst), wheel, lambda n: progress(
            f'已读 {n} 人' + (f' / 共 {expected} 人' if expected else '')), ticks, max_pages, expected=expected)
    finally:
        human.stop()
        # 只有鼠标还在我们手里时才放回去，人用过了就别再动它
        if moved[0] is not None and not (human.ok and human.since(started)):
            user32.SetCursorPos(*home)
    return {'rows': [parse_row(name, tags) for name in order], 'tags': tags,
            'expected': expected, 'gaps': gaps, 'capped': capped}


def _expand_tags(window, hwnd) -> None:
    """点一下左栏"标签"把标签名展开（不动鼠标的 Invoke/Select 都展不开，11/20 实测）。
    只点这一格：点之前核对点位上的控件就是这个格子、顶层窗口就是通讯录管理。"""
    cell = window.ListControl(ClassName='mmui::ContactsManagerControlView').ListItemControl(
        ClassName='mmui::ContactsManagerControlFolderCell', Name='标签')
    if not cell.Exists(1):
        return
    r = cell.BoundingRectangle
    x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    hit = auto.ControlFromPoint(x, y)
    under = user32.GetAncestor(user32.WindowFromPoint(_Point(x, y)), 2)
    if under != hwnd or 'FolderCell' not in (hit.ClassName or ''):
        return   # 点位不对就不点，标签按不认识处理（带空格的标签行会算拆不准）
    user32.SetCursorPos(x, y)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(1.0)


def settle(read, before, timeout: float = 2.0, step: float = 0.1, sleep=time.sleep):
    """滚完以后反复读可见行，直到和滚之前不一样或超时，交回最后读到的。

    11/20 真机：固定等 0.25 秒常常界面还没刷新，连续两屏"没新行"就被当成到底，589 人只读到 424。
    读的是 (行顶坐标, 文字)：只滚了半行时文字没变、坐标变了，也算刷新了。"""
    waited = 0.0
    rows = read()
    while rows == before and waited < timeout:
        sleep(step)
        waited += step
        rows = read()
    return rows


def scan(read, wheel, progress=lambda n: None, ticks: int = 3, max_pages: int = 3000, sleep=time.sleep,
         expected=None, stuck_limit: int = 6):
    """先滚到顶，再一屏一屏往下读，交回 (按出现顺序去重的行文字, 疑似跳行次数, 是否撞到滚动次数上限)。

    read() 交回 [(行顶, 文字)...]；wheel(n) 正数往下滚。
    到顶 / 到底：连续 stuck_limit 次滚完画面都不变才算；读够 expected 人也停。
    11/20 真机：原来上限 400 次，而微信每次滚轮只走约 1 行，400 次正好停在 424 人，被当成"读完"。
    现在上限放到 3000，撞到上限要报出来，不能悄悄当成读完。"""
    def move(n):
        """滚一次，画面变了交回新画面；连续 stuck_limit 次不变交回 None（到头了）。"""
        before = read()
        for _ in range(stuck_limit):
            wheel(n)
            after = settle(read, before, sleep=sleep)
            if after != before:
                return after
        return None

    for _ in range(max_pages):
        if move(-10) is None:
            break
    seen, order, gaps, capped = set(), [], 0, True
    rows = read()
    for _ in range(max_pages):
        names = [name for _, name in rows]
        for name in names:
            if name not in seen:
                seen.add(name)
                order.append(name)
        progress(len(order))
        if expected and len(order) >= expected:
            capped = False
            break
        after = move(ticks)
        if after is None:
            capped = False
            break
        if names and not set(names) & {name for _, name in after}:
            gaps += 1   # 和上一屏一行都不重叠，可能跳过了几行
        rows = after
    return order, gaps, capped


if __name__ == '__main__':
    # 真机检查用：只打印数量，不打印名字
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    result = read_all(progress=lambda text: print(text, end='\r', flush=True))
    rows = result['rows']
    ok = [row for row in rows if row['ok']]
    titles = [row['title'] for row in ok]
    print()
    print(f"窗口显示 {result['expected']} 人，读到 {len(rows)} 行，拆准 {len(ok)}，拆不准 {len(rows) - len(ok)}，"
          f"标签 {len(result['tags'])} 个，显示名重复 {len(titles) - len(set(titles))}，疑似跳行 {result['gaps']} 次，撞到滚动上限 {result['capped']}，"
          f"标签种类 {len({t for row in ok for t in row['tags']})}")
    # 每个标签：拆准的行里有几人、整行文字里出现几次（含拆不准的）。只打印编号
    for i, tag in enumerate(result['tags']):
        print(f"标签#{i + 1}（{len(tag)} 字{'，含空格' if ' ' in tag else ''}）：拆准行里 "
              f"{sum(tag in row['tags'] for row in ok)} 人，文字里出现 {sum(tag in row['raw'] for row in rows)} 行")
    bad = [row['raw'] for row in rows if not row['ok']]
    print('拆不准的行：空格数分布', sorted({r.count(' ') for r in bad}),
          '其中含已知标签', sum(any(t in r for t in result['tags']) for r in bad))
