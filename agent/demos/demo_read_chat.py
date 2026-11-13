"""演示：用 read_chat + wait_quiet 读白名单会话，看交给 reply 的消息对不对（D15）。

只读：不点、不按键、不滚动，不存截图。会话要人手动点开，标题对不上就不读。
打印的是交给 reply.py 的格式（谁、类型、时间、文字），不打印会话名。
日志写在 %TEMP%\\edge-agent\\demo-read-chat-*.log，不写进项目。

用法（在项目根目录）：
  python -m agent.demos.demo_read_chat --chat 2 [--rounds 5] [--ocr]
    --chat N   白名单第几个会话（必填）
    --rounds   读几轮：每轮等对方停 3 秒，打印一次，再等下一条新消息
    --ocr      跳过控件树，只用 OCR 兜底（看兜底能不能用）
"""
import argparse
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from agent.core import graph, messages
from agent.core.trigger import Tee, WHITELIST
from agent.tools import chatlog


def show(r: dict) -> None:
    print(f"  来源={r['source']}  静下来了={r.get('quiet')}  {r['reason']}  共 {len(r['msgs'])} 条")
    for m in r['msgs']:
        at = m['at'].strftime('%m-%d %H:%M') if m['at'] else '—'
        print(f"    {m['who']:<4} {m['kind']:<7} {at}  {m['text']!r}")


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser()
    p.add_argument('--chat', type=int, required=True)
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--ocr', action='store_true')
    args = p.parse_args()
    log_dir = Path(tempfile.gettempdir()) / 'edge-agent'
    log_dir.mkdir(exist_ok=True)
    sys.stdout = Tee(log_dir / f'demo-read-chat-{datetime.now():%m%d-%H%M}.log')
    wins = graph._wechat_windows()          # 不按 Ctrl+Alt+W：演示不碰键盘
    if not wins:
        sys.exit('微信不在桌面上（最小化或在托盘），先手动打开')
    hwnd, title = wins[0].hwnd, WHITELIST[args.chat - 1]
    if args.ocr:
        chatlog.message_list = lambda h: None   # 只在这个进程里假装没有控件树
    read = lambda: messages.read_chat(hwnd, title)
    last = None
    for n in range(1, args.rounds + 1):
        print(f'{datetime.now():%H:%M:%S} 第 {n} 轮：等新消息（最多 5 分钟）')
        end = time.time() + 300
        while time.time() < end:
            t = time.time()
            r = read()
            if r['ok'] and [m['text'] for m in r['msgs']] != last:
                break
            if not r['ok']:
                print(f'  这次没读成：{r["reason"]}')
            time.sleep(1)
        else:
            print('  5 分钟没有新消息，结束')
            break
        r = messages.wait_quiet(read, since=t)
        print(f'  读一次约 {time.time() - t:.1f} 秒（含等静）')
        show(r)
        last = [m['text'] for m in r['msgs']]


if __name__ == '__main__':
    main()
