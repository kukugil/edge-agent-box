"""读网页的控件树（UIA），交回和 ocr() 一样格式的文字列表。

为什么要有它：微信是自绘界面，只能截图识字；网页（QQ 邮箱）能直接读出每个按钮、每行文字和它的位置，
又快又准，不用 OCR 也不用 UI-TARS。两条路交回同一种格式，主循环里「看」按应用选一条就行（感知分流）。

前提：Chrome 要带 --force-renderer-accessibility 启动，不然网页内容读不到；
      再加 --disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows，
      窗口被挡住时也能读。
坐标：交回的 box 是“截图内坐标”（以 window_rect 的左上角为原点），和 capture_window + ocr 的结果对得上，
      所以 snap_to_text、find_text、chat_title 这类按坐标筛的函数都能直接用。
"""
import uiautomation as auto

from .window import window_rect

# 这些控件的名字就是界面上的字；Group/Pane 的名字多半和里面的 Text 重复，跳过免得一句话出现两次
_KINDS = {'TextControl', 'ButtonControl', 'HyperlinkControl', 'EditControl', 'ListItemControl',
          'MenuItemControl', 'CheckBoxControl', 'ComboBoxControl', 'TabItemControl', 'DocumentControl'}


def _value(c) -> str:
    """输入框里已经填的文字（Name 是输入框的标签，内容在 ValuePattern 里）。读不到返回空字符串。"""
    try:
        return c.GetValuePattern().Value or ''
    except Exception:
        return ''


def read_page(hwnd: int, max_depth: int = 60) -> list[dict]:
    """读 Chrome 窗口里网页部分的控件树。

    输入：hwnd Chrome 主窗口句柄
    输出：[{'text', 'box': [x1, y1, x2, y2], 'score': 1.0, 'kind': 控件种类, 'value': 输入框内容}, ...]
         只含网页内容（地址栏、标签页这些浏览器自己的控件不要），看不见的（大小为 0）去掉。
         读不到网页时返回空列表——多半是 Chrome 没带无障碍参数启动。
    注意：名字为空的输入框也会留下（text 为空），写信页的正文框常常没有名字，要靠 kind 和位置找。
    """
    root = auto.ControlFromHandle(hwnd)
    doc = root.DocumentControl(searchDepth=12)
    if not doc.Exists(0.5):
        return []
    l, t, _, _ = window_rect(hwnd)
    items = []
    for c, _depth in auto.WalkControl(doc, includeTop=False, maxDepth=max_depth):
        kind = c.ControlTypeName
        # Group 一般没名字（跳过），有名字的留下：QQ 邮箱的正文框就是一个名叫「输入正文」的 Group
        if kind not in _KINDS and not (kind == 'GroupControl' and (c.Name or '').strip()):
            continue
        r = c.BoundingRectangle
        if r.right <= r.left or r.bottom <= r.top:
            continue
        name = (c.Name or '').strip()
        value = _value(c) if kind in ('EditControl', 'DocumentControl', 'ComboBoxControl') else ''
        if not name and kind not in ('EditControl', 'DocumentControl'):
            continue
        items.append({'text': name, 'box': [r.left - l, r.top - t, r.right - l, r.bottom - t],
                      'score': 1.0, 'kind': kind, 'value': value})
    return items


# ---------- 邮件版主循环用的工具（11/5） ----------

import re
import time

from .ocr import center, norm
from .window import find_windows

NAV_RIGHT = 220   # QQ 邮箱左边文件夹栏的右边界（截图内 x）；和 agent/core/mail_check.py 里的同名常量一致


def find_mail_window() -> int | None:
    """找开着 QQ 邮箱的 Chrome 窗口，交回 hwnd；没找到交回 None。

    坑：来新邮件时标题会变成“收到 N 封新邮件 - Google Chrome”，所以不按标题里的“QQ邮箱”找，
        按页面里有没有「写信」这个按钮找。
    """
    for w in find_windows(class_prefix='Chrome_WidgetWin'):
        if w.visible and not w.minimized and w.title.endswith('Google Chrome') \
                and any(x['text'] == '写信' for x in read_page(w.hwnd)):
            return w.hwnd
    return None


def wait_page(hwnd: int, pred, timeout: float = 12.0):
    """反复读页面，直到 pred(items) 交回真值（网页切换要几秒才画完）。

    输出：(pred 交回的东西, 最后一次读到的 items)。超时时第一个是 None。
    """
    end = time.time() + timeout
    while True:
        items = read_page(hwnd)
        got = pred(items)
        if got or time.time() >= end:
            return (got or None), items
        time.sleep(0.5)


