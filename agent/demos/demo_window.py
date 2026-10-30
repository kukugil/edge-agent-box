"""演示：找窗口、激活、确认能响应。

第一部分：开一个可控的测试窗口（_testwin.py），人为制造 正常 / 最小化 / 隐藏 / 卡死 / 不响应输入
         五种状态，看 activate() 和 check_responsive() 能不能分辨出来。
第二部分：真实微信，只看不动——激活、悬停测试、截图、OCR、UI-TARS 定位搜索框（只算坐标，不点）。
         微信藏在托盘时不强行显示，只报告。只打印坐标，不存截图（真实账号的聊天内容不落盘）。

用法（在项目根目录）：python -m agent.demos.demo_window
运行期间别动鼠标键盘，大约 30 秒。
"""
import subprocess
import sys
import time
from pathlib import Path

from agent.tools import (activate, check_responsive, find_windows, ground, load_config, ocr, screenshot,
                         snap_to_text, window_rect)

HERE = Path(__file__).parent
results = []


def expect(name, got, want_ok):
    ok = got[0] == want_ok
    results.append(ok)
    print(f'  {"✓" if ok else "✗"} {name}：{got}')


def hover_points(hwnd):
    l, t, r, b = window_rect(hwnd)
    return [((r - l) // 2, int((b - t) * 0.3)), ((r - l) // 2, int((b - t) * 0.5))]


def test_window_part():
    print('== 第一部分：测试窗口')
    proc = subprocess.Popen([sys.executable, str(HERE / '_testwin.py')], stdin=subprocess.PIPE, text=True)

    def send(cmd):
        proc.stdin.write(cmd + '\n')
        proc.stdin.flush()
        time.sleep(0.6)

    try:
        hwnd = None
        for _ in range(30):
            ws = find_windows(title='agent-test-window', pid=proc.pid)
            if ws and ws[0].visible:
                hwnd = ws[0].hwnd
                break
            time.sleep(0.2)
        if not hwnd:
            sys.exit('没等到测试窗口')

        print('[正常]')
        expect('activate', activate(hwnd), True)
        expect('check_responsive（悬停）', check_responsive(hwnd, hover_points(hwnd)), True)

        print('[最小化]')
        send('min')
        expect('最小化时 check_responsive', check_responsive(hwnd), False)
        expect('activate 会还原', activate(hwnd), True)
        expect('还原后 check_responsive（悬停）', check_responsive(hwnd, hover_points(hwnd)), True)

        print('[隐藏，模拟藏到托盘]')
        send('hide')
        print('  find_windows 仍能找到它：', [(w.title, '可见' if w.visible else '隐藏') for w in find_windows(pid=proc.pid, title='agent-test-window')])
        expect('activate 拒绝强行显示', activate(hwnd), False)
        expect('check_responsive', check_responsive(hwnd), False)
        send('show')
        activate(hwnd)

        print('[卡死 4 秒]')
        send('hang')
        expect('卡住时 check_responsive', check_responsive(hwnd, timeout_ms=1000), False)
        time.sleep(4)
        activate(hwnd)
        expect('恢复后 check_responsive（悬停）', check_responsive(hwnd, hover_points(hwnd)), True)

        print('[不响应输入：程序活着、在前台，但鼠标移上去没反应]')
        send('freeze')
        activate(hwnd)
        expect('不做悬停测试时', check_responsive(hwnd), True)  # 前三级查不出来，这正是要悬停测试的原因
        expect('做悬停测试时', check_responsive(hwnd, hover_points(hwnd)), False)
    finally:
        try:
            send('quit')
        except OSError:
            pass
        proc.kill()


def wechat_part():
    print('\n== 第二部分：真实微信（只看不动）')
    ws = [w for w in find_windows(title='微信', class_prefix='Qt')]
    print('  找到的微信窗口：', [(w.hwnd, '可见' if w.visible else '隐藏', '最小化' if w.minimized else '') for w in ws])
    if not any(w.visible for w in ws):
        print('  没有可见的微信主窗口（可能在托盘里），不强行显示，跳过')
        return
    hwnd = next(w for w in ws if w.visible).hwnd
    print('  activate →', activate(hwnd))  # 最小化的会被还原
    # 微信左侧会话列表：每行约 64px 高，取第 2、3 行（截图内坐标）
    print('  check_responsive（悬停会话列表）→', check_responsive(hwnd, [(160, 170), (160, 240)]))

    shot = screenshot(window_rect(hwnd))
    items = ocr(shot.image)
    print(f'  截图 {shot.image.size}，左上角在屏幕 ({shot.left},{shot.top})；OCR {len(items)} 条')
    cfg = load_config()
    target = '左上角写着“搜索”的搜索框'
    pt, raw = ground(shot.image, target, cfg['grounder_url'], cfg['grounder_model'], cfg['grounder_max_pixels'])
    if pt is None:
        print('  定位失败：', raw)
        return
    snapped, note = snap_to_text(items, target, pt)
    print(f'  定位“{target}” → 截图内 ({pt[0]:.0f},{pt[1]:.0f})，校正后 ({snapped[0]:.0f},{snapped[1]:.0f}) {note}')
    print(f'  对应屏幕坐标 {shot.to_screen(*snapped)}（没有点击）')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    test_window_part()
    print(f'\n测试窗口部分通过 {sum(results)}/{len(results)}')
    wechat_part()


if __name__ == '__main__':
    main()
