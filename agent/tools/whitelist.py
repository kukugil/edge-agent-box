"""回复名单的读写和保存前检查。

名单文件仍是 agent/test_contacts.local.json（不进 git），监听只读其中的 allowed_chats。
从"通讯录管理"读到的候选好友缓存在 %LOCALAPPDATA%/edge-agent/contacts_cache.json：
里面是全部好友的名字，不能放项目目录。

改了名单要重新开始自动回复才生效：trigger/graph 在导入时读一次名单。
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from agent.tools.ocr import norm

CONTACTS = Path(__file__).resolve().parent.parent / 'test_contacts.local.json'
CACHE = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'edge-agent' / 'contacts_cache.json'
SIM_NAME = '文件传输助手'


def _write_atomic(path: Path, data: dict) -> None:
    """先写临时文件再替换，写到一半断电也不会留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_allowed(path: Path = CONTACTS) -> list[str]:
    """输出当前名单；文件不存在或坏了交回空列表（界面上再提示）。"""
    try:
        return list(json.loads(path.read_text(encoding='utf-8')).get('allowed_chats', []))
    except (OSError, ValueError):
        return []


def save_allowed(names: list[str], path: Path = CONTACTS) -> None:
    """只替换 allowed_chats，allowed_mail 和其他字段原样保留。"""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        data = {}
    data['allowed_chats'] = list(names)
    _write_atomic(path, data)


def load_cache(path: Path = CACHE) -> dict | None:
    """输出上次读到的 {'updated', 'expected', 'gaps', 'rows'}；没有就 None。"""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def save_cache(result: dict, path: Path = CACHE) -> dict:
    """把 contacts_manager.read_all 的结果加上时间存起来，交回存进去的内容。"""
    data = {'updated': datetime.now().strftime('%Y-%m-%d %H:%M'), **result}
    _write_atomic(path, data)
    return data


def check(names: list[str], rows: list[dict]) -> list[str]:
    """保存前检查，输出问题列表（空列表 = 可以保存）。

    - 名字不能为空，规范化后不能撞名（监听启动时也是这条，见 listen.contacts）。
    - 候选里有两个好友显示名相同（或规范化后相同）的，不许选：发送时分不清是谁。
    - 文件传输助手、或候选里没有的名字（手动输入）允许，但这里不担保它是好友；监听启动时会再核对会话能否唯一定位。
    """
    problems = []
    if any(not name.strip() for name in names):
        problems.append('名单里有空名字')
    keys = [norm(name) for name in names]
    if len(set(keys)) != len(keys):
        problems.append('名单里有重复的名字（去掉空格和标点后相同也算）')
    counts = {}
    for row in rows:
        if row.get('ok'):
            counts[norm(row['title'])] = counts.get(norm(row['title']), 0) + 1
    clash = [name for name in names if counts.get(norm(name), 0) > 1]
    if clash:
        problems.append(f'有 {len(clash)} 个名字在通讯录里不止一个人用，分不清是谁，请先在微信里给他们改不同的备注')
    if not names:
        problems.append('名单是空的，自动回复启动不了')
    return problems
