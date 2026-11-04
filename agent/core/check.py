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
CHAT_BOTTOM = 710    # 聊天区的下沿，再往下是输入框


def chat_title(items: list[dict]) -> str:
    """取出右侧聊天区顶部的会话标题。

    输入：ocr() 的结果，[{'text': ..., 'box': [x1, y1, x2, y2], 'score': ...}, ...]
    输出：标题文字；一条都没找到时返回空字符串。
    提示：标题的字满足三条——x1 在会话列表右边，y1 在标题栏下面，y2 在 TITLE_BOTTOM 上面。
         标题偶尔会被 OCR 拆成几段，把符合条件的都拼起来。
    """
    # TODO
    title = ''
    parts = []
    for it in items:
        if it['box'][0] > LIST_RIGHT and it['box'][1] > TITLE_TOP and it['box'][3] < TITLE_BOTTOM:
            parts.append(it)        
    new_items = sorted(parts,key=lambda it: it['box'][0])
    for it in new_items:
        title = title + it['text']
    return title


def count_msg(items: list[dict], msg: str) -> int:
    """数聊天区里有几条文字和 msg 一样。

    输入：ocr() 的结果；msg 是要找的消息。
    输出：条数，一条都没有就是 0。
    提示：“在聊天区里”和“和 msg 一样”的判断，verify_sent 里你已经写过了，搬过来，从“找到就返回”改成“找到就加 1”。
    """
    # TODO
    count = 0
    msg2 = []
    for it in items:
        if it['box'][1] > CHAT_TOP and it['box'][3] < CHAT_BOTTOM and it['box'][0] > LIST_RIGHT:
            msg2.append(it)
    for it in msg2:
        if norm(it['text']) == norm(msg):
            count += 1
    return count

def verify_sent(items: list[dict], task: dict, count_before: int) -> tuple[bool, str]:
    """核对这条消息有没有发对、发出去。

    输入：items 是发送之后截图的 OCR 结果；task 里有 'to'（发给谁）和 'msg'（发什么）；
         count_before 是发之前聊天区里数到几条这句话。
    输出：(通过没有, 原因)。原因用一句话说是哪一条没过，比如 '标题不对'。
         原因里不要写出识别到的名字和消息内容，日志里不留聊天内容。
    要查两条：
      1. 会话标题就是 task['to']。用相等，不用“包含”：要发给“小王”，标题是“小王2”也不能算对。
      2. 聊天区里和 task['msg'] 一样的条数，比 count_before 多。
         只查“有没有”不够：以前发过同一句话，这次没发成功，聊天区里照样有。
    提示：比较文字前都过一遍 norm()，OCR 常把空格和标点认错。第 2 条可以直接用 count_msg。
    """
    # TODO
    if norm(task['to']) == norm(chat_title(items)):
        if count_msg(items, task['msg']) > count_before:
            return (True,'检查全部通过')
        return (False,'聊天区没有多出这条消息')
    return (False,'标题不对')

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


def _grab(hwnd: int) -> list[dict]:
    """截一张窗口画面并识别文字。"""
    shot = capture_window(hwnd)
    if shot is None:
        sys.exit('截图失败（窗口最小化或藏起来了？）')
    return ocr(shot.image)


def run_live(to: str, msg: str) -> None:
    """对着真的微信窗口：先数一遍，等你手动发，再核对。程序只截图、识别，不动键鼠，不打印识别到的文字。"""
    wins = [w for w in find_windows(title='微信', class_prefix='Qt') if w.visible and not w.minimized]
    wins = [w for w in wins if window_rect(w.hwnd)[2] - window_rect(w.hwnd)[0] > 300]  # 跳过托盘菜单等小窗口
    if not wins:
        sys.exit('没找到能截图的微信主窗口（藏在托盘或最小化了？先手动打开它）')
    hwnd = wins[0].hwnd
    l, t, r, b = window_rect(hwnd)
    print(f'微信窗口 {r - l}x{b - t}（上面的位置常量是按实验时的大小量的，差太多会判错）')

    before = count_msg(_grab(hwnd), msg)
    print(f'发之前：聊天区里有 {before} 条')
    input('现在在微信里手动发这条消息，发完按回车（想测“没发成功”就直接按回车）')
    ok, reason = verify_sent(_grab(hwnd), {'to': to, 'msg': msg}, before)
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