def _quoted(target: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r'[“"‘\'「]([^”"’\'」]+)[”"’\'」]', target)]


def locate_text(items: list[dict], target: str) -> tuple[list, str]:
    """网页版的「定位」：不用 UI-TARS，直接按 target 里引号括起来的文字在控件树里找。

    输入：items  read_page() 的结果；target 模型写的“要点的元素”，例如 '列表里标题为“测试邮件 3”的邮件'
    输出：([x, y] 截图内坐标, 说明)；找不到交回 ([], 原因)。
    规则：
      1. 先找文字和引号里完全相等的（norm 后比）；没有再找包含的。“测试邮件 1”不会匹配到“测试邮件 10”。
      2. 同样的字出现好几处时，优先页面主体（x > NAV_RIGHT），其次靠上的那个。
         左边文件夹栏里也有「收件箱」「已发送」，target 里写了“左侧”才去那边找。
    """
    # 要点正文时一律点正文框第一行，不按引号里的字找。
    # 坑（11/6 验收）：模型常把引用原邮件里的字括进引号，按字找会点进引用区，打的字进不了正文
    body = _body_point(items) if '正文' in target else None
    if body:
        return body, '按「输入正文」找到正文框'
    qs = _quoted(target)
    if not qs:
        return [], 'target 里没有用引号括起要点的文字'
    left = '左' in target
    for q in qs:
        for exact in (True, False):
            c = [it for it in items if it['text']
                 and (norm(it['text']) == norm(q) if exact else norm(q) in norm(it['text']))]
            if not c:
                continue
            side = [it for it in c if (it['box'][0] < NAV_RIGHT) == left] or c
            best = min(side, key=lambda it: it['box'][1])
            x, y = center(best['box'])
            return [int(x), int(y)], f'按文字找到（{"相等" if exact else "包含"}，{len(c)} 处）'
    return [], '页面上没有引号里的那段文字'


def _body_point(items: list[dict]):
    """正文框第一行的位置。

    坑（11/5 实测）：回复页的正文框是一个名叫「输入正文」的 Group，下半截是引用的原邮件。
        点框的正中间会点进原邮件里，所以点左上角往里一点（第一行）。
    """
    g = [it for it in items if it['text'] == '输入正文']
    if not g:
        return None
    x1, y1, _, _ = g[0]['box']
    return [int(x1) + 60, int(y1) + 20]


def open_folder(hwnd: int, name: str = '已发送', timeout: float = 15.0) -> list[dict]:
    """点左边文件夹栏里的 name（如「已发送」），等右边列表换好，交回那一页的 items。

    “换好”的标志：右边主体顶部出现同名的标题（x > NAV_RIGHT 且 y < 200）。
    不会点别的东西；找不到文件夹交回空列表。
    """
    from .inputs import click
    from .window import activate, window_rect
    items = read_page(hwnd)
    nav = [it for it in items if it['text'] == name and it['box'][0] < NAV_RIGHT]
    if not nav:
        return []
    activate(hwnd)
    l, t, _, _ = window_rect(hwnd)
    x, y = center(nav[0]['box'])
    click(l + int(x), t + int(y))
    got, items = wait_page(hwnd, lambda it: [x for x in it if x['text'] == name
                                             and x['box'][0] > NAV_RIGHT and x['box'][1] < 200], timeout)
    return items if got else []


def body_text(items: list[dict]) -> str:
    """回复页正文框里「自己写的那部分」的文字，引用的原邮件不算。

    坑（11/6 验收）：收件箱里会混进以前的回复（发给自己的），模型有时打开的是“回复：测试邮件 k”，
        再点回复，引用区里就带着上次写的“测试回复 k”。只要在整页找这句话，规矩就会被骗过，
        结果一个字没打，空着正文发了出去。所以只看正文框里、「原始邮件」那一行上面的字。
    输出：拼起来的文字；不在回复页（没有正文框）时交回空字符串。
    """
    g = [it for it in items if it['text'] == '输入正文']
    if not g:
        return ''
    x1, y1, x2, y2 = g[0]['box']
    quote = [it['box'][1] for it in items if it['text'] == '原始邮件' and y1 <= it['box'][1] <= y2]
    bottom = min(quote) if quote else y2
    rows = [it for it in items if it['kind'] == 'TextControl' and it['text'] != '输入正文'
            and x1 <= it['box'][0] <= x2 and y1 <= it['box'][1] < bottom]
    return ''.join(it['text'] for it in sorted(rows, key=lambda it: (it['box'][1], it['box'][0])))
