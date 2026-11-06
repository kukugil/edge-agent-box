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
        if kind not in _KINDS:
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
