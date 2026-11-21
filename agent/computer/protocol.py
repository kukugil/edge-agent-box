"""解析电脑助手的 JSON 行事件。"""

import json


class EventLines:
    """把任意边界切分的字节流读成逐行 JSON 事件。"""

    def __init__(self, max_loop):
        """记录本轮允许的最大步数和未收齐的字节。"""
        self.max_loop = max_loop
        self.pending = b''

    def feed(self, data=b'', *, final=False):
        """追加字节并产出有效事件。final 时也处理最后一行。"""
        lines = (self.pending + data).split(b'\n')
        self.pending = b'' if final else lines.pop()
        for raw in lines:
            try:
                event = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                # 只接收事件，依赖库的杂项输出和断在半行的事件不能驱动界面。
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get('event')
            if kind in ('thinking', 'executing', 'executed'):
                n = event.get('n')
                if type(n) is int and 1 <= n <= self.max_loop:
                    yield event
            elif kind == 'start' or (kind == 'done' and event.get('result') in
                                     ('DONE', 'CALL_USER', 'STEP_LIMIT', 'ERROR', 'STOPPED')):
                yield event
