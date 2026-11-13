"""演示：用固定发送流程往白名单会话发一句（D15 方案二）。会动键鼠，跑的时候别碰鼠标键盘。

  --dry      只写不发：确认会话 → 点输入框 → 读空 → 粘贴 → 读回 → 清空，不按回车。先跑这个
  --vision   用看图适配器（不走控件树），看兜底那条路
不加 --dry 就真发：发一句 "发送测试+时分秒"，然后核对消息列表多了这一条。

用法（在项目根目录）：
  python -m agent.demos.demo_send_once --chat 3 --dry
  python -m agent.demos.demo_send_once --chat 3
  python -m agent.demos.demo_send_once --chat 3 --vision
"""
import argparse
import sys
import time

from agent.core import graph, send
from agent.core.trigger import WHITELIST, prepare


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser()
    p.add_argument('--chat', type=int, required=True, help='白名单第几个会话')
    p.add_argument('--dry', action='store_true')
    p.add_argument('--vision', action='store_true')
    args = p.parse_args()
    title = WHITELIST[args.chat - 1]
    wins = graph._wechat_windows()
    if not wins:
        sys.exit('微信不在桌面上，先手动打开')
    hwnd = wins[0].hwnd
    if not prepare(hwnd):
        sys.exit('微信没准备好（拉不到前台或不响应）')
    a = send.pick_adapter(hwnd, args.vision)
    print(f'适配器：{a.kind}，会话：白名单#{args.chat}')
    msg = f'发送测试{time.strftime("%H%M%S")}'
    if not args.dry:
        t = time.time()
        result, why = send.deliver(a, {'to': title, 'msg': msg})
        print(f'结果：{result}，{why}，共 {time.time() - t:.1f} 秒')
        return
    # 只写不发：和 deliver 前半段一样，最后清空
    print('打开会话：', a.open(title))
    print('会话核对：', a.title_ok(title))
    print('焦点在输入框：', send._focused(a))
    draft = a.input_text()
    print('输入框原来：', '读不到' if draft is None else f'{len(draft)} 个字')
    if draft:
        sys.exit('输入框里有字，不动')
    a.write(msg)
    back = a.input_text()
    print('粘贴后读回：', '一字不差' if back == msg else repr(back))
    print('清空：', a.clear(), '；清空后读回：', repr(a.input_text()))


if __name__ == '__main__':
    main()
