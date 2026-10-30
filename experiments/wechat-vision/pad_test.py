"""实验 2：在截图顶部补一条白边，看 UI-TARS 的“偏下约 28px”会不会跟着变。

判读：
  补边后点击点（换算回原图）也整体移动 → 偏差和“元素离图片顶边的距离”有关，补边就能修
  补边后点击点不变                       → 偏差跟着元素本身走，是模型对这类元素的认知问题

用法：python pad_test.py 0 14 28   （参数是白边高度，单位像素）
结果写到 results_pad.csv。
"""
import csv
import sys
import time

from PIL import Image

import eval as ev

pads = [int(p) for p in sys.argv[1:]] or [0, 14, 28]
tmp = ev.HERE / 'shots' / '_padded'
tmp.mkdir(exist_ok=True)
rows = []
for s in ev.LABELS['shots']:
    src = Image.open(ev.HERE / 'shots' / s['file']).convert('RGB')
    for pad in pads:
        im = Image.new('RGB', (src.width, src.height + pad), 'white')
        im.paste(src, (0, pad))
        path = tmp / f'{pad}-{s["file"]}'
        im.save(path)
        for t in s['targets']:
            t0 = time.time()
            pt, raw = ev.method_d(path, None, t, im.size)
            if pt:
                pt = (pt[0], pt[1] - pad)  # 换算回原图坐标
            ok = ev.inside(pt, t['box'])
            rows.append([pad, s['file'], t['id'], t['kind'], int(ok), int((time.time() - t0) * 1000),
                         '' if pt is None else f'{pt[0]:.0f},{pt[1]:.0f}', raw])
    print('完成', s['file'])

with open(ev.HERE / 'results_pad.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f)
    w.writerow(['白边px', '截图', '编号', '目标类型', '正确', '耗时ms', '点击点(原图)', '模型原始输出'])
    w.writerows(rows)

print('\n===== 汇总 =====')
for pad in pads:
    rs = [r for r in rows if r[0] == pad]
    print(f'白边 {pad:>2}px：{sum(r[4] for r in rs)}/{len(rs)}')
print('\n===== 重点题（框中心 vs 各白边下的点击点）=====')
boxes = {t['id']: t['box'] for s in ev.LABELS['shots'] for t in s['targets']}
for tid in ('01-t3', '03-i1', '02-i1', '02-t3', '04-i1'):
    b = boxes[tid]
    pts = '  '.join(f'{r[0]}px→{r[6]}{"✓" if r[4] else "✗"}' for r in rows if r[2] == tid)
    print(f'{tid} 框中心=({(b[0] + b[2]) / 2:.0f},{(b[1] + b[3]) / 2:.0f})  {pts}')
