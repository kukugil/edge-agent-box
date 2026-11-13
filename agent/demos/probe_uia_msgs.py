"""探针：微信 4.x 打开"屏幕阅读器"标志后，控件树里能不能读到消息（D15 读消息方案实验）。

只读控件属性，不点、不按键、不滚动，不存截图。
  --structure  看结构：数各类控件，列出 AutomationId。不打印任何 Name（Name 里有会话名和聊天内容）
  --read       读消息：先用 OCR 核对开着的会话在白名单里，才读消息列表；每条打印类型、左右、文字
               加 --seconds N 就一直盯，列表多出条目时打印（看新消息能不能不点就出现）
               加 --expect 文件（一段一句，空行分隔）就逐条比对
日志写在 %TEMP%\\edge-agent\\probe-uia-*.log，不写进项目。

依据：wechat-uia-reader（微信 4.2 实测）说要先开系统屏幕阅读器标志、再重启微信，控件树才完整；
本机 4.1.15.56 在 11/11 查到这个标志是关的，10/27 读不到控件树可能就是这个原因。

用法（在项目根目录）：
  python -m agent.demos.probe_uia_msgs --structure
  python -m agent.demos.probe_uia_msgs --read [--seconds 300] [--expect 路径]
"""
import argparse
import ctypes
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import uiautomation as auto

from agent.core import graph
from agent.core.check import chat_title
from agent.core.trigger import Tee, WHITELIST, ocr_crop, title_box
from agent.tools import capture_window, norm, window_rect

MAX_NODES = 5000    # 防止树特别大时一直走下去
MAX_DEPTH = 40


def screen_reader_on() -> bool:
    """输出系统屏幕阅读器标志（SPI_GETSCREENREADER）。只读。"""
    v = ctypes.c_bool(False)
    ctypes.windll.user32.SystemParametersInfoW(0x46, 0, ctypes.byref(v), 0)
    return bool(v.value)


def walk(root) -> list[tuple[int, object]]:
    """输入根控件，输出 [(深度, 控件)]，深度优先，最多 MAX_NODES 个。"""
    out, stack = [], [(0, root)]
    while stack and len(out) < MAX_NODES:
        depth, c = stack.pop()
        out.append((depth, c))
        if depth < MAX_DEPTH:
            try:
                kids = c.GetChildren()
            except Exception:       # 控件刚好被销毁时会抛，跳过就行
                kids = []
            stack.extend((depth + 1, k) for k in reversed(kids))
    return out


def structure(hwnd: int) -> None:
    t = time.time()
    nodes = walk(auto.ControlFromHandle(hwnd))
    print(f'共 {len(nodes)} 个控件，走一遍 {time.time() - t:.1f} 秒，最深 {max(d for d, _ in nodes)} 层')
    kinds = Counter((c.ControlTypeName, c.ClassName) for _, c in nodes)
    for (ct, cls), n in kinds.most_common(40):
        print(f'  {n:4d}  {ct:<22} {cls}')
    # 坑（11/11 实测）：会话行的 id 是 "session_item_" + 会话名，直接带人名和群名。
    # 所以只留"前缀"：第一个下划线段之后、像名字的部分都换成 *，再合并计数
    def mask(aid: str) -> str:
        if aid.startswith('session_item_'):
            return 'session_item_*'
        return aid if all(ch.isascii() for ch in aid) else '（含非 ASCII，已隐藏）'
    ids = Counter(mask(c.AutomationId) for _, c in nodes if c.AutomationId)
    print(f'AutomationId {len(ids)} 种（不含 Name）：')
    for aid, n in ids.most_common(60):
        print(f'  {n:3d}  {aid}')


def find_message_list(hwnd: int):
    """输入窗口句柄，输出最像消息列表的控件：优先 AutomationId 带 message 的，其次右半边子项最多的 List。"""
    nodes = [c for _, c in walk(auto.ControlFromHandle(hwnd))]
    named = [c for c in nodes if 'message' in (c.AutomationId or '').lower() and c.ControlTypeName == 'ListControl']
    if named:
        return named[0]
    l, _, r, _ = window_rect(hwnd)
    lists = [c for c in nodes if c.ControlTypeName == 'ListControl'
             and c.BoundingRectangle.left > l + (r - l) * 0.2]
    return max(lists, key=lambda c: len(c.GetChildren()), default=None)


def avatar_side(gray, box: tuple[int, int, int, int]) -> str:
    """输入窗口灰度图（numpy）和条目框（窗口内坐标），输出 '左'（对方）/'右'（我方）/'无'（时间线等）/'?'。

    为什么看头像：消息条目的框横跨整个聊天区，按框分不出左右；表情包、图片又没有字，OCR 也帮不上。
    单聊里每条消息都带头像：对方的在行左边 20–56px，我方的在行右边 20–56px（11/11 真机量，960 宽）。
    只看行的上 48px：头像贴着行顶；看太多会把下一行的头像算进来（时间线行只有 41px 高）。
    """
    x1, y1, x2, y2 = box
    top = gray[y1:y1 + min(48, y2 - y1)]
    if top.size == 0:
        return '?'
    prof = top.std(axis=0)

    def busy(a, b):                      # 这一段里"有内容"的列占多少
        seg = prof[a:b]
        return float((seg > 15).mean()) if seg.size else 0.0
    left, right = busy(x1 + 20, x1 + 56) > 0.6, busy(x2 - 56, x2 - 20) > 0.6
    return {(True, False): '左', (False, True): '右', (False, False): '无'}.get((left, right), '?')


