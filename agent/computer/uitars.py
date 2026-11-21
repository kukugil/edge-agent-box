"""按 UI-TARS Desktop 0.2.4 组装请求、解析动作和换算落点。"""

from dataclasses import dataclass
import math
import re


MAX_PIXELS = 16384 * 28 * 28
PROMPT = r"""You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='<|box_start|>(x1,y1)<|box_end|>')
left_double(start_box='<|box_start|>(x1,y1)<|box_end|>')
right_single(start_box='<|box_start|>(x1,y1)<|box_end|>')
drag(start_box='<|box_start|>(x1,y1)<|box_end|>', end_box='<|box_start|>(x3,y3)<|box_end|>')
hotkey(key='ctrl c') # Split keys with a space and use lowercase. Also, do not use more than 3 keys in one hotkey action.
type(content='xxx') # Use escape characters \', \", and \n in content part to ensure we can parse the content in normal python string format. If you want to submit your input, use \n at the end of content.
scroll(start_box='<|box_start|>(x1,y1)<|box_end|>', direction='down or up or right or left') # Show more information on the `direction` side.
wait() # Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.


## Note
- Use {language} in `Thought` part.
- Generate a well-defined and practical strategy in the `Thought` section, summarizing your next move and its objective.

"""


def instruction(task, history=(), language='zh'):
    """返回首条用户消息。history 是按时间排序的 (角色, 文本) 序列。"""
    lines = '\n'.join(f'{"human" if role == "human" else "assistant"}: {value}'
                      for role, value in history[-30:])
    prompt = PROMPT.format(language='Chinese' if language == 'zh' else 'English')
    return f'{prompt}## History Messages\n{lines}\n\n## User Instruction\n{task}'


def summary(prediction):
    """去掉回复的 Reflection 段，返回留给下一轮的文本。"""
    return re.sub(r'Reflection:[\s\S]*?(?=Action_Summary:|Action:|$)', '', prediction).strip()


def image_message(png_base64):
    """把一张已编码的 PNG 包成单条用户消息。"""
    return {'role': 'user', 'content': [{'type': 'image_url',
            'image_url': {'url': f'data:image/png;base64,{png_base64}'}}]}


def messages(first, turns):
    """把首条指令和本次会话转为消息列表，去掉最早的截图消息。"""
    to_drop = max(0, sum(item['role'] == 'user' for item in turns) - 5)
    result = [{'role': 'user', 'content': first}]
    for item in turns:
        if item['role'] == 'user' and to_drop:
            to_drop -= 1
        else:
            result.append(item)
    return result


def request_params(model, chat):
    """返回 V1.5 非流式 Chat Completions 的参数。"""
    return dict(model=model, messages=chat, temperature=0, top_p=0.7,
                max_tokens=65535, stream=False, seed=None, stop=None, frequency_penalty=None,
                presence_penalty=None, extra_body={'thinking': {'type': 'disabled'}})


@dataclass(frozen=True)
class Action:
    """一项解析结果，参数中的坐标仍在模型坐标系内。"""

    name: str
    params: dict
    thought: str = ''
    reflection: str = ''


ARGUMENT = re.compile(
    r'''\s*(\w+)\s*=\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|\([^()]*\)|\[[^\[\]]*\]|[^,'"()\[\]\n]+?)\s*(,|$)''',
    re.DOTALL,
)
ESCAPES = {'n': '\n', 'r': '\r', 't': '\t', "'": "'", '"': '"', '\\': '\\'}
ALIASES = {'point': 'start_box', 'start_point': 'start_box', 'end_point': 'end_box'}


def parse_call(raw):
    """解析一个调用并返回名称和参数。格式无效时返回空动作。"""
    call = re.fullmatch(r'(\w+)\((.*)\)', raw.strip(), re.DOTALL | re.ASCII)
    if not call:
        return '', {}
    name, rest = call.groups()
    params = {}
    while rest.strip():
        match = ARGUMENT.match(rest)
        if not match:
            return '', {}
        key, value, separator = match.groups()
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                return '', {}
            value = value[1:-1]
        key = ALIASES.get(key, key)
        if key == 'content':
            value = re.sub(r'\\([nrt\'"\\])', lambda m: ESCAPES[m[1]], value)
        else:
            value = value.strip()
        params[key] = value
        rest = rest[match.end():]
        if not separator:
            break
    return name, params


