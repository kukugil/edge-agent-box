"""微信自动回复的正式启动入口，由托盘在子进程里启动，也可以单独运行。

做的事：把微信切到前台 → 打开白名单里的第一个会话 → 开文件传输助手模拟（白名单里有它时）→ 交给新流程监听。
停止：托盘创建 --stop-file 指定的文件，监听在两轮之间看到就停，正在发的那条会发完再停。
用法：python -m agent.core.listen [--hours 12] [--stop-file 路径]
"""
import argparse
import sys
import time
from pathlib import Path

from agent.core import graph, messages, trigger, wechat_reply_flow
from agent.tools import find_windows, norm
from agent.tools.wechat_uia import WeChatUIA

STARTUP_TRIES = 30      # 启动时找微信、打开会话最多试几次，每次间隔约 2 秒
SIM_TRIES = 5           # 窗口刚还原有动画，两帧标题不一致会开模拟失败，等稳后重试


def say(text):
    """输出一行给托盘看。flush：托盘按行读子进程输出，不刷新会攒着不显示。"""
    print(text, flush=True)


def contacts(whitelist, sim_name):
    """输入白名单，输出要监听的会话；名字重复或规范化后撞名时交回 None（会分不清发给谁）。"""
    if not whitelist or len({norm(name) for name in whitelist}) != len(whitelist):
        return None
    # 文件传输助手放最前：启动时先打开它，屏上看不到别人的聊天
    return sorted(whitelist, key=lambda name: name != sim_name)


def bring_up(first, stop, tries=STARTUP_TRIES, wait=2.0):
    """把微信切到前台并打开 first 会话。

    输入：first 要先打开的会话；stop() 为 True 时放弃
    输出：微信主窗口句柄；找不到或被停止时交回 None
    坑：藏到托盘的微信没有可见窗口，程序不碰（强行显示后可能不响应），要人点一下托盘图标。
    """
    for attempt in range(tries):
        if stop():
            return None
        found = [w for w in find_windows(title='微信', class_prefix='Qt') if w.visible]
        big = graph._wechat_windows()
        window = (big or found or [None])[0]
        if window and trigger.prepare(window.hwnd) and WeChatUIA(window.hwnd).open(first):
            time.sleep(1.5)       # 等右边换成这个会话
            if messages.read_chat(window.hwnd, first)['ok']:
                return window.hwnd
        if attempt == 0:
            say('还没找到可用的微信窗口：微信藏在托盘里时请点一下微信的托盘图标')
        time.sleep(wait)
    return None


def start_simulation(hwnd, stop):
    """开文件传输助手模拟，成功交回 True。"""
    for _ in range(SIM_TRIES):
        if stop():
            return False
        time.sleep(1)
        result = trigger.sim_start(hwnd)
        if trigger.SIM['on']:
            say(result)
            return True
    say(result)
    return False


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='启动微信自动回复监听')
    parser.add_argument('--hours', type=float, default=12, help='最长运行小时数')
    parser.add_argument('--stop-file', type=Path, help='这个文件出现时停止监听')
    args = parser.parse_args(argv)
    stop = (lambda: args.stop_file.exists()) if args.stop_file else (lambda: False)

    titles = contacts(list(trigger.WHITELIST), trigger.SIM_NAME)
    if titles is None:
        say('已停止：白名单为空或有重名，分不清发给谁')
        return 2
    say(f'状态：正在启动，接入 {len(titles)} 个会话')
    hwnd = bring_up(titles[0], stop)
    if hwnd is None:
        say('已停止' if stop() else '已停止：没能把微信切到前台并打开会话')
        return 0 if stop() else 2
    say('已把微信切到前台并打开会话')
    if trigger.SIM_NAME in titles and not start_simulation(hwnd, stop):
        say('已停止' if stop() else '已停止：文件传输助手模拟没开成')
        return 0 if stop() else 2
    say('状态：监听中')
    try:
        wechat_reply_flow.run(hwnd, args.hours * 3600, titles, mode='send', stop=stop)
    except RuntimeError as exc:
        say(f'已停止：{exc}')
        return 2
    except OSError as exc:
        # 最常见的是 listener_lock 拿不到锁：同一个微信号已经有一个监听在跑，两个一起会抢着回
        say(f'已停止：{type(exc).__name__}（可能已经有一个监听在运行）')
        return 2
    say('已停止')
    return 0


if __name__ == '__main__':
    sys.exit(main())
