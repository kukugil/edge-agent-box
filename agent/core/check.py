"""真的核对：发完消息后，程序自己看屏幕判断“发对人、发出去了”，不问模型。

为什么单独一个文件：判断逻辑是纯函数（OCR 结果进、对错出），可以先用假的 OCR 结果离线测，
不用每次都开微信。测对了，再接进 graph.py 的 check 节点。

你要做的：填 chat_title、count_msg、verify_sent 三个函数（删掉 raise NotImplementedError）。

运行（在项目根目录）：
  python -m agent.core.check            离线测 8 种情况，成功时最后一行打印：核对逻辑全对。
  python -m agent.core.check --live 名字 消息
                                       对着现在打开的微信窗口：先数一遍，等你手动发完按回车，再核对。
                                       程序只截图和识别，不动键鼠。
"""
import sys

from agent.tools import capture_window, find_windows, norm, ocr, window_rect

# 微信窗口里各块的位置（截图内坐标，像素）。按实验时的窗口大小量的，窗口拉大拉小会变；
# D11 挪进应用配置文件。
LIST_RIGHT = 255     # 左边会话列表的右边界：x 小于它的都是列表里的字
TITLE_TOP = 30       # 窗口标题栏的下沿
TITLE_BOTTOM = 85    # 会话标题的下沿：标题文字整个在这一条里
CHAT_TOP = 80        # 聊天区（消息气泡）的上沿
CHAT_BOTTOM = 645    # 聊天区的下沿，再往下是工具栏和输入框（11/4 按 960×800 的窗口重量，原来 710 把输入框也算进去了）


DEFAULT_LAYOUT = {'right': 960, 'bottom': CHAT_BOTTOM}
OK, FAIL, UNKNOWN = 'ok', 'fail', 'unknown'


def chat_bottom(image) -> int:
    """输入窗口图，输出聊天底边；用灰色分隔线适配高度，不读文字。"""
    import numpy as np
    pixels = np.asarray(image.convert('L'))
    expected = image.height - 145
    left, right = LIST_RIGHT + 20, image.width - 20
    for y in range(min(image.height - 2, expected + 40), max(1, expected - 200) - 1, -1):
        row = pixels[y, left:right]
        if row.size and row.std() < 2 and abs(float(row.mean()) - 236) < 4:
            if (abs(float(row.mean()) - float(pixels[y - 1, left:right].mean())) > 4
                    and abs(float(row.mean()) - float(pixels[y + 1, left:right].mean())) > 2):
                return y - 10
    return expected - 10


def layout(image) -> dict:
    """输入同一张窗口图，输出聊天区右边和底边，避免混用旧尺寸。"""
    return {'right': image.width, 'bottom': chat_bottom(image)}


def chat_title(items: list[dict]) -> str:
    """输入 OCR 字块，输出完整可信的标题；截断或低置信度时返回空串。"""
    parts = sorted((it for it in items if it['box'][0] > LIST_RIGHT
                    and it['box'][1] > TITLE_TOP and it['box'][3] < TITLE_BOTTOM),
                   key=lambda it: it['box'][0])
    title = ''.join(it['text'] for it in parts)
    if title.endswith(('…', '...')) or any(it.get('score', 0) < 0.9 for it in parts):
        return ''
    return title


def title_matches(items: list[dict], name: str) -> bool:
    """输入 OCR 字块和目标名，输出是否完整匹配；读不清时拒绝认人。"""
    title = chat_title(items)
    return bool(title) and norm(title) == norm(name)


def is_ours(item: dict, lay=DEFAULT_LAYOUT) -> bool:
    """输入字块和布局，输出中心是否在聊天区右半边，用来区分发送方。"""
    return (item['box'][0] + item['box'][2]) / 2 > (LIST_RIGHT + lay['right']) / 2


def count_msg(items: list[dict], msg: str, lay=DEFAULT_LAYOUT) -> int:
    """输入 OCR、消息和布局，输出聊天区我方完全匹配的气泡数。"""
    return sum(it['box'][0] > LIST_RIGHT and it['box'][1] > CHAT_TOP
               and it['box'][3] < lay['bottom'] and is_ours(it, lay)
               and norm(it['text']) == norm(msg) for it in items)


def judge_sent(items, task, count_before, lay=DEFAULT_LAYOUT, still_in_box=None, image=None):
    """输入发后 OCR、任务、发前计数及可选读回/图，输出三态和脱敏原因。"""
    if not title_matches(items, task['to']):
        return UNKNOWN, '标题读不清或不是目标会话'
    if count_msg(items, task['msg'], lay) > count_before:
        if image is not None:
            from agent.tools import find_red_dots
            bubbles = [it for it in items if count_msg([it], task['msg'], lay)]
            x1, y1, x2, y2 = max(bubbles, key=lambda it: it['box'][3])['box']
            region = image.crop((max(0, x1 - 45), max(0, y1 - 5), x1, min(image.height, y2 + 5)))
            if find_red_dots(region):
                return UNKNOWN, '气泡旁有发送失败标记'
        return OK, '我方聊天区多出这条消息'
    if still_in_box is True:
        return FAIL, '输入框仍完整保留待发送文字'
    return UNKNOWN, '未确认新的我方消息'


