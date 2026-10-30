"""把 labels.json 的标注框画到截图上，存到 labeled/，方便人工抽查。"""
import json
from pathlib import Path
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
labels = json.loads((HERE / 'labels.json').read_text(encoding='utf-8-sig'))
out = HERE / 'labeled'
out.mkdir(exist_ok=True)
for s in labels['shots']:
    im = Image.open(HERE / 'shots' / s['file']).convert('RGB')
    d = ImageDraw.Draw(im)
    for t in s['targets']:
        color = (0, 160, 255) if t['kind'] == 'text' else (255, 0, 120)  # 蓝=文字目标，红=非文字目标
        d.rectangle(t['box'], outline=color, width=2)
        d.text((t['box'][0] + 2, t['box'][1] + 1), t['id'][3:], fill=color)
    im.save(out / s['file'])
    print('已画', s['file'])
