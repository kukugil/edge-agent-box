"""微信未读：导航栏总数字 + 会话列表里带角标的行。

D9 门铃和会话队列用。只读后台截图，不动键鼠；OCR 只读角标那几行的标题条，不读消息预览。

坐标线按 960×800 的窗口量（11/6，和 check.py 一致）：
    x:   0 ── 70 导航栏 ──┬── 头像 ~62–108，角标在头像右上角 x≈101–116 ──┬── 标题 116–200 ──┬── 时间 200–241
    y:   会话行高约 65px；角标上沿 ≈ 行上沿，标题在角标往下 0–25px，预览在再往下一行
"""
from PIL import Image

from .ocr import norm, ocr
from .screen import Shot, find_red_dots

NAV_W = 70             # 导航栏宽度：x 比它小的红点是导航栏上的
NAV_CHAT_BOTTOM = 150  # 导航栏「聊天」图标下沿：再往下的红点是别的图标（比如朋友圈），不算消息
BADGE_X = (90, 125)    # 会话行角标中心的 x 范围：排除头像里零星的红色，也排除导航栏
TITLE_X = (116, 200)   # 标题条的左右：左边避开角标（角标右沿到 116），右边 200 以后是时间
TITLE_DY = (-4, 24)    # 标题条相对角标上沿的上下：往下再多就读到预览（消息内容）了
DOT_ONLY = 12          # 角标宽高都小于它的是不带数字的小红点（免打扰会话常见）
ROW_TOP, ROW_H = 87, 65  # 第一行角标上沿、行高：按行找会话时，每行假装有个角标，好复用 read_titles


def total_badge(shot: Shot) -> bytes | None:
    """导航栏「聊天」图标上总未读数那一块的图片数据，没有红点时交回 None。

    门铃只比“和上次一不一样”，不认数字：白字红底和“99+”OCR 不稳。
    """
    dots = [d for d in find_red_dots(shot.image, NAV_W) if d[1] < NAV_CHAT_BOTTOM]
    if not dots:
        return None
    return shot.image.crop(dots[0]).tobytes()


def bell_state(shot: Shot) -> tuple:
    """门铃要比的“样子”：(总数字那块的像素, 会话列表里每个角标的框)。

    为什么不只看总数字：11/8 验收 20 条漏响 4 条，原因没完全查清。多盯一样角标位置作兜底：
    哪一行新出了角标、角标挪了位置，都算变了。只找红点不 OCR，约 0.02 秒，门铃照样便宜。
    """
    return total_badge(shot), tuple(badge_rows(shot.image))


def badge_rows(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """会话列表里的角标框，从上到下，截图内坐标。只认头像右上角那一列，不读字。"""
    out = []
    for x1, y1, x2, y2 in find_red_dots(image, BADGE_X[1]):
        if BADGE_X[0] <= (x1 + x2) / 2 <= BADGE_X[1]:
            out.append((x1, y1, x2, y2))
    return out


GAP = 20   # 拼图时条与条之间留的白边：太小 OCR 会把上下两条认成一段


def read_titles(image: Image.Image, badges: list[tuple[int, int, int, int]]) -> list[str]:
    """读每个角标那一行的会话标题：只把标题条裁出来 OCR，不碰预览和聊天区。

    为什么裁小条：整列 OCR 会把消息预览也读出来，安全规则只许读标题。
    为什么拼成一张再 OCR：每调一次 OCR 有约 0.6 秒的固定开销，5 行分开读要 3 秒多，拼起来只调一次。
    标题被 OCR 切成几段时，按 x 从左到右拼起来。
    """
    if not badges:
        return []
    h = TITLE_DY[1] - TITLE_DY[0]
    w = TITLE_X[1] - TITLE_X[0]
    sheet = Image.new('RGB', (w, len(badges) * (h + GAP)), 'white')
    for i, b in enumerate(badges):
        top = b[1] + TITLE_DY[0]
        sheet.paste(image.convert('RGB').crop((TITLE_X[0], top, TITLE_X[1], top + h)), (0, i * (h + GAP)))
    parts = [[] for _ in badges]
    for it in ocr(sheet):
        i = int((it['box'][1] + it['box'][3]) / 2 // (h + GAP))   # 按文字中心落在第几条
        if 0 <= i < len(badges):
            parts[i].append(it)
    out = []
    for items in parts:
        if not items:
            out.append('')
            continue
        # 条里偶尔会带进预览的上半截：只要和最上面那段同一行的
        first = min(it['box'][1] for it in items)
        same = sorted((it for it in items if it['box'][1] - first < 8), key=lambda it: it['box'][0])
        out.append(''.join(it['text'] for it in same))
    return out


def find_unread_rows(shot: Shot) -> list[dict]:
    """找会话列表里带未读角标的行。

    输入：capture_window 交回的 Shot（窗口 960×800 左右）
    输出：[{'title': 标题, 'box': 角标框 (x1,y1,x2,y2), 'dot_only': 是不是不带数字的小红点}]，从上到下，截图内坐标
          标题读不出来时是空字符串，调用方按“不在白名单”处理就安全
    注意：只看得到列表可见区里的行。微信把有新消息的会话顶到上面，所以一般够用；未读太多时下面的会漏，D11 再补。
    """
    badges = badge_rows(shot.image)
    rows = []
    for b, title in zip(badges, read_titles(shot.image, badges)):
        small = b[2] - b[0] < DOT_ONLY and b[3] - b[1] < DOT_ONLY
        rows.append({'title': title, 'box': b, 'dot_only': small})
    return rows


def find_row(image: Image.Image, title: str) -> tuple[int, int] | None:
    """在会话列表的可见行里按标题找一行，交回该点的位置（截图内坐标），找不到交回 None。

    输入：窗口截图（960×800 左右）、要找的会话标题
    输出：(x, y) 这一行标题处的点，拿去 shot.to_screen 再点；不管这一行有没有角标
    为什么不用角标定位：角标一点就没，回失败重试、被 agent 自己点掉的会话都没有角标了。
    只读每行的标题条，不读预览。名字太长被截成“…”、或在可见区外时找不到，调用方当失败处理。
    """
    rows = [(BADGE_X[0] + 11, y, TITLE_X[0], y + 15) for y in range(ROW_TOP, image.height - ROW_H // 2, ROW_H)]
    for row, text in zip(rows, read_titles(image, rows)):
        if norm(text) == norm(title):
            return (TITLE_X[0] + 44, row[1] + 25)
    return None
