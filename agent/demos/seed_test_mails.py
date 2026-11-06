"""准备验收用的测试邮件：测试号给自己发 N 封，标题「测试邮件 1」…「测试邮件 N」。

不是 agent：步骤写死，坐标从网页控件树里按文字找，不问模型。顺便验证三件事：
写信页能填、发送前能读出收件人核对、发完能读出“发送成功”。这三件事邮件版主循环都要用。

安全：收件人只从 agent/test_contacts.local.json 的 allowed_mail 读，而且只用第一个（测试号自己）。
      点「发送」前重新读一遍收件人那一行，不是这个地址、或者多出别的地址，就不发、直接退出。

用法（在项目根目录，QQ 邮箱开在带无障碍参数的 Chrome 里）：
  python -m agent.demos.seed_test_mails 10            发 10 封
  python -m agent.demos.seed_test_mails 1 --dry       只填好表单不点发送，用来检查
运行期间别动鼠标键盘。
"""
import json
import re
import sys
import time
from pathlib import Path

from agent.tools import activate, center, click, find_windows, paste_text, press, window_rect
from agent.tools.web import read_page

CONTACTS = Path(__file__).resolve().parent.parent / 'test_contacts.local.json'
ADDR_RE = re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+')


def find_mail_window() -> int:
    # 坑：来新邮件时标题会变成“收到 1 封新邮件”，所以不按标题找，按页面里有没有「写信」找
    for w in find_windows(class_prefix='Chrome_WidgetWin'):
        if w.visible and w.title.endswith('Google Chrome') and any(x['text'] == '写信' for x in read_page(w.hwnd)):
            return w.hwnd
    sys.exit('没找到开着 QQ 邮箱的 Chrome 窗口')


def wait_for(hwnd, pred, timeout=12.0):
    """反复读页面，直到 pred(items) 找到东西（写信页要好几秒才出来）。交回 (找到的东西, items)。"""
    end = time.time() + timeout
    while time.time() < end:
        items = read_page(hwnd)
        got = pred(items)
        if got:
            return got, items
        time.sleep(0.5)
    return None, read_page(hwnd)


def tap(hwnd, item):
    l, t, _, _ = window_rect(hwnd)
    x, y = center(item['box'])
    click(l + int(x), t + int(y))


def first(items, text, kind=None):
    c = [x for x in items if x['text'] == text and (kind is None or x['kind'] == kind)]
    return c[0] if c else None


def recipients(items, row_box):
    """收件人那一栏里的每个收件人小块的文字。

    坑：地址回车后变成一个小块，块上只显示昵称（这个测试号的昵称就是 @ 前面那串号码），控件树里也读不到完整地址。
    所以发送前只能按昵称核对；发出去以后再到「已发送」详情页核对完整地址（那里显示 <地址>）。
    """
    x1, top, x2, bottom = row_box
    return {x['text'] for x in items
            if x['kind'] == 'TextControl' and x['text'] and top <= x['box'][1] and x['box'][3] <= bottom
            and x1 < x['box'][0] < x2 and x['text'] not in ('抄送', '密送', '分别发送')}


def is_only(got: set, addr: str) -> bool:
    """收件人只有一个，而且是 addr 本身或者它 @ 前面那段（小块上显示的昵称）。"""
    return len(got) == 1 and next(iter(got)) in (addr, addr.split('@')[0])


def send_one(hwnd, addr, k, dry):
    activate(hwnd)
    time.sleep(0.3)
    write, _ = wait_for(hwnd, lambda it: first(it, '写信'))
    if not write:
        return False, '找不到「写信」'
    tap(hwnd, write)
    label, items = wait_for(hwnd, lambda it: first(it, '收件人', 'TextControl') if first(it, '主题', 'EditControl') else None)
    if not label:
        return False, '写信页没出来'
    # 收件人输入框有时不在控件树里（点进去才出现），所以点“收件人”这几个字右边 200px 那一栏
    x1, y1, x2, y2 = label['box']
    tap(hwnd, {'box': [x2 + 150, y1, x2 + 250, y2]})
    time.sleep(0.5)
    paste_text(addr, clear=False)
    press('enter')          # 回车把地址变成收件人小块
    time.sleep(0.5)
    _, items = wait_for(hwnd, lambda it: first(it, '主题', 'EditControl'))
    tap(hwnd, first(items, '主题', 'EditControl'))
    time.sleep(0.3)
    paste_text(f'测试邮件 {k}', clear=False)
    body = first(items, '输入正文')
    if not body:
        return False, '找不到正文框'
    tap(hwnd, body)
    time.sleep(0.3)
    paste_text(f'这是第 {k} 封测试邮件，请回复。', clear=False)
    time.sleep(0.8)

    # 发送前核对：收件人那一行只能有测试号自己
    items = read_page(hwnd)
    label = first(items, '收件人', 'TextControl')
    row = [label['box'][2], label['box'][1] - 6, label['box'][2] + 1200, label['box'][3] + 6] if label else None
    got = recipients(items, row) if row else set()
    if not is_only(got, addr):
        return False, f'收件人不对，没发（读到 {len(got)} 个收件人）'
    if dry:
        return True, '表单已填好，收件人核对通过（--dry，没点发送）'
    tap(hwnd, first(items, '发送'))
    # 坑 1：发完不会停在“发送成功”页，而是直接跳回收件箱，所以要自己去「已发送」里找这封的标题。
    # 坑 2：不能看收件箱：连着发几封后，投递会被延后，已发送里有了、收件箱还没到。
    subject = f'测试邮件 {k}'
    time.sleep(3)
    sent, _ = wait_for(hwnd, lambda it: first(it, '已发送'))
    tap(hwnd, sent)
    ok, _ = wait_for(hwnd, lambda it: [x for x in it if x['kind'] == 'TextControl' and x['text'] == subject], timeout=15)
    return bool(ok), '已出现在「已发送」里' if ok else '「已发送」里没有这封'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    dry = '--dry' in sys.argv
    start = int(sys.argv[sys.argv.index('--from') + 1]) if '--from' in sys.argv else 1   # 中途停了从第几封接着发
    addr = json.loads(CONTACTS.read_text(encoding='utf-8'))['allowed_mail'][0]
    hwnd = find_mail_window()
    for k in range(start, start + n):
        ok, why = send_one(hwnd, addr, k, dry)
        print(f'第 {k} 封：{"成功" if ok else "失败"}，{why}')
        if not ok:
            sys.exit('停下，没发后面的')
        time.sleep(8)   # 发太快会被延后投递，也容易被风控


if __name__ == '__main__':
    main()
