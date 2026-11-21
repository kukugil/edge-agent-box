"""在独立解释器中截图、请求 UI-TARS、执行动作并发送 JSON 行。"""

import argparse
import base64
import contextlib
import ctypes
from ctypes import wintypes
import io
import json
import math
import os
from pathlib import Path
import sys
import time

if __package__:
    from . import uitars
else:
    import uitars


def join_job():
    """创建并加入终止时回收子进程的 Windows Job，返回其句柄。"""
    class BasicLimits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                    ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                    ('max_working_set', ctypes.c_size_t), ('process_limit', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                    ('scheduling', wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', BasicLimits), ('io_counters', ctypes.c_uint64 * 6),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    job = api.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not (api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            and api.AssignProcessToJobObject(job, api.GetCurrentProcess())):
        error = ctypes.get_last_error()
        api.CloseHandle(job)
        raise ctypes.WinError(error)
    # worker 本身也在 job 中，提前关闭最后一个句柄会在输出 done 前杀掉自己。
    return job


class Stopped(Exception):
    """停止文件已出现，调用方可以收尾退出。"""


def check_stop(stop_file):
    """发现本次任务的停止标志时中断当前流程。"""
    if stop_file.exists():
        raise Stopped


def pause(seconds, check):
    """等指定秒数，每 50ms 检查一次停止标志。"""
    end = time.monotonic() + seconds
    while True:
        check()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.05))


def model_image(image, pad_top, max_pixels):
    """返回发给模型的 PNG 消息和图片尺寸，原图留给回放。"""
    if pad_top:
        from PIL import ImageOps
        image = ImageOps.expand(image, border=(0, pad_top, 0, 0), fill='white')
    width, height = image.size
    if width * height > max_pixels:
        from PIL import Image
        scale = math.sqrt(max_pixels / (width * height))
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return uitars.image_message(base64.b64encode(buffer.getvalue()).decode('ascii')), image.size


def release(gui, method, *args, **kwargs):
    """执行一次松开操作。鼠标触及角落时也要清理已经按住的键。"""
    failsafe = gui.FAILSAFE
    try:
        gui.FAILSAFE = False
        method(*args, **kwargs, _pause=False)
    finally:
        gui.FAILSAFE = failsafe


@contextlib.contextmanager
def held(gui, keys):
    """按顺序按住指定键，结束或异常时逆序尝试释放全部键。"""
    with contextlib.ExitStack() as releases:
        for key in keys:
            # 按下失败也可能已提交输入，先登记释放。
            releases.callback(release, gui, gui.keyUp, key)
            gui.keyDown(key, _pause=False)
        yield


def execute(action, point, end, gui, clipboard, wait, check, scroll_clicks):
    """执行一个已解析动作，返回实际使用的起点或 None。"""
    name, params = action.name, action.params
    clicks = {'click': ('left', 1), 'left_click': ('left', 1), 'left_single': ('left', 1),
              'left_double': ('left', 2), 'double_click': ('left', 2),
              'right_single': ('right', 1), 'right_click': ('right', 1)}
    if name in clicks and point is not None:
        gui.moveTo(*point, _pause=False)
        wait(0.1)
        button, count = clicks[name]
        gui.click(button=button, clicks=count, _pause=False)
        return point
    if name in ('drag', 'left_click_drag', 'select') and point is not None and end is not None:
        gui.moveTo(*point, _pause=False)
        wait(0.1)
        # 先登记释放，拖动异常时也尝试松开左键。
        with contextlib.ExitStack() as releases:
            releases.callback(release, gui, gui.mouseUp, button='left')
            gui.mouseDown(button='left', _pause=False)
            gui.moveTo(*end, duration=max(0.11, math.dist(point, end) / 3600), _pause=False)
        return point
    if name == 'scroll':
        direction = params.get('direction', '').lower()
        if direction in ('up', 'down'):
            if point is not None:
                gui.moveTo(*point, _pause=False)
            gui.scroll(scroll_clicks if direction == 'up' else -scroll_clicks, _pause=False)
            return point
    elif name == 'wait':
        wait(5)
    elif name == 'hotkey':
        keys = uitars.hotkeys(params.get('key') or params.get('hotkey', ''), gui.KEYBOARD_KEYS)
        with held(gui, keys):
            pass
    elif name == 'type' and (content := params.get('content', '')):
        original = clipboard.paste()
        submit = content.endswith('\n')
        try:
            clipboard.copy(content[:-1] if submit else content)
            with held(gui, ('ctrl', 'v')):
                wait(0.05)
            wait(0.05)
        finally:
            clipboard.copy(original)
        if submit:
            check()
            with held(gui, ('enter',)):
                pass
    return None


def describe(action):
    """把模型动作和参数压成一行，用于步骤回看。"""
    args = ' '.join(f'{key}={value}' for key, value in action.params.items())
    return f'{action.name or "空动作"} {" ".join(args.split())}'.strip()[:120]


