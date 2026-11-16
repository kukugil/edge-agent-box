"""把确认过身份的微信测试会话按联系人追加到本机文件。"""
import hashlib
import json
import msvcrt
import os
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile


class HandoffChanged(ValueError):
    """界面上的接管快照已被更新。"""


def handoff_path(root: Path, contact: str) -> Path:
    """按同一联系人身份返回持久的人工接管标记路径。"""
    return root / (hashlib.sha256(contact.encode('utf-8')).hexdigest() + '.handoff')


def message_keys(messages: list[dict]) -> list[str]:
    """保留消息顺序，用哈希识别处理过的批次。"""
    return [hashlib.sha256(json.dumps([m['who'], m['kind'], m['text']],
                                    ensure_ascii=False).encode('utf-8')).hexdigest()
            for m in messages]


@contextmanager
def _contact_lock(path: Path):
    """同一会话的历史写入与人工恢复串行执行。"""
    with path.open('a+b') as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        yield


def _read_rows(marker: Path) -> list[dict]:
    path = marker.with_suffix('.jsonl')
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def _batch(raw: str, rows: list[dict], loan_request):
    if raw:
        return json.loads(raw)
    start = next((i for i in range(len(rows) - 1, -1, -1)
                  if rows[i]['who'] == 'them' and loan_request([rows[i]])), None)
    return {'start': start, 'keys': message_keys(rows[:start + 1])} if start is not None else None


def handoff_details(marker: Path, loan_request) -> dict | None:
    """读当前接管状态与后续来信，供托盘窗口刷新。"""
    try:
        raw = marker.read_text(encoding='utf-8')
        modified = marker.stat()
    except FileNotFoundError:
        return None
    rows = _read_rows(marker)
    preview = _preview_bytes(marker)
    batch = _batch(raw, rows, loan_request)
    aligned = batch is not None and message_keys(rows[:len(batch['keys'])]) == batch['keys']
    captured = rows[batch.get('request', batch['start']):] if aligned else []
    incoming = [row for row in captured if row['who'] == 'them']
    request = incoming[0] if incoming else None
    source = batch.get('source', 'uia') if batch else 'uia'
    if source == 'ocr' and batch.get('request_text'):
        request = {'text': batch['request_text']}
    return {'stamp': (raw, modified.st_mtime_ns, message_keys(rows), _preview_key(preview)),
            'started': datetime.fromtimestamp(modified.st_mtime), 'aligned': aligned,
            'source': source, 'reason': batch.get('reason', '借钱请求') if batch else '借钱请求',
            'request': request, 'followups': incoming[1:], 'preview': preview}


def _preview_bytes(marker: Path) -> bytes:
    try:
        return marker.with_suffix('.png').read_bytes()
    except FileNotFoundError:
        return b''


def _preview_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest() if data else ''


def _save_handoff_preview(marker: Path, image) -> None:
    """只为活跃事项原子替换最近一张聊天区截图。"""
    if image is None or not marker.exists():
        return
    output = BytesIO()
    image.save(output, format='PNG')
    data = output.getvalue()
    if _preview_bytes(marker) == data:
        return
    pending = None
    try:
        with NamedTemporaryFile(mode='wb', dir=marker.parent, delete=False) as stream:
            pending = Path(stream.name)
            stream.write(data)
        pending.replace(marker.with_suffix('.png'))
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def _update_preview(marker: Path, image) -> None:
    try:
        _save_handoff_preview(marker, image)
    except OSError as exc:
        print(f'  聊天截图暂未保存（{type(exc).__name__}）')


def start_handoff(marker: Path, batch: dict, *, preview=None) -> None:
    """创建接管事项，和同步聊天、人工恢复共用会话锁。"""
    marker.parent.mkdir(parents=True, exist_ok=True)
    with _contact_lock(marker.with_suffix('.lock')):
        with marker.open('x', encoding='utf-8') as stream:
            json.dump(batch, stream, ensure_ascii=False)
        _update_preview(marker, preview)


def resume_handoff(marker: Path, loan_request, *, expected=None) -> None:
    """把活动接管转为已处理的批次，再解除暂停。"""
    if not marker.exists():
        return
    with _contact_lock(marker.with_suffix('.lock')):
        if marker.exists():
            _resume_handoff(marker, loan_request, expected)


