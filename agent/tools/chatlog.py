"""读聊天记录的底层：微信 4.x 的控件树 → 一条条消息（D15）。

只读控件属性和截图像素，不点、不按键、不滚动。不核对会话标题——那是调用方
（agent/core/messages.read_chat）的事，这里的函数单独用时读到的是"当前开着的那个会话"。

前提（11/11 实测，微信 4.1.15.56）：系统"屏幕阅读器"标志打开、微信重启过，控件树才有消息列表。
没打开时 message_list 返回 None，调用方改用 OCR。

消息格式和 agent/core/reply.py 约定的一样：
  {'who': 'them'|'us'|'none', 'kind': 'text'|'sticker'|'image'|'voice'|'file'|'time', 'text': str, 'at': datetime|None}

运行（在项目根目录）：
  python -m agent.tools.chatlog      离线测时间线解析、类型、左右合并
"""
import re
import sys
from datetime import datetime, timedelta

THEM, US, NONE = 'them', 'us', 'none'

# 11/11 真机见过的条目类名 → 类型。没见过的类一律当 'file'：reply.route 会把它转人工，
# 宁可多转人工，也不要把链接、名片、红包、转账当成表情包或空文字去回
KINDS = {
    'mmui::ChatTextItemView': 'text',
    'mmui::ChatImageItemView': 'image',
    'mmui::ChatVoiceItemView': 'voice',
    'mmui::ChatItemView': 'time',          # 时间线；系统提示（撤回等）也是这个类，同样不算任何一方
}
STICKER_NAMES = ('动画表情', '[动画表情]')

_cache: dict[int, object] = {}           # hwnd → 消息列表控件。找一次要走整棵树，切会话时它还是同一个（11/11 实测）


def message_list(hwnd: int):
    """输入窗口句柄，输出消息列表控件；控件树里没有时返回 None。

    先认 AutomationId='chat_message_list'（11/11 实测的名字），找不到再退一步：id 带 message 的 List。
    """
    import uiautomation as auto
    lst = _cache.get(hwnd)
    if lst is not None:
        try:
            r = lst.BoundingRectangle
            if r.width() > 0 and r.height() > 0:
                return lst
        except Exception:                # 控件被销毁（微信重启等）会抛，重新找
            pass
        _cache.pop(hwnd, None)
    found = None
    stack = [(0, auto.ControlFromHandle(hwnd))]
    seen = 0
    while stack and seen < 5000:         # 和探针一样设上限，树特别大时不一直走
        depth, c = stack.pop()
        seen += 1
        try:
            if c.ControlTypeName == 'ListControl':
                aid = (c.AutomationId or '').lower()
                if aid == 'chat_message_list':
                    found = c
                    break
                if 'message' in aid and found is None:
                    found = c
            if depth < 40:
                stack.extend((depth + 1, k) for k in reversed(c.GetChildren()))
        except Exception:
            continue
    if found is not None:
        _cache[hwnd] = found
    return found


def list_items(lst, origin: tuple[int, int]) -> tuple[list[dict], tuple[int, int, int, int]]:
    """输入消息列表控件和窗口左上角屏幕坐标，输出 (条目列表, 列表框)，坐标都是窗口内的。

    条目：{'cls': 类名, 'text': Name, 'box': (x1, y1, x2, y2)}，从上到下。
    """
    ox, oy = origin
    r = lst.BoundingRectangle
    frame = (r.left - ox, r.top - oy, r.right - ox, r.bottom - oy)
    out = []
    for c in lst.GetChildren():
        b = c.BoundingRectangle
        out.append({'cls': c.ClassName, 'text': c.Name or '',
                    'box': (b.left - ox, b.top - oy, b.right - ox, b.bottom - oy)})
    return out, frame


def kind_of(cls: str, text: str) -> str:
    """输入条目类名和 Name，输出消息类型。

    坑：表情包的类名是 ChatBubbleReferItemView，看名字像"引用消息"，11/11 只见过 Name="动画表情" 这一种；
    所以按 Name 认表情包，同一个类别的 Name 当文字（可能是引用回复）。
    """
    if text in STICKER_NAMES:
        return 'sticker'
    if cls == 'mmui::ChatBubbleReferItemView':
        return 'text' if text else 'file'
    return KINDS.get(cls, 'file')


