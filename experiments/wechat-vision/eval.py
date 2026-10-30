"""微信界面"不靠控件树"的四种定位方法对比。

A  只用 OCR：按目标文字找位置（相当于 RPA 的"按文字定位"）
B  OCR 出元素清单 → 文本模型挑一个（llm，现为 Qwen3.8-27B）
C  截图直接给通用视觉模型找位置（vlm，现为 Qwen3.8-27B 看图）
D  截图给专门的定位模型找位置（grounder，UI-TARS-1.5-7B）

用法：
  python eval.py            跑全部方法
  python eval.py A          只跑 A（不需要 Spark）
  python eval.py BD         只跑 B 和 D
模型地址在 config.json 里配。
"""
import base64
import csv
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

from rapidocr_onnxruntime import RapidOCR

HERE = Path(__file__).parent
CFG = json.loads((HERE / 'config.json').read_text(encoding='utf-8-sig'))  # -sig：兼容记事本存的带 BOM 文件
LABELS = json.loads((HERE / 'labels.json').read_text(encoding='utf-8-sig'))
ocr_engine = RapidOCR()


# ---------- 工具 ----------

def chat(url, model, messages, max_tokens=200):
    body = {
        'model': model, 'messages': messages, 'temperature': 0, 'max_tokens': max_tokens,
        'chat_template_kwargs': {'enable_thinking': False},  # 关掉 Qwen3 的思考，只要结论
    }
    req = urllib.request.Request(url.rstrip('/') + '/chat/completions', json.dumps(body).encode(),
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())['choices'][0]['message']['content']


def ocr(path):
    res, _ = ocr_engine(str(path))
    items = []
    for box, text, score in res or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        items.append({'text': text, 'box': [min(xs), min(ys), max(xs), max(ys)]})
    return items


def center(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def inside(pt, b):
    return pt is not None and b[0] <= pt[0] <= b[2] and b[1] <= pt[1] <= b[3]


def norm(s):
    return re.sub(r'[\s，。、,.!！?？:：“”"\']', '', s or '')


def first_json(s):
    m = re.search(r'\{.*?\}', s or '', re.S)
    return json.loads(m.group(0)) if m else {}


def image_url(path):
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()


def element_list(items):
    return '\n'.join(f'[{i}] {it["text"]}  位置=({int(center(it["box"])[0])},{int(center(it["box"])[1])})'
                     for i, it in enumerate(items))


# ---------- 三种方法：输入一条指令，输出一个点击点 ----------

def method_a(shot, items, t):
    if not t.get('anchor'):
        return None, '无文字锚点'
    for it in items:
        if t['anchor'] in it['text']:
            return center(it['box']), it['text']
    return None, '没找到文字'


def method_b(shot, items, t):
    prompt = (f'下面是微信窗口 OCR 识别出的界面元素清单（编号、文字、中心坐标）：\n{element_list(items)}\n\n'
              f'任务：{t["instruction"]}\n'
              '应该点击哪个元素？只输出 JSON：{"id": 编号}；清单里没有合适的元素就输出 {"id": -1}')
    out = chat(CFG['llm_url'], CFG['llm_model'], [{'role': 'user', 'content': prompt}])
    i = first_json(out).get('id', -1)
    if isinstance(i, int) and 0 <= i < len(items):
        return center(items[i]['box']), out
    return None, out


def method_c(shot, items, t, size):
    # 用 Qwen 视觉模型原生的定位格式：point_2d，0~1000 相对坐标
    prompt = (f'这是一张微信窗口截图。任务：{t["instruction"]}\n'
              '找到应该点击的界面元素，以 JSON 格式输出它的点坐标：[{"point_2d": [x, y], "label": "元素名"}]')
    msg = [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': image_url(shot)}},
        {'type': 'text', 'text': prompt}]}]
    out = chat(CFG['vlm_url'], CFG['vlm_model'], msg)
    m = (re.search(r'point_2d"?\s*:\s*\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)', out)
         or re.search(r'"x"\s*:\s*(\d+(?:\.\d+)?)\s*,\s*"y"\s*:\s*(\d+(?:\.\d+)?)', out))
    if m:
        return (float(m.group(1)) / 1000 * size[0], float(m.group(2)) / 1000 * size[1]), out
    return None, out


def smart_resize(w, h, factor=28):
    # UI-TARS（Qwen2.5-VL 架构）会先把图缩放到 28 的倍数，输出的是缩放后图片上的像素坐标
    max_pixels = CFG.get('grounder_max_pixels', 1003520)
    wb, hb = round(w / factor) * factor, round(h / factor) * factor
    if wb * hb > max_pixels:
        beta = math.sqrt(w * h / max_pixels)
        wb, hb = math.floor(w / beta / factor) * factor, math.floor(h / beta / factor) * factor
    return wb, hb


def method_d(shot, items, t, size):
    # UI-TARS 官方的定位提示词
    prompt = f'Output only the coordinate of one point in your response. What element matches the following task: {t["instruction"]}'
    msg = [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': image_url(shot)}},
        {'type': 'text', 'text': prompt}]}]
    out = chat(CFG['grounder_url'], CFG['grounder_model'], msg, max_tokens=64)
    return tars_point(out, size), out


