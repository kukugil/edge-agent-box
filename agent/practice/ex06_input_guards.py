"""练习 6：底层拦截的两个判断。

不碰键鼠。真正的写法在 agent/tools/inputs.py（press 开头的 _FORBIDDEN 检查、paste_text 开头的草稿检查），
讲解见 docs/学习笔记/04-底层拦截.md。先读懂那两段，再关上文件自己写。

你要做的：把下面 2 个函数里的 TODO 填上（删掉 raise NotImplementedError）。其他地方已经写好，不用改。

运行（在项目根目录）：python -m agent.practice.ex06_input_guards
成功时最后一行打印：全部通过。
"""
import sys

FORBIDDEN = {('esc',): 'Esc 会把微信藏进托盘', ('alt', 'f4'): 'Alt+F4 会关掉窗口'}


def forbidden_reason(keys: str) -> str:
    """这个按键能不能按。

    输入：keys 例如 'enter'、'ctrl+v'、'Shift+Esc'、'alt+f4'
    输出：不许按就交回原因（FORBIDDEN 里的那句），许按就交回 ''
    规则：先按 '+' 拆开、去空格、转小写；FORBIDDEN 里某个组合的每个键都出现在里面，就不许按。
    想一想：为什么要转小写？为什么 'shift+esc' 也要拦？
    """
    # TODO
    names = {key.strip().lower() for key in keys.split('+')}
    for unkey,why in FORBIDDEN.items():
        if set(unkey) <= names:
            return why
    return ''

    


def decide(draft: str, text: str) -> str:
    """粘贴前看一眼输入框，决定怎么做。

    输入：draft 是输入框里原有的字（read_focused_text 读到的），text 是要粘贴的字
    输出：三种之一
      'paste'  ：框是空的（draft == ''），粘贴
      'skip'   ：框里正好是 text（逐字相等，不去空白），不粘第二遍
      'refuse' ：框里是别的，只有空格、换行也算，拒绝
    想一想：'skip' 和 'refuse' 的判断，哪个要写在前面？为什么？只有一个空格的框，为什么也要拒绝？
    """
    # TODO
    if draft == text:
        return 'skip'
    if draft == '':
        return 'paste'   
    else:
        return 'refuse'
    
# ---------- 下面已经写好 ----------

CASES_KEYS = [
    ('enter', ''), ('ctrl+v', ''), ('esc', 'Esc 会把微信藏进托盘'), ('ESC', 'Esc 会把微信藏进托盘'),
    ('shift + esc', 'Esc 会把微信藏进托盘'), ('alt+f4', 'Alt+F4 会关掉窗口'), ('f4', ''), ('alt', ''),
]
CASES_DRAFT = [
    ('', '测试回复 1', 'paste'), ('  \n', '测试回复 1', 'refuse'), ('测试回复 1', '测试回复 1', 'skip'),
    ('测试回复 1\n', '测试回复 1', 'refuse'), ('　', '测试回复 1', 'refuse'), ('你好', '测试回复 1', 'refuse'), ('测试回复', '测试回复 1', 'refuse'),
]


def main():
    bad = 0
    for keys, want in CASES_KEYS:
        got = forbidden_reason(keys)
        ok = got == want
        bad += not ok
        print(f"  [{'对' if ok else '错'}] forbidden_reason({keys!r}) → {got!r}{'' if ok else f'，应该是 {want!r}'}")
    for draft, text, want in CASES_DRAFT:
        got = decide(draft, text)
        ok = got == want
        bad += not ok
        print(f"  [{'对' if ok else '错'}] decide({draft!r}, {text!r}) → {got!r}{'' if ok else f'，应该是 {want!r}'}")
    print('全部通过' if bad == 0 else f'{bad} 个不对')
    sys.exit(0 if bad == 0 else 1)


if __name__ == '__main__':
    main()