def avatar_side(gray, box: tuple[int, int, int, int]) -> str:
    """输入窗口灰度图（numpy 二维数组）和条目框，输出 THEM / US / NONE / '?'。

    为什么看头像：条目框横跨整个聊天区，按框分不出左右；表情包、图片又没有字，OCR 也帮不上。
    单聊里每条消息都带头像：对方的在行左边 20–56px，我方的在行右边 20–56px（11/11 真机量，960 宽）。
    只看行的上 48px：头像贴着行顶，看太多会把下一行的头像算进来（时间线行只有 41px 高）。
    """
    x1, y1, x2, y2 = box
    top = gray[max(0, y1):max(0, y1) + min(48, y2 - y1)]
    if top.size == 0:
        return '?'
    prof = top.std(axis=0)

    def busy(a, b):
        seg = prof[max(0, a):max(0, b)]
        return float((seg > 15).mean()) if seg.size else 0.0
    left, right = busy(x1 + 20, x1 + 56) > 0.6, busy(x2 - 56, x2 - 20) > 0.6
    return {(True, False): THEM, (False, True): US, (False, False): NONE}.get((left, right), '?')


_WEEK = '一二三四五六日'
_TIME = re.compile(
    r'^(?:(?P<y>\d{4})[年/-])?(?:(?P<mo>\d{1,2})[月/-](?P<d>\d{1,2})日?)?\s*'
    r'(?P<rel>昨天|前天|(?:星期|周)[一二三四五六日天])?\s*'
    r'(?P<ap>凌晨|早上|上午|中午|下午|晚上)?\s*(?P<h>\d{1,2}):(?P<mi>\d{2})$')


def parse_time(label: str, now: datetime) -> datetime | None:
    """输入时间线上的字（如 "14:32" "昨天 09:05" "星期二 下午3:10" "11月10日 14:32"）和当前时间，
    输出它表示的时刻；认不出（系统提示等）返回 None。

    坑：微信时间线只写到分钟，没写年份的日期要按"不会在将来"推回去年。真机上具体是哪几种写法还没核对全。
    """
    m = _TIME.match(label.strip())
    if not m:
        return None
    h, mi = int(m['h']), int(m['mi'])
    if m['ap'] in ('下午', '晚上') and h < 12:
        h += 12
    if m['ap'] == '中午' and h < 11:
        h += 12
    if h > 23 or mi > 59:
        return None
    day = now.date()
    if m['mo']:
        y = int(m['y']) if m['y'] else now.year
        try:
            day = day.replace(year=y, month=int(m['mo']), day=int(m['d']))
        except ValueError:
            return None
        if not m['y'] and day > now.date():
            day = day.replace(year=y - 1)
    elif m['rel'] == '昨天':
        day -= timedelta(days=1)
    elif m['rel'] == '前天':
        day -= timedelta(days=2)
    elif m['rel']:
        want = _WEEK.index(m['rel'][-1].replace('天', '日'))
        back = (day.weekday() - want) % 7 or 7           # 写星期几的都是今天以前、一周以内的
        day -= timedelta(days=back)
    return datetime(day.year, day.month, day.day, h, mi)


def to_msgs(items: list[dict], sides: list[str], now: datetime) -> list[dict]:
    """输入条目、每条的左右（和 items 一一对应）和当前时间，输出约定格式的消息列表。

    at 取它上面最近一条时间线的时刻；一屏顶上还没出现时间线的消息 at=None。
    """
    out, at = [], None
    for it, side in zip(items, sides):
        kind = kind_of(it['cls'], it['text'])
        if kind == 'time':
            at = parse_time(it['text'], now) or at
            out.append({'who': NONE, 'kind': 'time', 'text': it['text'], 'at': at})
            continue
        # box 给发后核对找失败标记用；reply 不看它
        out.append({'who': side, 'kind': kind, 'text': it['text'], 'at': at, 'box': it['box']})
    return out


