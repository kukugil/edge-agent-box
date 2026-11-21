"""在 Agent-S 的解释器里运行单个任务，并向托盘报告每一步。"""

import argparse
import contextlib
import ctypes
from ctypes import wintypes
from datetime import datetime
import io
import json
import logging
import os
from pathlib import Path
import sys
import time
import traceback


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


def run_agent(agent, task, screen_size, shots, emit, screenshot, resample):
    """按原版 CLI 运行至多 15 步，返回模型状态或步数耗尽。"""
    from .protocol import click_coordinates

    width, height = screen_size
    scale = min(2400 / width, 2400 / height, 1)
    scaled_size = (int(width * scale), int(height * scale))
    obs = {}
    for n in range(1, 16):
        step_started = time.monotonic()
        image = screenshot()
        shot = shots / f'step-{n}.png'
        image.save(shot)
        buffered = io.BytesIO()
        image.resize(scaled_size, resample).save(buffered, format='PNG')
        emit('thinking', n=n, shot=str(shot), screen=screen_size)
        obs['screenshot'] = buffered.getvalue()
        info, code = agent.predict(instruction=task, observation=obs)
        exec_code = code[0]
        status = exec_code.strip().upper()
        step = dict(n=n, plan=info.get('plan', '')[:300], code=info.get('plan_code', ''),
                    click=click_coordinates(exec_code), shot=str(shot), screen=screen_size)
        if status in ('DONE', 'FAIL'):
            emit('executed', **step, seconds=time.monotonic() - step_started)
            return status
        if 'next' in exec_code.lower():
            emit('executed', **step, seconds=time.monotonic() - step_started)
            continue
        if 'wait' in exec_code.lower():
            emit('executing', **step)
            time.sleep(5)
            emit('executed', n=n, seconds=time.monotonic() - step_started)
            continue
        time.sleep(1.0)
        emit('executing', **step)
        execution_started = time.monotonic()
        exec(exec_code)
        emit('executed', n=n, seconds=time.monotonic() - execution_started)
        time.sleep(1.0)
    return 'STEP_LIMIT'


def run_task(task, emit):
    """读取模型参数并构造 Windows Agent S3，返回运行结果。"""
    config_path = Path(__file__).resolve().parents[1] / 'config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))['s3']
    sys.path.insert(0, config['agent_s_dir'])

    import pyautogui
    from PIL import Image
    from gui_agents.s3.agents.agent_s import AgentS3
    from gui_agents.s3.agents.grounding import OSWorldACI

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    shots = Path(os.environ['LOCALAPPDATA']) / 'edge-agent' / 's3-runs' / stamp
    shots.mkdir(parents=True)
    emit('start', task=task)
    size = tuple(pyautogui.size())
    if not os.getenv('DEEPSEEK_API_KEY'):
        raise ValueError('缺少 S3 模型凭据')
    generation = dict(engine_type=config['provider'], model=config['model'],
                      base_url=config['model_url'], api_key='', temperature=None)
    grounding = dict(engine_type=config['ground_provider'], model=config['ground_model'],
                     base_url=config['ground_url'], api_key=config['ground_api_key'],
                     grounding_width=config['grounding_width'],
                     grounding_height=config['grounding_height'])
    aci = OSWorldACI(env=None, platform='windows', engine_params_for_generation=generation,
                     engine_params_for_grounding=grounding, width=size[0], height=size[1])
    agent = AgentS3(generation, aci, platform='windows',
                    max_trajectory_length=8, enable_reflection=True)
    agent.reset()
    return run_agent(agent, task, size, shots, emit, pyautogui.screenshot, Image.LANCZOS)


def main():
    """运行一次任务，stdout 只写 JSON，其他输出交给 stderr 日志。"""
    started = time.monotonic()
    stdout = sys.stdout
    secret = os.getenv('DEEPSEEK_API_KEY', '')
    encoded_secret = json.dumps(secret)[1:-1]

    def emit(event, **fields):
        """向原 stdout 输出一行事件，并移除可能回显的凭据。"""
        line = json.dumps(dict(event=event, **fields), ensure_ascii=True)
        if encoded_secret:
            line = line.replace(encoded_secret, '[已隐藏]')
        print(line, file=stdout, flush=True)

    detail = ''
    try:
        job = join_job()
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--task', required=True)
        args = parser.parse_args()
        log_dir = Path(os.environ['LOCALAPPDATA']) / 'edge-agent' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)

        class CredentialFilter(logging.Formatter):
            def format(self, record):
                """格式化并移除日志中的凭据。"""
                message = super().format(record)
                return message.replace(secret, '[REDACTED]') if secret else message

        handler = logging.FileHandler(log_dir / f's3-worker-{datetime.now():%Y%m%d-%H%M%S-%f}.log',
                                      encoding='utf-8')
        handler.setFormatter(CredentialFilter('%(asctime)s %(levelname)s %(name)s %(message)s'))
        with contextlib.redirect_stdout(sys.stderr):
            logging.basicConfig(level=logging.INFO, handlers=[handler])
            result = run_task(args.task, emit)
    except Exception as exc:
        diagnostic = traceback.format_exc()
        print(diagnostic.replace(secret, '[已隐藏]') if secret else diagnostic,
              file=sys.stderr, end='')
        result, detail = 'ERROR', type(exc).__name__
    emit('done', result=result, detail=detail, seconds=time.monotonic() - started)
    return int(result == 'ERROR')


if __name__ == '__main__':
    raise SystemExit(main())
