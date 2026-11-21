"""用已核对的模型配置启动原版 Agent S3 命令行。"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess


def load_config():
    """从本工作树读取 S3 配置并返回字典。"""
    return json.loads(Path(__file__).resolve().parents[1].joinpath('config.json')
                      .read_text(encoding='utf-8'))['s3']


def validated_key(config):
    """核对凭据对应的模型配置，再返回 Windows 凭据中的 key。"""
    from agent.tools.secrets import get_key

    path = Path(os.environ['LOCALAPPDATA']) / 'edge-agent' / 'assistant' / 'profiles.json'
    profiles = json.loads(path.read_text(encoding='utf-8'))
    profile_id = config['credential'].split('/', 1)[1]
    matches = [profile for profile in profiles if profile.get('id') == profile_id]
    expected = ('deepseek', 'deepseek-flash', 'https://api.deepseek.com')
    if (config['provider'], config['model'], config['model_url']) != expected or (
        len(matches) != 1 or (matches[0].get('model'), matches[0].get('base_url')) != expected[1:]
    ):
        raise ValueError('S3 模型、地址或凭据对应的本机配置不符')
    key = get_key(config['credential'])
    if not key:
        raise ValueError('未找到 S3 的 Windows 凭据')
    return key


def minimize_wechat():
    """找到当前可见的微信主窗口，确认能响应后用 Win32 最小化。"""
    from ctypes import wintypes
    from agent.tools.window import check_responsive, find_windows, window_rect

    for window in find_windows(title='微信', class_prefix='Qt'):
        if not window.visible or window.minimized:
            continue
        left, _, right, _ = window_rect(window.hwnd)
        if right - left <= 300:
            continue
        ok, reason = check_responsive(window.hwnd)
        if not ok:
            raise RuntimeError(reason)
        # ShowWindow 返回的是原可见状态，IsIconic 才能核对最小化结果。
        ctypes.windll.user32.ShowWindow(wintypes.HWND(window.hwnd), 6)  # SW_MINIMIZE
        if not ctypes.windll.user32.IsIconic(wintypes.HWND(window.hwnd)):
            raise RuntimeError('微信窗口最小化失败')
        return


def restore_light_theme():
    """把当前用户的应用和系统主题注册表值都改回浅色。"""
    import winreg

    path = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        for name in ('AppsUseLightTheme', 'SystemUsesLightTheme'):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)


def main():
    """解析任务和桌面准备选项，返回原版 CLI 的退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task')
    parser.add_argument('--minimize-wechat', action='store_true')
    parser.add_argument('--theme-light', action='store_true')
    args = parser.parse_args()
    if not (args.task or args.minimize_wechat or args.theme_light):
        parser.error('请指定 --task、--minimize-wechat 或 --theme-light')
    if args.task is not None and not args.task.strip():
        parser.error('--task 不能为空')

    if args.task:
        config = load_config()
        env = dict(os.environ, DEEPSEEK_API_KEY=validated_key(config),
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    if args.minimize_wechat:
        minimize_wechat()
    if args.theme_light:
        restore_light_theme()
    if not args.task:
        return 0

    command = [str(Path(config['python']).with_name('agent_s.exe')), '--task', args.task]
    for name in ('provider', 'model', 'model_url', 'ground_provider', 'ground_model',
                 'ground_url', 'ground_api_key', 'grounding_width', 'grounding_height'):
        command.extend((f'--{name}', str(config[name])))
    return subprocess.run(command, cwd=config['agent_s_dir'], env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
