"""探针：能不能读准对方发的消息（D15 第 1 步，方案能否成立的前提）。

只截图、只识别，不动键鼠，不存截图。会话要你自己先点开；标题不在白名单就不读聊天区。
每次对方气泡有变化，打印新出现的气泡：第几条、几行、行距、读得清不清、读到的字。
传了 --expect（一行一句，空行分隔多行消息，文件放临时目录，不放项目），就逐条比对读得全不全。

用法（在项目根目录）：
  python -m agent.demos.probe_read_msgs --seconds 300 [--expect 路径]
日志写在 %TEMP%\\edge-agent\\probe-read-*.log，不写进项目。
"""
import argparse
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from agent.core import graph
from agent.core.check import LIST_RIGHT, CHAT_TOP, chat_title, layout
from agent.core.messages import THEM, bubbles
from agent.core.trigger import Tee, WHITELIST, chat_items, ocr_crop, title_box
from agent.tools import capture_window, norm


def load_expect(path: str | None) -> list[str]:
    """输入期望文件路径，输出期望句子列表；空行分隔，一段里的多行算一条多行消息。"""
    if not path:
        return []
    blocks = Path(path).read_text(encoding='utf-8').split('\n\n')
    return [b.strip('\n') for b in blocks if b.strip()]


def line_gaps(items: list[dict], lay: dict) -> list[int]:
    """输入 OCR 和布局，输出左半边相邻字块的上下空隙，用来调 LINE_GAP。"""
    left = sorted((it for it in items if LIST_RIGHT < it['box'][0] < (LIST_RIGHT + lay['right']) / 2
                   and it['box'][1] > CHAT_TOP and it['box'][3] < lay['bottom']), key=lambda it: it['box'][1])
    return [int(b['box'][1] - a['box'][3]) for a, b in zip(left, left[1:])]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser()
    p.add_argument('--seconds', type=float, default=300)
    p.add_argument('--expect')
    args = p.parse_args()
    expect = load_expect(args.expect)
    log_dir = Path(tempfile.gettempdir()) / 'edge-agent'
    log_dir.mkdir(exist_ok=True)
    log = log_dir / f'probe-read-{datetime.now():%m%d-%H%M}.log'
    sys.stdout = Tee(log)
    print(f'日志：{log}')
    wins = graph._wechat_windows()     # 不按 Ctrl+Alt+W：探针一下键盘都不碰
    if not wins:
        sys.exit('微信不在桌面上（最小化或在托盘），先手动打开并点开测试好友的会话')
    hwnd = wins[0].hwnd
    seen: list[str] = []               # 已打印过的对方气泡文字（按出现顺序）
    hits = set()
    end = time.time() + args.seconds
    print(f'开始读 {args.seconds:.0f} 秒；期望 {len(expect)} 条')
    while time.time() < end:
        shot = capture_window(hwnd)
        if shot is None:
            time.sleep(1)
            continue
        title = chat_title(ocr_crop(shot, title_box(shot)))
        who = next((i for i, w in enumerate(WHITELIST, 1) if title and norm(title) == norm(w)), None)
        if who is None:
            print(f'{datetime.now():%H:%M:%S} 开着的会话不在白名单（或标题读不清），不读聊天区')
            time.sleep(3)
            continue
        lay = layout(shot.image)
        items = chat_items(shot, lay)
        bs = [b for b in bubbles(items, lay) if b['who'] == THEM]
        texts = [b['text'] for b in bs]
        # 新出现的：上一屏的尾巴和这一屏的开头对齐，多出来的才算新（同一句再发一遍也算新的）
        k = min(len(texts), len(seen))
        while k > 0 and texts[:k] != seen[-k:]:
            k -= 1
        new = bs[k:]
        for b in new:
            lines = sum(1 for it in items if it['box'][1] >= b['box'][1] and it['box'][3] <= b['box'][3]
                        and LIST_RIGHT < it['box'][0] < b['box'][2])
            match = next((i for i, e in enumerate(expect, 1) if norm(e) == norm(b['text'])), None)
            exact = match and expect[match - 1].replace('\n', '') == b['text']
            if match:
                hits.add(match)
            print(f'{datetime.now():%H:%M:%S} 白名单#{who} 对方气泡 {lines} 行 高{b["box"][3] - b["box"][1]}'
                  f' 清楚={b["clear"]} 被切={b["cut"]} 期望={match or "-"}{"(逐字)" if exact else ""}：{b["text"]}')
        if new:
            print(f'  左半边行距：{line_gaps(items, lay)}')
            seen = texts
        time.sleep(1)
    if expect:
        miss = [i for i in range(1, len(expect) + 1) if i not in hits]
        print(f'\n期望 {len(expect)} 条，读对 {len(hits)} 条；没读对的：{miss or "无"}')


if __name__ == '__main__':
    main()
