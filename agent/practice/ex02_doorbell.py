"""练习 2：自己做一个门铃。

每隔 1 秒看一眼微信左边的总数字，数字变了就打印“叮！来消息了”。
这就是小助手的门铃：平时不叫大模型，只做这件很便宜的事；响了才叫醒后面的“看 → 想 → 做 → 检查”。
讲解见 docs/学习笔记/02-新消息门铃.md。

你要做的：把下面 2 个函数里的 TODO 填上（删掉 raise NotImplementedError）。其他地方已经写好，不用改。

运行（在项目根目录）：python -m agent.practice.ex02_doorbell
运行前把微信点开（可以被别的窗口挡住，但不要最小化）。
程序启动后，用手机往文件传输助手发 3 条消息，每条隔几秒。成功时打印 3 次“叮”，最后一行：门铃响了 3 次。
这个程序只看不动，运行时可以正常用电脑。
"""
import sys
import time

from agent.tools import capture_window, find_red_dots, find_windows, window_rect

NAV_W = 70       # 微信左边导航栏的宽度，只在这一条里找红点
INTERVAL = 1.0   # 每隔几秒看一眼
SECONDS = 60     # 一共盯多久


def find_wechat() -> int:
    """找微信主窗口，返回窗口句柄。已写好。"""
    for w in find_windows(title='微信', class_prefix='Qt'):
        if w.visible and w.minimized:
            sys.exit('微信是最小化的，请先点开（可以被别的窗口挡住）')
        l, t, r, b = window_rect(w.hwnd)
        if w.visible and r - l > 300:
            return w.hwnd
    sys.exit('没找到微信主窗口。藏在托盘的话，请先点托盘图标')


def look_at_total(hwnd: int) -> bytes | None:
    """看一眼左边“聊天”图标上的总数字。

    输出：红点那一小块图片的数据（bytes）；看不到红点就返回 None。
    为什么不认出数字是几：门铃只要知道“和上次长得一不一样”，直接比较图片就行，又快又不会认错。
    提示：
      1. capture_window(hwnd) 让微信交一张画面，返回 Shot；拿不到时返回 None，这时你也返回 None
      2. find_red_dots(shot.image, NAV_W) 找红点，返回一个列表，从上到下排；没有红点时是空列表，也返回 None
      3. 列表第一个就是“聊天”图标上的红点，它是一个框。shot.image.crop(框).tobytes() 就是这一小块的数据
    """
    shot = capture_window(hwnd)
    if not shot:
        return None
    dots = find_red_dots(shot.image,NAV_W)
    if not dots:
        return None
    return shot.image.crop(dots[0]).tobytes()

def watch(hwnd: int, seconds: float) -> int:
    """盯 seconds 秒，总数字每变一次就打印一行“叮！来消息了”。

    输出：门铃一共响了几次。
    提示：
      1. 先看一眼，把结果记在变量 last 里；再用 time.time() 记下开始时间
      2. 循环，直到过了 seconds 秒：
           等 INTERVAL 秒 → 再看一眼 → 和 last 不一样就打印、计数 → 更新 last
      3. 想一想：如果忘了更新 last，门铃会怎么响？
    """
    last1 = look_at_total(hwnd)
    starttime = time.time()
    loop = 0 
    while time.time()-starttime <= seconds:
        time.sleep(INTERVAL)
        last2 = look_at_total(hwnd)
        if last1 != last2:
            loop += 1
            print("叮！来消息了")
            last1 = last2
    return loop

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    hwnd = find_wechat()
    print(f'门铃开始工作，盯 {SECONDS} 秒。现在用手机往文件传输助手发 3 条消息，每条隔几秒。')
    rings = watch(hwnd, SECONDS)
    print(f'门铃响了 {rings} 次')


if __name__ == '__main__':
    main()
