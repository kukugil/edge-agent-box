"""演示：点击、按键、清空后粘贴、滚动。在一个新开的记事本上做，不碰微信。

每做一步，用控件树把记事本里的内容读回来核对（记事本的控件树是能读的），
这就是“做完要核对”：不信自己做了，信读回来的结果。

用法（在项目根目录）：python -m agent.demos.demo_inputs
运行期间别动鼠标键盘，大约 10 秒；结束时记事本会被直接关掉（不保存）。
"""
import subprocess
import sys
import time

import uiautomation as auto

from agent.tools import (activate, check_responsive, click, find_windows, paste_text, press, screen_changed,
                         screenshot, scroll, window_rect)


def read_notepad(hwnd):
    return auto.ControlFromHandle(hwnd).EditControl(searchDepth=1).GetValuePattern().Value


def check(name, ok, detail=''):
    print(f'  {"✓" if ok else "✗"} {name}  {detail}')
    return ok


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    proc = subprocess.Popen('notepad.exe')
    try:
        hwnd = None
        for _ in range(30):
            ws = [w for w in find_windows(class_prefix='Notepad', pid=proc.pid) if w.visible]
            if ws:
                hwnd = ws[0].hwnd
                break
            time.sleep(0.2)
        if not hwnd:
            sys.exit('没等到记事本窗口')
        print('找到记事本', hwnd)
        print('activate →', activate(hwnd))
        print('check_responsive →', check_responsive(hwnd))

        shot = screenshot(window_rect(hwnd))
        x, y = shot.to_screen(shot.image.width / 2, shot.image.height / 2)  # 编辑区中间
        results = []

        click(x, y)
        time.sleep(0.2)
        paste_text('第一段：你好，记事本')
        time.sleep(0.2)
        results.append(check('click + paste_text', read_notepad(hwnd) == '第一段：你好，记事本', repr(read_notepad(hwnd))))

        paste_text('第二段：覆盖掉第一段')  # clear=True：先全选再粘贴
        time.sleep(0.2)
        results.append(check('paste_text 清空后粘贴', read_notepad(hwnd) == '第二段：覆盖掉第一段', repr(read_notepad(hwnd))))

        paste_text('+追加', clear=False)
        time.sleep(0.2)
        results.append(check('paste_text(clear=False) 追加', read_notepad(hwnd) == '第二段：覆盖掉第一段+追加',
                             repr(read_notepad(hwnd))))

        press('ctrl+a')
        press('backspace')
        time.sleep(0.2)
        results.append(check("press('ctrl+a') + press('backspace') 清空", read_notepad(hwnd) == '', repr(read_notepad(hwnd))))

        paste_text('\r\n'.join(f'第{i}行' for i in range(1, 201)))  # 200 行，够滚
        press('ctrl+home')
        time.sleep(0.3)
        before = screenshot(window_rect(hwnd)).image
        scroll(x, y, 10)
        time.sleep(0.3)
        after = screenshot(window_rect(hwnd)).image
        results.append(check('scroll 向下 10 格，画面有变化', screen_changed(before, after)))

        try:
            press('ctrl+nosuchkey')
            results.append(check('press 不认识的键会报错', False))
        except ValueError as e:
            results.append(check('press 不认识的键会报错', True, str(e)))

        print(f'\n通过 {sum(results)}/{len(results)}')
    finally:
        proc.kill()  # 直接结束进程，不弹“是否保存”


if __name__ == '__main__':
    main()