def request_prediction(create, config, chat, wait, check, errors):
    """请求模型。连接错误可重试两次，其他 API 错误直接报出。"""
    timeout_error, connection_error, status_error = errors
    params = uitars.request_params(config['model'], chat)
    if not config.get('thinking', True):
        del params['extra_body']
    for attempt in range(3):
        check()
        try:
            result = create(**params)
            return result.choices[0].message.content if result.choices else None
        except timeout_error as exc:
            raise RuntimeError('Spark 请求超时（30 秒）') from exc
        except connection_error as exc:
            if attempt == 2:
                raise RuntimeError('Spark 连接中断') from exc
            wait(3)
        except status_error as exc:
            raise RuntimeError(f'Spark HTTP 错误（{exc.status_code}）') from exc


def run_loop(task, config, shots, capture, predict, perform, emit, check, wait):
    """运行截图轮次，通过回调执行动作和发送事件，返回 (结果, 说明)。"""
    first = uitars.instruction(task, language=config['language'])
    turns = []
    pad_top = config['pad_top']
    for n in range(1, config['max_loop'] + 1):
        check()
        started = time.monotonic()
        image, screen = capture()
        shot = shots / f'{n:02d}.png'
        image.save(shot, format='PNG')
        frame, image_size = model_image(image, pad_top, config['max_pixels'])
        turns = uitars.messages(first, [*turns, frame])[1:]
        fields = dict(n=n, thought='', action='', shot=str(shot), screen=screen)
        emit('thinking', **fields)
        labels = []
        result = None
        try:
            prediction = predict(uitars.messages(first, turns))
            check()
            actions = uitars.parse_prediction(prediction)
            turns.append({'role': 'assistant', 'content': uitars.summary(prediction)})
            fields['thought'] = actions[0].thought
            for action in actions:
                check()
                labels.append(describe(action))
                fields['action'] = ' / '.join(labels)
                emit('executing', **fields)
                if action.name in ('finished', 'call_user'):
                    result = ('DONE', '') if action.name == 'finished' else ('CALL_USER', action.thought)
                    break
                if action.name == 'error_env':
                    raise RuntimeError('执行异常：模型报告环境错误')
                point = uitars.screen_point(action.params.get('start_box'), screen, image_size, pad_top)
                end = uitars.screen_point(action.params.get('end_box'), screen, image_size, pad_top)
                try:
                    actual = perform(action, point, end)
                except Stopped:
                    raise
                except Exception as exc:
                    raise RuntimeError(f'执行异常（{action.name}，{type(exc).__name__}）') from exc
                if actual is not None:
                    fields['point'] = actual
                check()
        finally:
            emit('executed', seconds=time.monotonic() - started, **fields)
        if result is not None:
            return result
        wait(config['loop_interval_ms'] / 1000)
    return 'STEP_LIMIT', ''


def run_task(task, config, shots, stop_file, emit):
    """绑定真实截图、模型连接和键鼠，再返回本次任务结果。"""
    import openai
    import pyautogui
    import pyperclip

    def check():
        """检查本次任务的停止文件。"""
        check_stop(stop_file)

    def wait(seconds):
        """让执行层和模型重试都能在等待期间响应停止。"""
        pause(seconds, check)

    def capture():
        """截图并返回图片和主屏逻辑尺寸。"""
        return pyautogui.screenshot(), tuple(pyautogui.size())

    errors = openai.APITimeoutError, openai.APIConnectionError, openai.APIStatusError
    with openai.OpenAI(base_url=config['base_url'], api_key=config['api_key'],
                      timeout=30, max_retries=0) as client:
        def predict(chat):
            """发送本轮消息并返回模型文本。"""
            return request_prediction(client.chat.completions.create, config, chat, wait, check, errors)

        def perform(action, point, end):
            """执行给定动作，返回实际使用的落点。"""
            return execute(action, point, end, pyautogui, pyperclip, wait, check, config['scroll_clicks'])

        return run_loop(task, config, shots, capture, predict, perform, emit, check, wait)


def main():
    """从命令行读取任务，从标准输入读取 computer 配置，输出 JSON 行。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    output = sys.stdout

    def emit(event, **fields):
        """将一个业务事件写到原始标准输出。"""
        print(json.dumps(dict(event=event, **fields), ensure_ascii=False), file=output, flush=True)

    try:
        config = json.load(sys.stdin)
        # 保持句柄打开至进程退出，让 Windows 在退出时回收同一 Job 的子进程。
        job = join_job() if os.name == 'nt' else None
        emit('start')
        with contextlib.redirect_stdout(sys.stderr):
            result, detail = run_task(args.task, config, args.run_dir, args.run_dir / 'stop', emit)
    except Stopped:
        result, detail = 'STOPPED', '已停止'
    except (RuntimeError, ValueError) as exc:
        result, detail = 'ERROR', str(exc)
    except Exception as exc:
        result, detail = 'ERROR', f'运行异常（{type(exc).__name__}）'
        print(detail, file=sys.stderr)
    emit('done', result=result, detail=detail)


if __name__ == '__main__':
    main()
