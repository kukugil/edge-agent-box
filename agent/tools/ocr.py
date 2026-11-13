"""OCR：读出图片里的文字和位置（RapidOCR，本机 CPU 运行，不需要 Spark）。"""
import re
from functools import lru_cache

from PIL import Image


@lru_cache(maxsize=2)
def _engine(no_upscale: bool = False):
    # 模型加载要一两秒，第一次用到时才加载，之后复用同一个
    from rapidocr_onnxruntime import RapidOCR
    if no_upscale:
        # 坑（11/12 实测）：默认会把图的短边放大到 736 再找字。标题条只有 90 高，等于放大 8 倍，
        # 认一次 1.1 秒；只缩不放后 0.07 秒，认出的字和把握一样。整张窗口 800 高，两种设置都不缩放
        return RapidOCR(det_limit_side_len=960, det_limit_type='max')
    return RapidOCR()


def ocr(image: Image.Image, min_score: float = 0.0, no_upscale: bool = False) -> list[dict]:
    """识别图片里的文字。

    输入：PIL 图片（通常是 shot.image）
    输出：[{'text': '文件传输助手', 'box': [x1, y1, x2, y2], 'score': 0.98}, ...]
          box 是文字外接框，截图内坐标；score 是识别把握，低于 min_score 的丢掉
    no_upscale=True：小图不放大，快十几倍。只用在标题条这种一行字的窄条上；聊天区还按默认放大，免得小字认漏
    """
    res, _ = _engine(no_upscale)(image)
    items = []
    for box, text, score in res or []:
        if float(score) < min_score:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        items.append({'text': text, 'box': [min(xs), min(ys), max(xs), max(ys)], 'score': round(float(score), 3)})
    return items


def norm(s: str) -> str:
    """去掉空格和常见标点，比较文字时用（OCR 常把标点认错）。"""
    return re.sub(r'[\s，。、,.!！?？:：“”"\']', '', s or '')


def center(box) -> tuple[float, float]:
    """框的中心点。"""
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def find_text(items: list[dict], text: str) -> list[dict]:
    """在 OCR 结果里找包含 text 的条目（忽略空格标点）。"""
    t = norm(text)
    return [it for it in items if t and t in norm(it['text'])]
