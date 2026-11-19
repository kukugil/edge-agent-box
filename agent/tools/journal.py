"""发送前落盘，重启后保守隔离未完成任务；不记录收件人和消息原文。"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def append(path, rec):
    """输入路径和五字段记录，追加 JSON 并同步磁盘；输出无，写入失败向上报告。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = {key: rec[key] for key in ('time', 'id', 'who', 'hash', 'status')}
    with target.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(fields, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def record(task, status):
    """输入任务和状态，按可选 journal 写脱敏记录；没有路径时不写，输出无。"""
    if task.get('audit') is not None:
        task['audit'](status)
    if 'journal' in task:
        append(task['journal'], {'time': datetime.now(timezone.utc).isoformat(),
               'id': task['id'], 'who': task['who'],
               'hash': hashlib.sha1(task['msg'].encode('utf-8')).hexdigest()[:8], 'status': status})


def unresolved(path) -> set[int]:
    """输入账本路径，输出仍有未决任务的白名单序号；缺文件交回空集合。"""
    target = Path(path)
    if not target.exists():
        return set()
    latest = {}
    for line in target.read_text(encoding='utf-8').splitlines():
        rec = json.loads(line)
        latest[(rec['who'], rec['id'])] = rec['status']
    return {who for (who, _), status in latest.items() if status not in ('ok', 'fail')}


def clear(path):
    """输入账本路径，人工确认后清空记录；输出无，不操作应用。"""
    target = Path(path)
    if target.exists():
        target.write_text('', encoding='utf-8')
