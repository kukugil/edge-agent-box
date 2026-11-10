"""演示：找微信会话列表里带未读角标的行。只看不动，运行时可以正常用电脑。

运行（在项目根目录，微信打开、不要最小化，可以被挡住）：
  python -m agent.demos.demo_unread          看一眼
  python -m agent.demos.demo_unread 60       盯 60 秒，总数字一变就重扫一次
只打印坐标、是不是小红点、在不在白名单，不打印标题和消息内容。
"""
import json
import sys
import time
from pathlib import Path

from agent.core.graph import _wechat_windows
from agent.tools import capture_window, norm
from agent.tools.unread import find_unread_rows, total_badge

CONTACTS = Path(__file__).resolve().parent.parent / 'test_contacts.local.json'
WHITELIST = json.loads(CONTACTS.read_text(encoding='utf-8'))['allowed_chats']


def show(hwnd: int) -> None:
    shot = capture_window(hwnd)
    if shot is None:
        print('拿不到画面')
        return
    t = time.time()
    rows = find_unread_rows(shot)
    print(f'{time.strftime("%H:%M:%S")} 扫了 {time.time() - t:.2f} 秒，{len(rows)} 行带角标')
    for r in rows:
        ok = any(norm(w) == norm(r['title']) for w in WHITELIST)
        kind = '小红点' if r['dot_only'] else '数字'
        print(f'  角标 {r["box"]}  {kind}  标题 {len(r["title"])} 个字  {"白名单" if ok else "-"}')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    wins = _wechat_windows()
    if not wins:
        sys.exit('没找到微信主窗口：从托盘点开，不要最小化')
    hwnd = wins[0].hwnd
    show(hwnd)
    if len(sys.argv) < 2:
        return
    end = time.time() + float(sys.argv[1])
    last = total_badge(capture_window(hwnd))
    while time.time() < end:
        time.sleep(0.5)
        shot = capture_window(hwnd)
        if shot is None:
            continue
        now = total_badge(shot)
        if now != last:
            print('叮：总未读数变了')
            show(hwnd)
            last = now


if __name__ == '__main__':
    main()
