"""离线测试：底层键鼠拦截（反例清单第 18、31 条）。不动真实键鼠、不碰剪贴板。

运行：python -m agent.demos.test_input_guards

做法：把 inputs 里真正发按键的 user32 和读写剪贴板的 auto 换成假的，
假输入框按收到的 ctrl+a / ctrl+c / ctrl+v 改自己的内容，最后检查"框里剩什么、发了哪些键"。
"""
import agent.tools.inputs as inputs
from agent.tools.inputs import InputRefused

VK_NAME = {v: k for k, v in inputs._VK.items()}


class FakeDesk:
    """一个假的输入框 + 假剪贴板 + 假键盘。只认 ctrl+a / ctrl+c / ctrl+v / ctrl+end / enter。"""

    def __init__(self, box: str = '', copy_works: bool = True):
        self.box = box
        self.clip = ''
        self.selected = False
        self.copy_works = copy_works      # False：模拟焦点不在可复制的地方，ctrl+c 什么都不做
        self.down = []                    # 当前按住的键
        self.sent = []                    # 依次按出去的组合键，例如 'ctrl+v'
        self.events = 0                   # 原始键盘事件总数，单独按下 ctrl 也算：证明"一个键都没发"用它

    # --- 假 user32 ---
    def SetCursorPos(self, x, y):         # 鼠标也算事件：证明"被拒绝时鼠标一下都没动"
        self.events += 1

    def mouse_event(self, *args):
        self.events += 1

    def keybd_event(self, vk, scan, flags, extra):
        self.events += 1
        name = VK_NAME[vk]
        if flags & 0x2:                   # 抬起
            self.down.remove(name)
            return
        self.down.append(name)
        if len(self.down) == 1 and name == 'ctrl':
            return
        combo = '+'.join(self.down)
        self.sent.append(combo)
        if combo == 'ctrl+a':
            self.selected = True
        elif combo == 'ctrl+c' and self.selected and self.copy_works and self.box:
            self.clip = self.box
        elif combo == 'ctrl+v':
            self.box = self.clip if self.selected else self.box + self.clip
            self.selected = False
        elif combo == 'ctrl+end':
            self.selected = False

    # --- 假 auto（剪贴板）---
    def SetClipboardText(self, t):
        self.clip = t

    def GetClipboardText(self):
        return self.clip


def run(name, desk, fn):
    real_user32, real_auto = inputs.user32, inputs.auto
    inputs.user32, inputs.auto = desk, desk
    real_sleep, inputs.time.sleep = inputs.time.sleep, lambda s: None
    try:
        return fn()
    finally:
        inputs.user32, inputs.auto, inputs.time.sleep = real_user32, real_auto, real_sleep


def case(name, ok, detail=''):
    print(f"  [{'通过' if ok else '失败'}] {name}{'：' + detail if detail and not ok else ''}")
    return ok


def raises(fn) -> str:
    try:
        fn()
    except InputRefused as e:
        return str(e)
    return ''


