"""定位：把“点哪个元素”的一句话变成截图里的坐标。"""
import math
import re

from PIL import Image

from .models import chat, image_url
from .ocr import center, norm
from .screen import pad_top

PAD_TOP = 56
PROMPT = 'Output only the coordinate of one point in your response. What element matches the following task: {}'


def _smart_resize(w: int, h: int, max_pixels: int, factor: int = 28) -> tuple[int, int]:
    # UI-TARS（Qwen2.5-VL 架构）会先把图缩放到 28 的倍数、总像素不超过 max_pixels，
    # 它输出的坐标是“缩放后那张图”上的像素，所以要知道缩放后的尺寸才能换算回来
    wb, hb = round(w / factor) * factor, round(h / factor) * factor
    if wb * hb > max_pixels:
        beta = math.sqrt(w * h / max_pixels)
        wb, hb = math.floor(w / beta / factor) * factor, math.floor(h / beta / factor) * factor
    return wb, hb


def _parse_point(out: str, size: tuple[int, int], max_pixels: int):
    # 兼容 <point>x y</point>、(x,y)、<click point='(x,y)'/> 几种输出
    m = re.search(r'(\d+(?:\.\d+)?)\s*[,\s]\s*(\d+(?:\.\d+)?)', out or '')
    if not m:
        return None
    rw, rh = _smart_resize(*size, max_pixels)
    return float(m.group(1)) * size[0] / rw, float(m.group(2)) * size[1] / rh


def ground(image: Image.Image, target: str, url: str, model: str,
           max_pixels: int = 1003520, pad: int = PAD_TOP) -> tuple[tuple[float, float] | None, str]:
    """让 UI-TARS 找出 target 描述的元素在哪。

    输入：image  截图（shot.image）
          target 元素描述，例如“左上角写着“搜索”的搜索框”；会自动加上“点击”二字
          url / model / max_pixels  来自 config.json 的 grounder_*
          pad    顶部补白边的高度，默认 56；传 0 关掉
    输出：(点, 模型原文)。点是原图（未补边）里的 (x, y)；模型没给出坐标时点为 None。
    内部三步：补白边 → 调 UI-TARS → 先按缩放比例换算、再减掉白边高度。
    """
    img = pad_top(image, pad) if pad else image.convert('RGB')
    msg = [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': image_url(img)}},
        {'type': 'text', 'text': PROMPT.format('点击' + target)}]}]
    out = chat(url, model, msg, max_tokens=64)
    pt = _parse_point(out, img.size, max_pixels)
    if pt is None:
        return None, out
    return (pt[0], pt[1] - pad), out


# 引号里的文字后面紧跟这些词，说明它是参照物（“‘发送’按钮左侧的输入框”），不是要点的目标
_REFERENCE_WORDS = ('左', '右', '上方', '下方', '旁', '附近', '之间')


def snap_to_text(items: list[dict], target: str, pt: tuple[float, float],
                 max_dist: float = 60) -> tuple[tuple[float, float], str]:
    """用 OCR 校正 UI-TARS 的点。

    做法：取 target 里引号括起来的文字，在 OCR 结果里找到它，
    选离 pt 最近的那个文字框；如果距离不超过 max_dist，就把点拉到文字框中心。
    输入：items  ocr() 的结果；target 元素描述；pt ground() 给的点
    输出：(校正后的点, 说明)。没校正时原样返回 pt，说明为空字符串。
    为什么限制 60px：UI-TARS 已知偏差约 28px；离得太远多半是别处的同名文字，不能乱拉。
    """
    quoted = []
    for m in re.finditer(r'[“"‘\']([^”"’\']+)[”"’\']', target):
        after = target[m.end():m.end() + 6]
        if len(m.group(1)) >= 2 and not any(w in after for w in _REFERENCE_WORDS):
            quoted.append(m.group(1))
    cands = [it for it in items for q in quoted if norm(q) in norm(it['text'])]
    if not cands:
        return pt, ''
    best = min(cands, key=lambda it: math.dist(center(it['box']), pt))
    c = center(best['box'])
    if math.dist(c, pt) > max_dist:
        return pt, ''
    return c, f'OCR 校正到“{best["text"]}”'
