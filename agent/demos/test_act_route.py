"""离线测「做」和路口 after_check（反例清单第 9、10、24 条的止血）。假键鼠，只数调用次数，不碰真窗口。

用法（在项目根目录）：python -m agent.demos.test_act_route
"""
import sys
import agent.core.graph as g
from agent.tools import InputRefused
from langgraph.graph import END

calls = []
g.time.sleep = lambda s: None
g.window_rect = lambda h: (100, 200, 0, 0)
g.click = lambda x, y: calls.append(('click', x, y))
g.press = lambda k: calls.append(('press', k))
g.paste_text = lambda t, mode='empty_only': calls.append(('paste', t, mode))


def st(action, allow=True, steps=None, **kw):
    s = {'task': {'to': '文件传输助手', 'msg': '测试回复 1'}, 'action': action, 'allow': allow, 'block_reason': '规矩拦了', 'hwnd': 1,
         'location': (10, 20), 'steps': steps or [], 'step_count': len(steps or []),
         'done': False, 'failures': 0}
    s.update(kw)
    return s


sys.stdout.reconfigure(encoding='utf-8')
bad = 0
def ok(name, cond):
    global bad
    bad += not cond
    print(f"  [{'对' if cond else '错'}] {name}")


g.build_graph()   # 先确认整张图建得起来：11/9 删重复函数时误删过 after_rules，单测函数测不出来
ok('整张图建得起来', True)

# act
for kind, act in [('click', {'type': 'click', 'target': '输入框'}), ('type', {'type': 'type', 'text': 'x'}), ('key', {'type': 'key', 'keys': 'enter'})]:
    calls.clear(); g.activate = lambda h: (False, '窗口没响应')
    r = g.act(st(act))['steps'][-1]['result']
    ok(f'切前台失败时 {kind} 不动键鼠，记 "{r}"', calls == [] and r.startswith('切前台失败'))

g.activate = lambda h: (True, '')
calls.clear(); r = g.act(st({'type': 'key', 'keys': 'enter'}))['steps'][-1]['result']
ok(f'正常按回车：记 "{r}"，按了一次', r == '已按回车' and calls == [('press', 'enter')])
calls.clear(); r = g.act(st({'type': 'click', 'target': '发送按钮'}))['steps'][-1]['result']
ok(f'点发送：记 "{r}"，点在 (110, 220)', r == '已点发送' and calls == [('click', 110, 220)])
calls.clear(); g.act(st({'type': 'type', 'text': '测试回复 1'}))
ok('微信输入用 empty_only（有草稿就拒绝）', calls == [('paste', '测试回复 1', 'empty_only')])
calls.clear(); g.act(st({'type': 'type', 'text': '测试回复 1'}, task={'app': 'mail'}))
ok('邮件正文用 replace（整框换掉，粘完读回）', calls == [('paste', '测试回复 1', 'replace')])
calls.clear(); r = g.act(st({'type': 'done'}))['steps'][-1]['result']
ok(f'done 不动键鼠：记 "{r}"', r == '模型说完成了' and calls == [])
calls.clear(); r = g.act(st({'type': 'key'}, allow=False))['steps'][-1]['result']
ok(f'规矩拦下不动键鼠：记 "{r}"', r == '规矩拦了' and calls == [])

def refuse(t, mode='empty_only'): raise InputRefused('输入框里已经有 2 个字符')
g.paste_text = refuse
calls.clear(); r = g.act(st({'type': 'type', 'text': 'x'}))['steps'][-1]['result']
ok(f'底层拒绝不崩：记 "{r}"', r.startswith('被底层拒绝：输入框里') and calls == [])

# after_check
S = lambda *rs, **kw: st({}, steps=[{'action': {}, 'result': x} for x in rs], **kw)
ok('按过回车、核对失败一次 → 停', g.after_check(S('已输入', '已按回车', failures=1)) == END)
ok('点过发送、后面又点了别处 → 停', g.after_check(S('已点发送', '已点击')) == END)
ok('上一步被底层拒绝 → 停', g.after_check(S('已点击', '被底层拒绝：草稿')) == END)
ok('上一步切前台失败 → 停', g.after_check(S('切前台失败：没响应')) == END)
ok('被拒绝在前、之后正常 → 继续', g.after_check(S('被底层拒绝：x', '已点击')) == 'look')
ok('刚输入完还没回车 → 继续', g.after_check(S('已点击', '已输入')) == 'look')
ok('规矩拦下 → 继续（让模型换做法）', g.after_check(S('规矩拦了')) == 'look')
ok('一步都没有 → 继续', g.after_check(S()) == 'look')
print('全部通过' if bad == 0 else f'{bad} 个不对')
sys.exit(1 if bad else 0)