def describe(item, gray, origin, list_top) -> dict:
    """输入消息条目、窗口灰度图、窗口左上角屏幕坐标和列表上沿（窗口内），输出 {类, 文字, 框, 左右}。文字取条目的 Name。"""
    r = item.BoundingRectangle
    box = (r.left - origin[0], r.top - origin[1], r.right - origin[0], r.bottom - origin[1])
    if item.ClassName == 'mmui::ChatItemView':
        side = '无'          # 时间线、系统提示：不属于任何一方（11/11 真机滚动时被判成过"左"）
    elif gray is None or box[1] < list_top:
        side = '?'           # 行顶被切掉，头像不在画面里；滚动中截图和控件框也可能错开，等下一屏
    else:
        side = avatar_side(gray, box)
    return {'cls': item.ClassName, 'text': item.Name or '', 'box': box, 'side': side}


def read(hwnd: int, seconds: float, expect: list[str], only: int | None = None) -> None:
    who = None
    for _ in range(60):                  # 等人切到白名单会话，最多 2 分钟；之前一律不读消息列表
        shot = capture_window(hwnd)
        title = chat_title(ocr_crop(shot, title_box(shot))) if shot else ''
        who = next((i for i, w in enumerate(WHITELIST, 1) if title and norm(title) == norm(w)), None)
        if who is not None and (only is None or who == only):
            break
        who = None
        time.sleep(2)
    if who is None:
        sys.exit('开着的会话不在白名单（或标题读不清），不读消息列表。先手动点开测试好友的会话')
    lst = find_message_list(hwnd)
    if lst is None:
        sys.exit('没找到消息列表控件（屏幕阅读器标志开了吗？微信重启了吗？）')
    print(f'白名单#{who}  消息列表：{lst.ControlTypeName} {lst.ClassName} id={lst.AutomationId!r}')
    import numpy as np
    seen: list[str] = []
    hits = set()
    end = time.time() + seconds
    first = True
    while True:
        t = time.time()
        shot = capture_window(hwnd)
        # 坑（11/11 实测）：只在开头核对一次标题不够。人中途切到别的会话，消息列表控件还是同一个，
        # 探针照读不误，读到了白名单外的聊天。所以每一圈都重新核对；最小化截不到图、核对不了，也停读
        now = chat_title(ocr_crop(shot, title_box(shot))) if shot else ''
        if not (now and norm(now) == norm(WHITELIST[who - 1])):
            print(f'{datetime.now():%H:%M:%S} 标题对不上或截不到图（最小化？），这一圈不读')
            seen = []
            if time.time() >= end:
                break
            time.sleep(1)
            continue
        gray = np.asarray(shot.image.convert('L')).astype(float)
        origin = window_rect(hwnd)[:2]
        items = [describe(c, gray, origin, lst.BoundingRectangle.top - origin[1]) for c in lst.GetChildren()]
        cost = time.time() - t
        keys = [f"{it['cls']}|{it['text']}|{it['side']}" for it in items]
        k = min(len(keys), len(seen))
        while k > 0 and keys[:k] != seen[-k:]:
            k -= 1
        new = items if first else items[k:]
        for it in new:
            m = next((i for i, e in enumerate(expect, 1) if norm(e) == norm(it['text'])), None)
            exact = m and expect[m - 1].replace('\n', '') == it['text'].replace('\n', '')
            if m:
                hits.add(m)
            print(f'{datetime.now():%H:%M:%S} [{it["cls"]}] {it["side"]} 框{it["box"]} 期望={m or "-"}'
                  f'{"(逐字)" if exact else ""}：{it["text"]!r}')
        if new:
            print(f'  这一屏 {len(items)} 条，读一遍 {cost:.2f} 秒')
        seen, first = keys, False
        if time.time() >= end:
            break
        time.sleep(1)
    if expect:
        print(f'\n期望 {len(expect)} 条，读对 {len(hits)} 条；没读对的：{[i for i in range(1, len(expect) + 1) if i not in hits] or "无"}')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--structure', action='store_true')
    g.add_argument('--read', action='store_true')
    p.add_argument('--seconds', type=float, default=0)
    p.add_argument('--expect')
    p.add_argument('--chat', type=int, help='只读白名单第几个会话')
    args = p.parse_args()
    log_dir = Path(tempfile.gettempdir()) / 'edge-agent'
    log_dir.mkdir(exist_ok=True)
    sys.stdout = Tee(log_dir / f'probe-uia-{datetime.now():%m%d-%H%M}.log')
    print(f'屏幕阅读器标志：{"开" if screen_reader_on() else "关"}')
    wins = graph._wechat_windows()       # 不按 Ctrl+Alt+W：探针不碰键盘
    for _ in range(120):                 # 最小化时等人还原，最多等 2 分钟（11/11 第一次就因为最小化直接退了）
        if wins:
            break
        time.sleep(1)
        wins = graph._wechat_windows()
    if not wins:
        sys.exit('微信不在桌面上（最小化或在托盘），先手动打开')
    if args.structure:
        structure(wins[0].hwnd)
    else:
        expect = []
        if args.expect:
            expect = [b.strip('\n') for b in Path(args.expect).read_text(encoding='utf-8').split('\n\n') if b.strip()]
        read(wins[0].hwnd, args.seconds, expect, args.chat)


if __name__ == '__main__':
    main()