def main():
    results = []

    print('第 31 条：危险按键')
    for keys in ('esc', 'ESC', 'shift+esc', 'alt+f4'):
        d = FakeDesk()
        why = run(keys, d, lambda: raises(lambda: inputs.press(keys)))
        results.append(case(f"press('{keys}') 被拒绝，一个键都没发", bool(why) and d.events == 0, f'why={why!r} events={d.events}'))
    d = FakeDesk()
    run('enter', d, lambda: inputs.press('enter'))
    results.append(case("press('enter') 照常能按", d.sent == ['enter'], str(d.sent)))

    print('第 18 条：输入框里已有草稿')
    d = FakeDesk(box='你好我正在打')
    why = run('draft', d, lambda: raises(lambda: inputs.paste_text('测试回复 1')))
    results.append(case('有草稿：拒绝粘贴', bool(why), why))
    results.append(case('有草稿：草稿原样保留', d.box == '你好我正在打', repr(d.box)))
    results.append(case('有草稿：没发 ctrl+v', 'ctrl+v' not in d.sent, str(d.sent)))
    results.append(case('有草稿：最后取消了选中', d.selected is False))
    results.append(case('有草稿：报错里不带草稿内容', '你好' not in why, why))

    d = FakeDesk(box='')
    run('empty', d, lambda: inputs.paste_text('测试回复 1'))
    results.append(case('空输入框：照常粘进去', d.box == '测试回复 1', repr(d.box)))

    d = FakeDesk(box='测试回复 1')
    run('same', d, lambda: inputs.paste_text('测试回复 1'))
    results.append(case('框里正好是这句：不粘第二遍', d.box == '测试回复 1' and 'ctrl+v' not in d.sent, repr(d.box)))

    for blank in (' ', '\t', '\r\n', '　'):
        d = FakeDesk(box=blank)
        why = run('blank', d, lambda: raises(lambda: inputs.paste_text('测试回复 1')))
        results.append(case(f'只有空白的草稿 {blank!r}：也拒绝，框不变', bool(why) and d.box == blank, repr(d.box)))

    d = FakeDesk(box='测试回复 1 ')
    why = run('near', d, lambda: raises(lambda: inputs.paste_text('测试回复 1')))
    results.append(case('框里只多一个空格：不算同一句，拒绝', bool(why) and d.box == '测试回复 1 ', repr(d.box)))

    d = FakeDesk(box='你好我正在打', copy_works=False)
    why = run('draft-nocopy', d, lambda: raises(lambda: inputs.paste_text('测试回复 1')))
    results.append(case('有草稿但复制失败：粘完读回不对，拒绝', bool(why), why))
    results.append(case('有草稿但复制失败：没按回车', 'enter' not in d.sent, str(d.sent)))

    d = FakeDesk(box='', copy_works=False)
    why = run('nocopy', d, lambda: raises(lambda: inputs.paste_text('测试回复 1')))
    results.append(case('复制一直不成（读不到输入框）：不确定，拒绝', bool(why), why))

    d = FakeDesk(box='旧内容')
    run('replace', d, lambda: inputs.paste_text('新内容', mode='replace'))
    results.append(case("mode='replace'：覆盖", d.box == '新内容', repr(d.box)))

    d = FakeDesk(box='旧内容', copy_works=False)
    why = run('replace-nocopy', d, lambda: raises(lambda: inputs.paste_text('新内容', mode='replace')))
    results.append(case("mode='replace' 读不回：拒绝", bool(why), why))

    d = FakeDesk(box='张')
    run('append', d, lambda: inputs.paste_text('三', mode='append'))
    results.append(case("mode='append'：追加", d.box == '张三', repr(d.box)))

    print('第 32、33 条：点关闭、最小化')
    real_at, inputs._window_rect_at = inputs._window_rect_at, lambda x, y: (100, 50, 1060, 850)   # 假窗口 960×800
    try:
        for name, x, y, want in [('关闭（右上角）', 1045, 60, True), ('最小化（关闭左边 90px）', 955, 65, True),
                                 ('最大化窗口的关闭（y 在窗口上沿外 8px 内）', 1050, 45, True),
                                 ('标题栏左边，离按钮远', 300, 60, False), ('右上角往下 50px（聊天区顶上）', 1045, 100, False)]:
            d = FakeDesk()
            why = run(name, d, lambda: raises(lambda: inputs.click(x, y)))
            got = bool(why)
            results.append(case(f'click {name}：{"拒绝，鼠标没动" if want else "照常点"}', got == want and (d.events == 0) == want,
                                f'why={why!r} events={d.events}'))
    finally:
        inputs._window_rect_at = real_at

    try:
        inputs.paste_text('x', mode='clear')
        results.append(case('不认识的 mode 报错', False))
    except ValueError:
        results.append(case('不认识的 mode 报错', True))

    print(f'\n{sum(results)}/{len(results)} 通过')
    raise SystemExit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
