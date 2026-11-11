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
# 发送许可用的假画面：标题、输入框读回按用例改 desk
desk = {'title': '文件传输助手', 'box': '测试回复 1'}
g.capture_window = lambda h: type('Shot', (), {'image': None})()
g.ocr = lambda img: []
g.title_matches = lambda items, name: desk['title'] == name
g.journal.record = lambda task, status: None
g.read_focused_text = lambda: desk['box']


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
for kind, act in [('click', {'type': 'click', 'target': '输入框'}), ('type', {'type': 'type', 'text': 'x'}), ('key', {'type': 'key', 'key': 'enter'})]:
    calls.clear(); g.activate = lambda h: (False, '窗口没响应')
    r = g.act(st(act))['steps'][-1]['result']
    ok(f'切前台失败时 {kind} 不动键鼠，记 "{r}"', calls == [] and r.startswith('切前台失败'))

g.activate = lambda h: (True, '')
calls.clear(); r = g.act(st({'type': 'key', 'key': 'enter'}))['steps'][-1]['result']
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

# 发送许可（D12，第 4、19、20、22、25、26 条）：按回车前最后一刻再查标题和输入框
for name, title, box in [('会话标题变了（列表重排、点到别的会话）', '测试好友B', '测试回复 1'),
                         ('焦点被抢走，读不到输入框', '文件传输助手', ''),
                         ('框里只差标点', '文件传输助手', '测试回复 1。'),
                         ('框里多了别的字', '文件传输助手', '你好测试回复 1'),
                         ('焦点在搜索框，读到的是搜索词', '文件传输助手', '文件传输')]:
    desk.update(title=title, box=box)
    calls.clear(); s = st({'type': 'key', 'key': 'enter'}); r = g.act(s)['steps'][-1]['result']
    ok(f'{name} → 不按回车，记 "{r}"', calls == [] and r.startswith('发送许可作废'))
desk.update(title='文件传输助手', box='测试回复 1')
ok('发送许可作废 → 停', g.after_check(st({}, steps=[{'action': {}, 'result': '发送许可作废：x'}])) == END)

# Codex 11/10 大洞 1：不认识的动作不能落到按回车
g.WHITELIST = ['文件传输助手']
calls.clear(); r = g.act(st({'type': 'noop'}))['steps'][-1]['result']
ok(f'「做」碰到不认识的动作 → 不按回车，记 "{r}"', calls == [] and '不认识' in r)
ok('规矩拦下不认识的动作', not g.rules(st({'type': 'noop'}, items=[]))['allow'])
calls.clear(); g.act(st({'type': 'key', 'key': 'ctrl+v'}))
ok('「做」碰到 enter 以外的键 → 不按', calls == [])

# Codex 11/10 大洞 2：描述写"点输入框"，坐标落在「发送」按钮上
SEND_BTN = {'text': '发送(S)', 'box': [860, 740, 930, 765], 'score': 1.0}
for name, loc, want in [('落在按钮正中', (895, 752), False), ('偏出按钮 10px', (940, 752), False),
                        ('离按钮远（输入框中间）', (600, 700), True)]:
    rr = g.rules(st({'type': 'click', 'target': '输入框'}, items=[SEND_BTN], location=loc))
    ok(f'点在{name} → {"放行" if want else "拦"}（{rr["block_reason"] or "放行"}）', rr['allow'] == want)

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

# D12 收尾（11/11）：输完字只许回车；回车前不再靠 OCR 看输入框
typed = [{'action': {'type': 'click'}, 'result': '已点击'}, {'action': {'type': 'type'}, 'result': '已输入'}]
for name, loc in [('点在发送按钮位置、识字没认出按钮', (895, 752)), ('点在别处', (300, 300))]:
    rr = g.rules(st({'type': 'click', 'target': '输入框'}, steps=typed, items=[], location=loc))
    ok(f'输完字后 click（{name}）→ 拦', not rr['allow'])
blocked_then = typed + [{'action': {'type': 'click'}, 'result': '字已经输好了…'}]
ok('输完字、被拦一次后再 click → 还是拦', not g.rules(st({'type': 'click', 'target': 'x'}, steps=blocked_then, items=[], location=(300, 300)))['allow'])
ok('输完字后 key enter，OCR 一个字都没认出 → 放行（交给发送许可读回）',
   g.rules(st({'type': 'key', 'key': 'enter'}, steps=typed, items=[]))['allow'])
ok('没输字就 key enter → 拦', not g.rules(st({'type': 'key', 'key': 'enter'}, steps=typed[:1], items=[]))['allow'])
ok('还没输字时 click 输入框 → 放行', g.rules(st({'type': 'click', 'target': '输入框'}, steps=typed[:1], items=[], location=(300, 300)))['allow'])

# 核对：被拦下的回车不去等消息；打字按读回结果算
g.judge_sent = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('不该去核对'))
g.screen_changed = lambda a, b: False
c = g.check(st({'type': 'key', 'key': 'enter'}, steps=typed + [{'action': {}, 'result': '按 enter 之前必须先 type'}], picture=None, step_ok=False))
ok('回车被拦下 → 不去核对聊天区', c['done'] is False)
c = g.check(st({'type': 'type', 'text': '测试回复 1'}, steps=typed, picture=None, step_ok=False))
ok('打字记了"已输入" → 这步算过，不靠 OCR', c['step_ok'] and c['failures'] == 0)

# trigger.run_one：被拒绝、许可作废 → 交给人，不重试
import agent.core.trigger as tr
tr.prepare = lambda h: True
tr.open_chat = lambda h, t: 'shot'
for last, want in [('被底层拒绝：草稿', tr.HUMAN), ('发送许可作废：读不到输入框', tr.HUMAN),
                   ('切前台失败：没响应', tr.RETRY), ('已按回车', tr.UNSURE)]:
    end = {'done': False, 'steps': [{'action': {}, 'result': last}], 'step_count': 1}
    tr.graph.build_graph = lambda nodes=None, _e=end: type('G', (), {'invoke': lambda self, s, c: _e})()
    got, _ = tr.run_one(1, {'to': '文件传输助手', 'msg': 'x'})
    ok(f'run_one 停在"{last[:8]}…" → {got}', got == want)
print('全部通过' if bad == 0 else f'{bad} 个不对')
sys.exit(1 if bad else 0)