def same_frames(a: list[dict], b: list[dict]) -> bool:
    """输入前后两帧的消息列表，输出两帧是否一模一样（谁说的、类型、文字都一样）。

    为什么要两帧：11/11 补测里新消息刚出来的第一帧，头像还没画上，左右判成"无"；滚动时截图和控件框也会错开。
    两帧一致才认，不一致就等下一次读。
    """
    key = lambda ms: [(m['who'], m['kind'], m['text']) for m in ms]
    return key(a) == key(b)


# ---------- 离线测试 ----------

def run_offline() -> None:
    now = datetime(2026, 11, 12, 10, 0)            # 星期四
    cases = [
        ('14:32', datetime(2026, 11, 12, 14, 32)),
        ('9:05', datetime(2026, 11, 12, 9, 5)),
        ('昨天 09:05', datetime(2026, 11, 11, 9, 5)),
        ('前天 23:59', datetime(2026, 11, 10, 23, 59)),
        ('星期二 14:00', datetime(2026, 11, 10, 14, 0)),
        ('周四 08:00', datetime(2026, 11, 5, 8, 0)),
        ('星期二 下午3:10', datetime(2026, 11, 10, 15, 10)),
        ('晚上 8:00', datetime(2026, 11, 12, 20, 0)),
        ('11月10日 14:32', datetime(2026, 11, 10, 14, 32)),
        ('12月30日 14:32', datetime(2025, 12, 30, 14, 32)),
        ('2025年11月10日 14:32', datetime(2025, 11, 10, 14, 32)),
        ('2025/11/10 14:32', datetime(2025, 11, 10, 14, 32)),
        ('你撤回了一条消息', None),
        ('25:00', None),
    ]
    ok = 0
    for label, want in cases:
        got = parse_time(label, now)
        ok += got == want
        if got != want:
            print(f'  错  {label!r}：得到 {got}，应为 {want}')
    total = len(cases)

    checks = [
        (kind_of('mmui::ChatTextItemView', '你好'), 'text'),
        (kind_of('mmui::ChatBubbleReferItemView', '动画表情'), 'sticker'),
        (kind_of('mmui::ChatBubbleReferItemView', ''), 'file'),
        (kind_of('mmui::ChatVoiceItemView', '语音3"秒未播放'), 'voice'),
        (kind_of('mmui::ChatSomethingNewView', '名片'), 'file'),
    ]
    items = [{'cls': 'mmui::ChatTextItemView', 'text': '顶上的旧话', 'box': (0, 0, 1, 1)},
             {'cls': 'mmui::ChatItemView', 'text': '09:50', 'box': (0, 0, 1, 1)},
             {'cls': 'mmui::ChatTextItemView', 'text': '在吗', 'box': (0, 0, 1, 1)},
             {'cls': 'mmui::ChatItemView', 'text': '你撤回了一条消息', 'box': (0, 0, 1, 1)},
             {'cls': 'mmui::ChatBubbleReferItemView', 'text': '动画表情', 'box': (0, 0, 1, 1)}]
    ms = to_msgs(items, [THEM, NONE, THEM, NONE, US], now)
    checks += [
        (ms[0]['at'], None),
        (ms[2]['at'], datetime(2026, 11, 12, 9, 50)),
        (ms[4]['at'], datetime(2026, 11, 12, 9, 50)),      # 撤回提示认不出时间，沿用上一条时间线
        ((ms[4]['who'], ms[4]['kind']), (US, 'sticker')),
        (same_frames(ms, [dict(m) for m in ms]), True),
        (same_frames(ms, [*ms[:2], {**ms[2], 'who': NONE}, *ms[3:]]), False),
    ]
    for i, (got, want) in enumerate(checks, 1):
        ok += got == want
        if got != want:
            print(f'  错  第 {i} 条检查：得到 {got}，应为 {want}')
    total += len(checks)

    import numpy as np
    gray = np.full((100, 960), 245.0)
    gray[10:50, 300:340] = 40.0        # 行左边一块深色的"头像"
    sides = (avatar_side(gray, (280, 10, 960, 90)), avatar_side(np.full((100, 960), 245.0), (280, 10, 960, 90)))
    ok += sides == (THEM, NONE)
    total += 1
    print(f'{ok}/{total} 对')
    assert ok == total, '离线测试失败'


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    run_offline()
