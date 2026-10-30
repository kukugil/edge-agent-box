"""OCR：读出图片里的文字和位置（RapidOCR，本机 CPU 运行，不需要 Spark）。"""
import re
from functools import lru_cache

from PIL import Image


@lru_cache(maxsize=1)
def _engine():
    # 模型加载要一两秒，第一次用到时才加载，之后复用同一个
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def ocr(image: Image.Image, min_score: float = 0.0) -> list[dict]:
    """识别图片里的文字。

    输入：PIL 图片（通常是 shot.image）
    输出：[{'text': '文件传输助手', 'box': [x1, y1, x2, y2], 'score': 0.98}, ...]
          box 是文字外接框，截图内坐标；score 是识别把握，低于 min_score 的丢掉
    """
    res, _ = _engine()(image)
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
