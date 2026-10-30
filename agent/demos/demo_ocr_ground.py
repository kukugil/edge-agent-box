"""演示：OCR、UI-TARS 定位（补白边）、OCR 校正。离线，用实验里存好的微信截图，不碰真实窗口。

挑了 6 个目标，其中 01-t3（搜索框）和 03-i1（搜索框旁的按钮）贴着截图顶边，
是“不补白边就点偏”的典型。每个目标分别跑 不补边 / 补 56px，看点有没有落进标注框。
只打印坐标和对错，不存图：用的是真实微信截图，带标注的图不留在项目里。

用法（在项目根目录）：python -m agent.demos.demo_ocr_ground
需要 Spark 上的 UI-TARS 在线。
"""
import json
import sys
import time
from pathlib import Path

from PIL import Image

from agent.tools import center, find_text, ground, load_config, ocr, snap_to_text

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments' / 'wechat-vision'
PICK = ('01-t1', '01-t3', '02-t3', '02-i1', '03-i1', '04-i1')


def inside(pt, b):
    return pt is not None and b[0] <= pt[0] <= b[2] and b[1] <= pt[1] <= b[3]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    cfg = load_config()
    labels = json.loads((EXP / 'labels.json').read_text(encoding='utf-8-sig'))
    score = {0: 0, 56: 0}
    for s in labels['shots']:
        targets = [t for t in s['targets'] if t['id'] in PICK]
        if not targets:
            continue
        im = Image.open(EXP / 'shots' / s['file']).convert('RGB')

        t0 = time.time()
        items = ocr(im)
        print(f'\n== {s["file"]}  OCR 读到 {len(items)} 条文字，{(time.time() - t0) * 1000:.0f}ms；前 5 条：',
              [it['text'] for it in items[:5]])
        hit = find_text(items, '文件传输助手')
        if hit:
            print(f'   find_text("文件传输助手") → 中心 {tuple(round(v) for v in center(hit[0]["box"]))}')

        for t in targets:
            target = t['instruction'].removeprefix('点击')  # ground() 自己会加“点击”
            for pad in (0, 56):
                t0 = time.time()
                pt, raw = ground(im, target, cfg['grounder_url'], cfg['grounder_model'],
                                 cfg['grounder_max_pixels'], pad=pad)
                ms = (time.time() - t0) * 1000
                if pad == 0:
                    print(f'  [{t["id"]}] {target}  标注框 {t["box"]}')
                    print(f'     不补边：{fmt(pt)} {"✓" if inside(pt, t["box"]) else "✗"}  ({ms:.0f}ms, 原文 {raw!r})')
                    score[0] += inside(pt, t['box'])
                    continue
                snapped, note = snap_to_text(items, target, pt) if pt else (None, '')
                ok = inside(snapped, t['box'])
                score[56] += ok
                print(f'     补56px：{fmt(pt)} → 校正后 {fmt(snapped)} {"✓" if ok else "✗"}  {note}  ({ms:.0f}ms)')
    print(f'\n汇总（{len(PICK)} 个目标）：不补边 {score[0]} 个点中；补 56px + OCR 校正 {score[56]} 个点中')


def fmt(pt):
    return '无' if pt is None else f'({pt[0]:.0f},{pt[1]:.0f})'


if __name__ == '__main__':
    main()
