"""调用 Spark 上的模型（OpenAI 兼容接口）。托盘"模型设置"里改过地址、模型名和 key 时用改过的。"""
import base64
import io
import json
import os
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'config.json'
MODEL_KEYS = ('llm_url', 'llm_model', 'vlm_url', 'vlm_model')


def user_config_path() -> Path:
    """托盘"模型设置"保存的位置。只放地址和模型名，密钥在 Windows 凭据管理器（agent/tools/secrets.py）。"""
    return Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'edge-agent' / 'model.json'


def load_user_config() -> dict:
    """读托盘里设的模型；没设过或文件坏了交回空字典，用仓库里的默认值。"""
    try:
        data = json.loads(user_config_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in MODEL_KEYS if isinstance(data.get(k), str) and data[k].strip()}


def check_model(url: str, model: str) -> tuple[str, str]:
    """检查托盘里填的地址和模型名，交回去掉首尾空格的两项；不合格抛 ValueError。"""
    url, model = url.strip(), model.strip()
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.query:
        raise ValueError('接口地址要以 http:// 或 https:// 开头，不能带账号、密钥或参数')
    if not model:
        raise ValueError('请填写模型名')
    return url, model


def save_user_config(url: str, model: str) -> None:
    """保存托盘里设的模型。回复和读屏共用这一个模型（和 config.json 现在的配法一致）。"""
    url, model = check_model(url, model)
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'llm_url': url, 'llm_model': model, 'vlm_url': url, 'vlm_model': model},
                               ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    """读模型地址配置（agent/config.json）；托盘里设过模型，就用设的地址和模型名盖掉默认值。"""
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))  # -sig：兼容记事本存的带 BOM 文件
    if Path(path) == DEFAULT_CONFIG:
        config.update(load_user_config())
    return config


def auth_headers(url: str) -> dict:
    """只有发往托盘里设的那个地址才带密钥：换回内网 Spark 时，不能把云端的 key 发过去。"""
    if url != load_user_config().get('llm_url'):
        return {}
    try:
        from agent.tools.secrets import get_key
        key = get_key()
    except OSError:
        return {}
    return {'Authorization': 'Bearer ' + key} if key else {}


def image_url(image: Image.Image) -> str:
    """把图片编码成接口能收的 data URL。"""
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def chat(url: str, model: str, messages: list, max_tokens: int = 200, timeout: int = 120,
         key: str | None = None) -> str:
    """发一次对话请求，返回模型回复的文字。temperature=0，结果尽量稳定。

    key：托盘"测试连接"时传入还没保存的 key；平时为 None，按 auth_headers 取。
    """
    body = {
        'model': model, 'messages': messages, 'temperature': 0, 'max_tokens': max_tokens,
        'chat_template_kwargs': {'enable_thinking': False},  # 关掉 Qwen3 的思考，只要结论
    }
    headers = {'Content-Type': 'application/json'}
    if key is None:
        headers.update(auth_headers(url))
    elif key:
        headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(url.rstrip('/') + '/chat/completions', json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())['choices'][0]['message']['content']