def tars_point(out, size):
    # 兼容 <point>x y</point>（空格分隔）、(x,y)、<click point='(x,y)'/>
    m = re.search(r'(\d+(?:\.\d+)?)\s*[,\s]\s*(\d+(?:\.\d+)?)', out)
    if not m:
        return None
    rw, rh = smart_resize(*size)
    return float(m.group(1)) * size[0] / rw, float(m.group(2)) * size[1] / rh


TARS_OFFICIAL = ('You are a GUI agent. You are given a task and your action history, with screenshots. '
                 'You need to perform the next action to complete the task. \n\n## Output Format\n\nAction: ...\n\n\n'
                 "## Action Space\nclick(point='<point>x1 y1</point>')\n\n## User Instruction\n{instruction}")


def method_e(shot, items, t, size):
    # 同一个 UI-TARS，换成 1.5 官方动作格式的提示词（bytedance/UI-TARS issue #201）
    msg = [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': image_url(shot)}},
        {'type': 'text', 'text': TARS_OFFICIAL.format(instruction=t['instruction'])}]}]
    out = chat(CFG['grounder_url'], CFG['grounder_model'], msg, max_tokens=64)
    return tars_point(out, size), out


# ---------- 读屏题：B 用 OCR 文字回答，C 看图回答 ----------

def read_b(items, q):
    prompt = f'下面是微信窗口 OCR 识别出的文字：\n{element_list(items)}\n\n问题：{q}\n只输出答案本身，不要解释。'
    return chat(CFG['llm_url'], CFG['llm_model'], [{'role': 'user', 'content': prompt}])


def read_c(shot, q):
    msg = [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': image_url(shot)}},
        {'type': 'text', 'text': f'看这张微信截图回答：{q}\n只输出答案本身，不要解释。'}]}]
    return chat(CFG['vlm_url'], CFG['vlm_model'], msg)


# ---------- 主流程 ----------

def main():
    from PIL import Image
    methods = sys.argv[1] if len(sys.argv) > 1 else 'ABCD'
    rows = []
    for s in LABELS['shots']:
        shot = HERE / 'shots' / s['file']
        size = Image.open(shot).size
        t0 = time.time(); items = ocr(shot); ocr_ms = int((time.time() - t0) * 1000)
        print(f'== {s["file"]}  OCR {len(items)} 条，{ocr_ms}ms')
        for t in s.get('targets', []):
            for m in methods:
                t0 = time.time()
                try:
                    if m == 'A': pt, raw = method_a(shot, items, t)
                    elif m == 'B': pt, raw = method_b(shot, items, t)
                    elif m == 'C': pt, raw = method_c(shot, items, t, size)
                    elif m == 'D': pt, raw = method_d(shot, items, t, size)
                    else: pt, raw = method_e(shot, items, t, size)
                except Exception as e:
                    pt, raw = None, f'出错: {e}'
                ms = int((time.time() - t0) * 1000) + (ocr_ms if m in 'AB' else 0)
                ok = inside(pt, t['box'])
                rows.append(['点击', s['file'], t['id'], t['kind'], m, int(ok), ms, t['instruction'],
                             '' if pt is None else f'{pt[0]:.0f},{pt[1]:.0f}', str(raw)[:200]])
                print(f'  [{m}] {"✓" if ok else "✗"} {t["instruction"]}  {ms}ms')
        for q in s.get('questions', []):
            for m in re.sub('[ADE]', '', methods):  # 读屏题只比 B（读 OCR 文字）和 C（看图）
                t0 = time.time()
                try:
                    ans = read_b(items, q['q']) if m == 'B' else read_c(shot, q['q'])
                except Exception as e:
                    ans = f'出错: {e}'
                ms = int((time.time() - t0) * 1000)
                ok = norm(q['answer']) in norm(ans)
                rows.append(['读屏', s['file'], q['q'][:20], 'text', m, int(ok), ms, q['q'], '', ans[:200]])
                print(f'  [{m}] {"✓" if ok else "✗"} {q["q"]} → {ans[:40]!r}')

    out_name = 'results.csv' if methods == 'ABCD' else f'results_{methods}.csv'  # 只跑部分方法时单独存，不覆盖全量结果
    with open(HERE / out_name, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['题型', '截图', '编号', '目标类型', '方法', '正确', '耗时ms', '指令', '点击点', '模型原始输出'])
        w.writerows(rows)

    print('\n===== 汇总 =====')
    for kind in ('点击', '读屏'):
        for sub in ('text', 'icon'):
            for m in methods:
                rs = [r for r in rows if r[0] == kind and r[3] == sub and r[4] == m]
                if rs:
                    acc = sum(r[5] for r in rs) / len(rs)
                    avg = sum(r[6] for r in rs) / len(rs)
                    print(f'{kind}-{"文字目标" if sub == "text" else "非文字目标"}  方法{m}: '
                          f'{sum(r[5] for r in rs)}/{len(rs)} = {acc:.0%}  平均 {avg:.0f}ms')


if __name__ == '__main__':
    main()
