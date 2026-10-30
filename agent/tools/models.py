"""调用 Spark 上的模型（OpenAI 兼容接口）。"""
import base64
import io
import json
import urllib.request
from pathlib import Path

from PIL import Image

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'config.json'


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    """读模型地址配置（agent/config.json）。"""
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))  # -sig：兼容记事本存的带 BOM 文件


def image_url(image: Image.Image) -> str:
    """把图片编码成接口能收的 data URL。"""
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def chat(url: str, model: str, messages: list, max_tokens: int = 200, timeout: int = 120) -> str:
    """发一次对话请求，返回模型回复的文字。temperature=0，结果尽量稳定。"""
    body = {
        'model': model, 'messages': messages, 'temperature': 0, 'max_tokens': max_tokens,
        'chat_template_kwargs': {'enable_thinking': False},  # 关掉 Qwen3 的思考，只要结论
    }
    req = urllib.request.Request(url.rstrip('/') + '/chat/completions', json.dumps(body).encode(),
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())['choices'][0]['message']['content']
