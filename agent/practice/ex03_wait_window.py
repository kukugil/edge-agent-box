"""练习 3：修好一个等窗口的函数。

wait_for_notepad 想做的事：最多等 timeout 秒，每隔 1 秒看一眼，记事本一出现就交回窗口句柄；到时间还没有就交回 None。
现在它有 4 处错，每一处都能跑，不一定报错，只是结果不对。讲解见 learning/lessons/0002-trace-the-path.html。

你要做的：用调试器一步步走，找到错的那行再改。只改 wait_for_notepad，其他地方没有错。

调试：VSCodium 左边“运行和调试”选“练习3”，按 F5。
运行（在项目根目录）：python -m agent.practice.ex03_wait_window
这个程序只看不动，运行时可以正常用电脑。
"""
import sys
import time

from agent.tools import find_windows

TIMEOUT = 15     # 最多等几秒
INTERVAL = 1.0   # 每隔几秒看一眼


def find_notepad() -> int | None:
    """找记事本窗口，返回窗口句柄；没有就返回 None。已写好，没有错。"""
    for w in find_windows(class_prefix='Notepad'):
        if w.visible:
            return w.hwnd
    return None


def wait_for_notepad(timeout: float) -> int | None:
    """最多等 timeout 秒。

    输出：记事本的窗口句柄；到时间还没出现就返回 None。
    """
    start = time.time()
    tries = 0
    while time.time() - start <= timeout:
        tries += 1
        print(f'第 {tries} 次看')
        hwnd = find_notepad()
        if hwnd:
            return hwnd
        time.sleep(INTERVAL)
    return None


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    print(f'最多等 {TIMEOUT} 秒')
    start = time.time()
    hwnd = wait_for_notepad(TIMEOUT)
    print(f'结果：{hwnd}，用了 {time.time() - start:.1f} 秒')


if __name__ == '__main__':
    main()