def _resume_handoff(marker: Path, loan_request, expected) -> None:
    rows = _read_rows(marker)
    keys = message_keys(rows)
    raw = marker.read_text(encoding='utf-8')
    if expected is not None and (raw, marker.stat().st_mtime_ns, keys, _preview_key(_preview_bytes(marker))) != expected:
        raise HandoffChanged('接管事项已变化，请刷新后处理')
    batch = _batch(raw, rows, loan_request)
    if batch is not None:
        batch.pop('request_text', None)
    if batch is not None and keys[:len(batch['keys'])] == batch['keys']:
        batch['keys'] = keys
    if batch is not None:
        completed = marker.with_suffix('.handled.json')
        batches = json.loads(completed.read_text(encoding='utf-8')) if completed.exists() else []
        if batch not in batches:
            pending = None
            try:
                with NamedTemporaryFile(mode='w', encoding='utf-8', dir=marker.parent, delete=False) as stream:
                    pending = Path(stream.name)
                    json.dump([*batches, batch], stream)
                pending.replace(completed)
            finally:
                if pending is not None:
                    pending.unlink(missing_ok=True)
    marker.with_suffix('.png').unlink(missing_ok=True)
    marker.unlink()


def without_handled(marker: Path, messages: list[dict], followup) -> list[dict]:
    """排除已处理批次和紧接的借钱补充，保留其后的正常消息和新请求。"""
    completed = marker.with_suffix('.handled.json')
    if not completed.exists():
        return messages
    batches = json.loads(completed.read_text(encoding='utf-8'))
    keys = message_keys(messages)
    excluded = set()
    for batch in batches:
        known = batch['keys']
        match_followup = followup if batch.get('reason', '借钱请求') == '借钱请求' else None
        if known and keys[:len(known)] == known:
            stop = len(known)
            while match_followup and stop < len(messages) and match_followup(messages[stop]):
                stop += 1
            excluded.update(range(batch['start'], stop))
        elif batch.get('source') == 'ocr':
            # OCR 可能缺少存档里的早期消息，只比对当时接管的连续来信。
            captured = known[batch['start']:]
            if not captured:
                continue
            for begin in range(len(keys) - len(captured) + 1):
                end = begin + len(captured)
                if keys[begin:end] != captured:
                    continue
                while match_followup and end < len(messages) and match_followup(messages[end]):
                    end += 1
                excluded.update(range(begin, end))
                break
    return [m for i, m in enumerate(messages) if i not in excluded]


def _same(left: dict, right: dict) -> bool:
    """比较两次读到的消息。有明确时间冲突时，不能当成同一条。"""
    return (all(left[k] == right[k] for k in ('who', 'kind', 'text'))
            and (left['at'] is None or right['at'] is None or left['at'] == right['at']))


def _combined(saved: list[dict], current: list[dict]) -> tuple[list[dict], list[dict]]:
    """返回这次可用的上下文和能安全追加的消息。"""
    if not saved:
        return current, current
    if not current:
        return current, []

    for length in range(min(len(saved), len(current)), 0, -1):
        for start in range(len(current) - length + 1):
            end = start + length
            if all(_same(old, new) for old, new in zip(saved[-length:], current[start:end])):
                added = current[end:]
                return saved + added, added

    # 没有重叠时，只有明确的时间先后能证明这批是新的。也可能只是用户向上滚了。
    first_at, last_at = current[0]['at'], saved[-1]['at']
    if first_at is not None and last_at is not None and first_at > last_at:
        return saved + current, current
    # 能认出这是往上滚后看到的旧屏时，不让它被当成新来消息。
    for start in range(len(saved) - len(current) + 1):
        if all(_same(old, new) for old, new in zip(saved[start:], current)):
            return [], []
    return current, []


def sync(root: Path, contact: str, messages: list[dict], *, preview=None) -> list[dict]:
    """保存当前会话中能确认的新原文，并返回供回复用的消息列表。

    root 是本机记录目录，contact 是已核对的白名单会话身份。忽略时间分隔行和屏幕坐标。
    """
    key = hashlib.sha256(contact.encode('utf-8')).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{key}.jsonl'
    current = [{k: msg[k] for k in ('who', 'kind', 'text', 'at')}
               for msg in messages if msg['who'] in ('us', 'them')]
    # 同一联系人串行合并，临时文件写完整后替换。中断时原文件仍可读。
    with _contact_lock(root / f'{key}.lock'):
        original = path.read_text(encoding='utf-8') if path.exists() else ''
        saved = [json.loads(line) for line in original.splitlines()]
        for msg in saved:
            msg['at'] = datetime.fromisoformat(msg['at']) if msg['at'] else None
        context, added = _combined(saved, current)
        if added:
            pending = None
            try:
                with NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                        dir=root, delete=False) as stream:
                    pending = Path(stream.name)
                    stream.write(original)
                    for msg in added:
                        at = msg['at'].isoformat() if msg['at'] is not None else None
                        stream.write(json.dumps({**msg, 'at': at}, ensure_ascii=False) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                pending.replace(path)
            finally:
                if pending is not None:
                    pending.unlink(missing_ok=True)
        _update_preview(path.with_suffix('.handoff'), preview)
    return context
