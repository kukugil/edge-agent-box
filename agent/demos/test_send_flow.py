"""离线测固定发送流程 deliver（D15 方案二）：假适配器，不碰微信、不动键鼠。

每条用例是一种"情况不对"，看流程停在哪、有没有按回车、框里留没留字。

运行（在项目根目录）：
  python -m agent.demos.test_send_flow
"""
import sys
from unittest.mock import patch

from agent.core import send


class Fake:
    """假适配器：一个输入框 + 一个"我方消息列表"，各种毛病用参数打开。"""
    kind = 'fake'

    def __init__(self, title_ok=True, draft='', focus=True, paste_wrong=False, enter_noop=False,
                 enter_silent=False, count_none=False, title_flip=False, clear_fails=False, paste_lag=0):
        self.box, self.sent, self.enters = draft, [], 0
        self.lag = 0
        self.paste_lag = paste_lag   # 粘贴后前几次读回还是空的（11/18 真机：控件值晚约 0.1 秒）
        self.o = dict(title_ok=title_ok, focus=focus, paste_wrong=paste_wrong, enter_noop=enter_noop,
                      enter_silent=enter_silent, count_none=count_none, title_flip=title_flip,
                      clear_fails=clear_fails)
        self.title_checks = 0

    def open(self, title): return True

    def title_ok(self, title):
        self.title_checks += 1
        # title_flip：第一次核对对，之后（回车前那次）变了，模拟中途被切走
        return self.o['title_ok'] and not (self.o['title_flip'] and self.title_checks > 1)

    def input_text(self):
        if self.lag:                 # -1 表示一直读不到
            self.lag -= self.lag > 0
            return ''
        return self.box

    def write(self, text):
        self.box += ('错' if self.o['paste_wrong'] else '') + text
        self.lag = self.paste_lag
        return True

    def clear(self):
        if not self.o['clear_fails']:
            self.box = ''
        return self.box == ''

    def focus_input(self): return self.o['focus']
    def input_focused(self): return self.o['focus']

    def count_mine(self, title, msg):
        return None if self.o['count_none'] else self.sent.count(msg)

    def press_enter(self):
        self.enters += 1
        if self.o['enter_noop']:
            return                       # 回车没起作用：字留在框里
        if not self.o['enter_silent']:
            self.sent.append(self.box)   # enter_silent：发出去了但消息列表看不到（核对不上）
        self.box = ''


CASES = [
    # (说明, 假适配器参数, 应该的结果, 应该按回车几次, 框里应该剩什么)
    ('一切正常', {}, send.OK, 1, ''),
    ('会话对不上：不粘不按', {'title_ok': False}, send.RETRY, 0, ''),
    ('发前数不清：不粘不按', {'count_none': True}, send.RETRY, 0, ''),
    ('焦点不在输入框：不粘不按', {'focus': False}, send.RETRY, 0, ''),
    ('框里有别人的草稿：不动、交给人', {'draft': '我打了一半'}, send.HUMAN, 0, '我打了一半'),
    ('粘贴后控件值晚到：等到一致再发', {'paste_lag': 2}, send.OK, 1, ''),
    # 读回一直是空，程序以为框是空的不会去清；框里真有字时下一轮会按"已有草稿"交给人，不会误发
    ('粘贴后一直读不到：不按回车', {'paste_lag': -1}, send.RETRY, 0, '测试'),
    ('粘贴读回不对：清掉、重试', {'paste_wrong': True}, send.RETRY, 0, ''),
    ('粘贴读回不对又清不掉：交给人', {'paste_wrong': True, 'clear_fails': True}, send.HUMAN, 0, '错测试'),
    ('回车前会话变了：不按、清掉', {'title_flip': True}, send.RETRY, 0, ''),
    ('回车没起作用：字还在，清掉、重试', {'enter_noop': True}, send.RETRY, 1, ''),
    ('发了但核对不上：可能已发、交给人', {'enter_silent': True}, send.UNSURE, 1, ''),
]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    bad = 0
    for desc, kw, want, enters, left in CASES:
        a = Fake(**kw)
        with patch.object(send, 'press', side_effect=lambda k, a=a: a.press_enter() if k == 'enter' else None), \
             patch.object(send.time, 'sleep'):
            got, why = send.deliver(a, {'to': '测试好友A', 'msg': '测试'}, log=lambda *x: None)
        good = (got, a.enters, a.box) == (want, enters, left)
        bad += not good
        print(f"  [{'对' if good else '错'}] {desc}：{got}，按回车 {a.enters} 次，框里剩 {a.box!r}（{why}）")
    print(f'\n{len(CASES) - bad}/{len(CASES)} 对')
    assert bad == 0, '有用例不对'

    from agent.core import trigger
    old = {'who': 'them', 'kind': 'text', 'text': '几点了？'}
    new = {**old, 'text': '我问的是明天的安排'}
    for scenario, want, left in (
        ('自动旧稿', send.RETRY, ''),
        ('本人改稿', send.HUMAN, '本人正在写'),
        ('读屏失败', send.HUMAN, '现在九点'),
        ('重要事项', send.HUMAN, '现在九点'),
    ):
        a = Fake()
        a.hwnd = 1
        task = {'to': '测试好友A', 'msg': '现在九点', 'reply_snapshot': trigger.msg_key([old]),
                'pause': scenario == '重要事项'}

        def read_view(*args):
            if scenario == '本人改稿':
                a.box = '本人正在写'
            return {'ok': scenario != '读屏失败', 'msgs': [old, new]}

        with patch.object(trigger, 'read_view', side_effect=read_view), patch.object(send, 'press') as press:
            result, _ = send.deliver(a, task, log=lambda *args: None)
            assert (result, a.box) == (want, left), scenario
            if want == send.RETRY:
                assert task['msg'] is None and 'reply_snapshot' not in task
            press.assert_not_called()
        print(f'晚到消息 / {scenario}：{result}')


if __name__ == '__main__':
    main()