def parse_prediction(text):
    """返回按顺序排列的动作。模型无文本时报告解析失败。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('解析失败：模型未返回动作文本')
    text = text.strip().replace('\r\n', '\n')
    if re.match(r'\w+\(', text):
        name, params = parse_call(text)
        if name:
            return [Action(name, params)]
    # 优先识别独立行的动作头，避免把输入正文里的 Action: 当作动作头。
    header = re.search(r'(?m)^[ \t]*Action[:：]', text)
    if header is None and not re.match(r'\w+\(', text):
        header = re.search(r'Action[:：]', text)
    prefix = text[:header.start()] if header else ''
    action_text = text[header.end():] if header else text
    thought = reflection = ''
    if 'Thought:' in prefix:
        thought = prefix.partition('Thought:')[2].strip()
    elif 'Action_Summary:' in prefix:
        before, _, thought = prefix.partition('Action_Summary:')
        reflection = before.partition('Reflection:')[2].strip()
        thought = thought.strip()
    # 先尝试整段，可保留 type 正文里的真实空行。
    name, params = parse_call(action_text)
    if name:
        return [Action(name, params, thought, reflection)]
    return [Action(*parse_call(raw), thought, reflection)
            for raw in action_text.strip().split('\n\n')]


def smart_resize(height, width, max_pixels=MAX_PIXELS):
    """按 V1.5 的 28 像素图块规则返回模型尺寸 (宽, 高)。"""
    factor = 28
    min_pixels = 100 * factor * factor
    if min(height, width) <= 0 or max(height, width) / min(height, width) > 200:
        raise ValueError('截图尺寸或宽高比无效')
    w = max(factor, math.floor(width / factor + 0.5) * factor)
    h = max(factor, math.floor(height / factor + 0.5) * factor)
    if w * h > max_pixels:
        beta = math.sqrt(height * width / max_pixels)
        w, h = (math.floor(value / beta / factor) * factor for value in (width, height))
    elif w * h < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        w, h = (math.ceil(value * beta / factor) * factor for value in (width, height))
    if not w or not h:
        raise ValueError('像素上限无法容纳截图图块')
    return w, h


def screen_point(box, screen, image_size, pad_top=0):
    """用模型图块尺寸换算框中心，返回原屏落点或 None。"""
    clean = re.sub(r'<[^>]+>|[()\[\]]', '', box or '').strip()
    try:
        values = [float(value) for value in re.split(r'\s*,\s*|\s+', clean)]
    except ValueError:
        return None
    if len(values) not in (2, 4) or not all(map(math.isfinite, values)):
        return None
    if len(values) == 2:
        values *= 2
    x1, y1, x2, y2 = values
    width, height = screen
    model_w, model_h = smart_resize(image_size[1], image_size[0])
    return (math.floor((x1 + x2) / 2 * width / model_w + 0.5),
            math.floor((y1 + y2) / 2 * (height + pad_top) / model_h - pad_top + 0.5))


def hotkeys(value, supported):
    """生成 Windows 键序列，丢弃 pyautogui 不支持的键。"""
    aliases = {
        'return': 'enter', 'control': 'ctrl', 'leftcontrol': 'ctrlleft',
        'rightcontrol': 'ctrlright', 'leftshift': 'shiftleft', 'rightshift': 'shiftright',
        'leftalt': 'altleft', 'rightalt': 'altright', 'leftwin': 'winleft', 'rightwin': 'winright',
        'page down': 'pagedown', 'page up': 'pageup', 'meta': 'win', 'command': 'win',
        'cmd': 'win', 'comma': ',', 'arrowup': 'up', 'arrowdown': 'down',
        'arrowleft': 'left', 'arrowright': 'right', 'escape': 'esc',
        'backspace': 'backspace', 'spacebar': 'space',
    }
    value = re.sub(r'\bpage\s+(up|down)\b', r'page\1', value.lower())
    keys = (aliases.get(key, key) for key in re.split(r'[\s+]+', value))
    return [key for key in keys if key in supported]
