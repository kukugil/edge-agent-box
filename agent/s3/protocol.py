"""S3 进程事件、动作说明和截图坐标换算。"""

import ast
import io
import json
import re
import tokenize


class EventLines:
    """把任意边界切分的字节流读成逐行 JSON 事件。"""

    def __init__(self):
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
                if type(n) is int and 1 <= n <= 15:
                    yield event
            elif kind == 'start' or (kind == 'done' and event.get('result') in
                                     ('DONE', 'FAIL', 'STEP_LIMIT', 'ERROR')):
                yield event


def click_coordinates(code):
    """从执行代码中返回第一个带字面量坐标的 pyautogui.click，或 None。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    calls = sorted((node for node in ast.walk(tree) if isinstance(node, ast.Call)),
                   key=lambda node: (node.lineno, node.col_offset))
    for call in calls:
        func = call.func
        if (isinstance(func, ast.Attribute) and func.attr == 'click'
                and isinstance(func.value, ast.Name) and func.value.id == 'pyautogui'
                and len(call.args) >= 2):
            try:
                xy = [ast.literal_eval(arg) for arg in call.args[:2]]
            except (ValueError, TypeError):
                continue
            if all(type(value) in (int, float) for value in xy):
                return xy
    return None


def describe_action(plan_code):
    """从计划代码的第一个 agent 调用取中文动作短描述。"""
    labels = {'click': '点击', 'type': '输入', 'hotkey': '快捷键', 'hold_and_press': '快捷键',
              'scroll': '滚动', 'drag_and_drop': '拖拽', 'open': '打开应用',
              'switch_applications': '切换应用', 'wait': '等待', 'next': '继续',
              'done': '完成', 'fail': '未完成', 'highlight_text_span': '选中文字'}
    try:
        tokens = [token.string for token in tokenize.generate_tokens(io.StringIO(plan_code).readline)
                  if token.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE)]
    except (tokenize.TokenError, IndentationError):
        return '操作'
    for a, b, name, end in zip(tokens, tokens[1:], tokens[2:], tokens[3:]):
        if (a, b, end) == ('agent', '.', '(') and re.fullmatch(r'\w+', name):
            return labels.get(name, '操作')
    return '操作'


def image_rect(image_size, area):
    """将图像等比放进可绘制区域，返回居中的 (左, 上, 宽, 高)。"""
    width, height = image_size
    x, y, available_width, available_height = area
    scale = min(available_width / width, available_height / height)
    drawn_width, drawn_height = width * scale, height * scale
    return (x + (available_width - drawn_width) / 2, y + (available_height - drawn_height) / 2,
            drawn_width, drawn_height)


def marker_position(click, screen_size, drawn_rect):
    """把原屏点击坐标映射到图片实际绘制区，越界则不画。"""
    if click is None:
        return None
    x, y = click
    width, height = screen_size
    if not (0 <= x < width and 0 <= y < height):
        return None
    left, top, drawn_width, drawn_height = drawn_rect
    return left + x * drawn_width / width, top + y * drawn_height / height
