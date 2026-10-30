"""练习 1：打开记事本 → 粘贴一句话 → 截图 → 用 OCR 核对。

这是一个最小的“做 → 核对”：程序做完一件事，不信自己“做了”，而是重新截图、用 OCR 看屏幕上到底有没有。
讲解见 docs/学习笔记/01-工具层.md。

你要做的：把下面 4 个函数里的 TODO 填上（删掉 raise NotImplementedError）。main() 已经写好，不用改。
要用的工具都在 agent.tools 里，已经导入好了；每个函数的说明里写了提示。

运行（在项目根目录）：python -m agent.practice.ex01_notepad_ocr
成功时最后一行打印：核对通过。运行期间别动鼠标键盘。
"""
import subprocess
import sys
import time

from agent.tools import (Shot, activate, check_responsive, click, find_windows, norm, ocr, paste_text,
                         screenshot, window_rect)

SENTENCE = '今天学会了工具层'


def open_notepad() -> tuple[subprocess.Popen, int]:
    """启动记事本，等它的窗口出现。

    输出：(进程对象, 窗口句柄 hwnd)
    提示：
      - subprocess.Popen('notepad.exe') 启动，进程号是 proc.pid
      - 窗口不是一启动就有，要循环等：每 0.2 秒用 find_windows 找一次，最多等 6 秒
      - 记事本的窗口类名以 'Notepad' 开头；只要 visible 为 True 的
      - 等不到就 raise RuntimeError('没等到记事本窗口')
    """
    proc = subprocess.Popen('notepad.exe')
    for _ in range(30):
        lists = find_windows(class_prefix='Notepad',pid=proc.pid)
        lists2 = [x for x in lists if x.visible ]
        if lists2:
            return (proc,lists2[0].hwnd)
        time.sleep(0.2)
    raise RuntimeError('没等到记事本窗口')
    
def type_sentence(hwnd: int, text: str) -> None:
    """把 text 输入到记事本里。

    提示（顺序很重要，想想为什么）：
      1. activate(hwnd)，再 check_responsive(hwnd)；两个都返回 (是否成功, 原因)，不成功就 raise RuntimeError(原因)
      2. 先截一张图拿到 Shot，算出窗口正中间的屏幕坐标（截图内坐标 → 屏幕坐标用哪个方法？）
      3. click 那个点，让光标进到编辑区；等 0.2 秒
      4. paste_text(text)
    """
    ok,why = activate(hwnd)
    if not ok:
        raise RuntimeError(why)
    ok,why = check_responsive(hwnd) 
    if not ok:
        raise RuntimeError(why)
    shot = screenshot(window_rect(hwnd))
    sx,sy = shot.to_screen(shot.image.width/2,shot.image.height/2)
    click(sx,sy)
    time.sleep(0.2)
    paste_text(text)

def capture(hwnd: int) -> Shot:
    """截记事本窗口的图。

    提示：一行就够。窗口的屏幕区域用 window_rect(hwnd) 拿。
    """
    return screenshot(window_rect(hwnd))
def verify_by_ocr(shot: Shot, text: str) -> tuple[bool, list[str]]:
    """用 OCR 核对截图里有没有 text。

    输出：(有没有, OCR 读到的所有文字列表)。第二项是为了核对失败时能看到 OCR 到底读到了什么。
    提示：
      - ocr() 要的是图片，不是 Shot
      - OCR 常把标点认错，比较前两边都用 norm() 处理
      - “有”指：某一条 OCR 文字里包含 text
    """
    items = ocr(shot.image)
    texts = [item['text'] for item in items]
    for t in texts:
        if norm(text) in norm(t):
            return(True,texts)
    return (False,texts)
        

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    proc, hwnd = open_notepad()
    print('记事本窗口：', hwnd)
    try:
        type_sentence(hwnd, SENTENCE)
        time.sleep(0.5)  # 等界面画完再截图
        shot = capture(hwnd)
        print(f'截图尺寸 {shot.image.size}，窗口左上角在屏幕 ({shot.left},{shot.top})')
        ok, texts = verify_by_ocr(shot, SENTENCE)
        print('OCR 读到：', texts)
        print('核对通过' if ok else '核对失败：屏幕上没找到这句话')
    finally:
        proc.kill()  # 直接结束进程，不弹“是否保存”


if __name__ == '__main__':
    main()