def verify_sent(items, task, count_before, lay=DEFAULT_LAYOUT, still_in_box=None, image=None):
    """输入同 judge_sent，输出旧版布尔结果和原因，保留调用兼容性。"""
    result, why = judge_sent(items, task, count_before, lay, still_in_box, image)
    return result == OK, why

# ---------- 下面已经写好 ----------

def _item(text, x1, y1, x2, y2):
    return {'text': text, 'box': [x1, y1, x2, y2], 'score': 0.99}


# 假的 OCR 结果：左边列表里也有目标名字，用来检查有没有把列表里的字当成标题
_LIST = [_item('测试好友A', 70, 100, 160, 120), _item('测试好友B', 70, 170, 160, 190)]
_TASK = {'to': '测试好友A', 'msg': '测试 1'}

_TITLE_A = _item('测试好友A', 290, 45, 380, 70)
_BUBBLE = _item('测试1', 820, 500, 870, 525)          # 聊天区里一条「测试 1」
_BUBBLE_2 = _item('测试 1', 820, 560, 870, 585)       # 再一条，位置往下

CASES = [
    # (说明, OCR 结果, 发之前数到几条, 应该通过吗)
    ('发对了', _LIST + [_TITLE_A, _BUBBLE], 0, True),
    ('发进了别的会话', _LIST + [_item('测试好友B', 290, 45, 380, 70), _BUBBLE], 0, False),
    ('标题只是包含名字', _LIST + [_item('测试好友A2', 290, 45, 390, 70), _BUBBLE], 0, False),
    ('消息还在输入框里', _LIST + [_TITLE_A, _item('测试 1', 290, 760, 350, 780)], 0, False),
    ('别的消息里带了这几个字', _LIST + [_TITLE_A, _item('测试1早上好', 780, 500, 900, 525)], 0, False),
    ('标题被拆成两段', _LIST + [_item('测试', 290, 45, 330, 70), _item('好友A', 332, 45, 380, 70), _BUBBLE], 0, True),
    ('以前发过，这次没发成功', _LIST + [_TITLE_A, _BUBBLE], 1, False),
    ('以前发过，这次又发成功了', _LIST + [_TITLE_A, _BUBBLE, _BUBBLE_2], 1, True),
]


def run_offline() -> None:
    for name, items, before, want in CASES:
        ok, reason = verify_sent(items, _TASK, before)
        mark = '对' if ok == want else '错'
        print(f'[{mark}] {name}：应该{"通过" if want else "不通过"}，结果{"通过" if ok else "不通过"}（{reason}）')
        if ok != want:
            sys.exit('有一种情况判错了，看上面那一行')
    print('核对逻辑全对')


def _grab(hwnd: int, with_layout=False):
    """输入窗口句柄和布局选项，输出 OCR 或 OCR、布局、图，供现场核对使用。"""
    shot = capture_window(hwnd)
    if shot is None:
        sys.exit('截图失败（窗口最小化或藏起来了？）')
    items = ocr(shot.image)
    return (items, layout(shot.image), shot.image) if with_layout else items


def run_live(to: str, msg: str) -> None:
    """对着真的微信窗口：先数一遍，等你手动发，再核对。程序只截图、识别，不动键鼠，不打印识别到的文字。"""
    wins = [w for w in find_windows(title='微信', class_prefix='Qt') if w.visible and not w.minimized]
    wins = [w for w in wins if window_rect(w.hwnd)[2] - window_rect(w.hwnd)[0] > 300]  # 跳过托盘菜单等小窗口
    if not wins:
        sys.exit('没找到能截图的微信主窗口（藏在托盘或最小化了？先手动打开它）')
    hwnd = wins[0].hwnd
    l, t, r, b = window_rect(hwnd)
    print(f'微信窗口 {r - l}x{b - t}（上面的位置常量是按实验时的大小量的，差太多会判错）')

    items, lay, image = _grab(hwnd, with_layout=True)
    before = count_msg(items, msg, lay)
    print(f'发之前：聊天区里有 {before} 条')
    input('现在在微信里手动发这条消息，发完按回车（想测“没发成功”就直接按回车）')
    items, lay, image = _grab(hwnd, with_layout=True)
    ok, reason = verify_sent(items, {'to': to, 'msg': msg}, before, lay, image=image)
    print('通过' if ok else f'不通过：{reason}')

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    if len(sys.argv) == 4 and sys.argv[1] == '--live':
        run_live(sys.argv[2], sys.argv[3])
    else:
        run_offline()


if __name__ == '__main__':
    main()
