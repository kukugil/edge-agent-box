"""真机演示：底层拦截在微信里管不管用（反例清单第 18、31 条）。

在「文件传输助手」里做，全程不按回车、不点发送，什么都不会发出去：
  1. 读输入框，原来不是空的就停：那是你自己的草稿，不碰
  2. 默认方式往空框里粘一句：要成功（paste_text 粘完会自己读回，读回不对会报错）。
     这一步同时证明"全选+复制"在微信里读得到：第 1 步读到空，可能只是复制没成
  3. 用 append 放一段假草稿，读回逐字相等
  4. 默认方式粘另一句：要被拒绝，假草稿原样保留
  5. 默认方式粘同一句：不粘第二遍
  6. press('esc')：要被拒绝；之后不做任何修复，直接查微信还在不在前台
  最后（出错也会做）：框里只剩自己放的字时才清掉，再读回是空的
只打印对错，不打印聊天内容。比较一律逐字，不去空白。

用法（在项目根目录）：python -m agent.demos.demo_input_guards
运行期间别动鼠标键盘，大约 15 秒。
"""
import sys
import time

from agent.core import graph
from agent.core.trigger import is_front, open_chat, prepare
from agent.tools import InputRefused, paste_text, press, read_focused_text

CHAT = '文件传输助手'
DRAFT = 'D11假草稿-请忽略'
OTHER = '测试回复 D11'


def check(name, ok):
    print(f"  [{'通过' if ok else '失败'}] {name}")
    return ok


def refused(fn) -> bool:
    try:
        fn()
    except InputRefused:
        return True
    return False


def clear_own() -> None:
    """框里正好是自己放的字才清。别的情况（可能混了用户的字）一律不动。"""
    if read_focused_text() in (DRAFT, OTHER):
        press('ctrl+a')
        press('backspace')
        time.sleep(0.2)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    hwnd = graph.find_wechat()
    if not prepare(hwnd):
        sys.exit('微信没准备好，停')
    if open_chat(hwnd, CHAT) is None:
        sys.exit(f'没能打开{CHAT}，停')
    time.sleep(0.3)
    if read_focused_text() != '':
        sys.exit('输入框里原来就有字（可能是你的草稿），不碰，停')

    results = []
    try:
        results.append(check('2. 空框默认粘贴：成功，粘完读回逐字相等', not refused(lambda: paste_text(OTHER))))
        results.append(check('2. 再读一次：正好是这句', read_focused_text() == OTHER))
        clear_own()
        results.append(check('2. 清掉后读回是空的', read_focused_text() == ''))

        paste_text(DRAFT, mode='append')
        time.sleep(0.2)
        results.append(check('3. 放假草稿后读回逐字相等', read_focused_text() == DRAFT))

        results.append(check('4. 有草稿时粘另一句：被拒绝', refused(lambda: paste_text(OTHER))))
        results.append(check('4. 假草稿原样保留', read_focused_text() == DRAFT))

        results.append(check('5. 粘同一句：没报错', not refused(lambda: paste_text(DRAFT))))
        results.append(check('5. 没粘第二遍', read_focused_text() == DRAFT))

        results.append(check("6. press('esc')：被拒绝", refused(lambda: press('esc'))))
        results.append(check('6. 微信还在前台（没先修复就查）', is_front(hwnd)))
    finally:
        clear_own()
        results.append(check('最后：框里是空的', read_focused_text() == ''))

    print(f'\n{sum(results)}/{len(results)} 通过')
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
