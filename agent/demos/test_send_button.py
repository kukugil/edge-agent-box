"""离线测：邮件"发送"要按实际点到的元素认，不按模型写的 target（D12 第一件，Codex 11/10 审查发现）。

漏洞：target 写成 顶部写着“送”的按钮，定位退回包含匹配照样点到「发送」，
    但规矩只在 target 含"发送"时才查收件人和正文 → 不查收件人就发；「做」记成"已点击"，路口不停 → 还可能发第二遍。
假键鼠、假页面数据，不碰真窗口。

用法（在项目根目录）：python -m agent.demos.test_send_button
"""
import sys

import agent.core.graph as g
import agent.core.mail_nodes as mn
from agent.core.mail_check import _t
from langgraph.graph import END

sys.stdout.reconfigure(encoding='utf-8')
ADDR = 'tester01@qq.com'          # 假地址，测的是逻辑
mn.ALLOWED = [ADDR]

calls = []
g.time.sleep = lambda s: None
mn.time.sleep = lambda s: None
g.activate = lambda h: (True, '')
g.window_rect = lambda h: (0, 0, 0, 0)
g.click = lambda x, y: calls.append(('click', x, y))
g.press = lambda k: calls.append(('press', k))
g.paste_text = lambda t, mode='empty_only': calls.append(('paste', t, mode))
mn.open_folder = lambda hwnd, name='已发送': calls.append(('open_folder', name)) or []
mn.capture_window = lambda hwnd: calls.append(('capture',)) or type('Shot', (), {'image': None})()
mn.screen_changed = lambda a, b: True

# 回信页：顶部「发送」按钮、收件人行、正文框（里面有没有那句话按用例换）
TOP = [_t('发送', 292, 162, 320, 180), _t('收件人', 244, 208, 286, 226), _t('抄送', 1490, 209, 1516, 225),
       _t('分别发送', 1599, 209, 1651, 225), _t('主题', 335, 240, 1651, 284, 'EditControl', '回复：测试邮件 3')]
BODY = _t('输入正文', 335, 300, 1651, 700, 'GroupControl')
GOOD_TO = _t('tester01', 336, 208, 419, 226)
BAD_TO = _t('tester02', 336, 208, 419, 226)
MSG = _t('测试回复 3', 340, 310, 420, 328)
SEND_XY = [306, 171]


def page(to, with_msg):
    return TOP + [to, BODY] + ([MSG] if with_msg else [])


def state(target, items):
    s = g.new_state({'app': 'mail', 'to': ADDR, 'subject': '测试邮件 3', 'msg': '测试回复 3'}, 1)
    s.update(action={'type': 'click', 'target': target}, items=items)
    s.update(mn.locate(s))
    s.update(mn.rules(s))
    return s


bad = 0
def ok(name, cond):
    global bad
    bad += not cond
    print(f"  [{'对' if cond else '错'}] {name}")


print('一、绕过：target 不写"发送"，实际点到发送按钮')
for target in ('顶部写着“送”的按钮', '顶部写着“发”的按钮', '顶部的“发 送”'):
    s = state(target, page(BAD_TO, True))
    ok(f'{target}：收件人不对 → 拦（点在 {s["location"]}）', not s['allow'])
    s = state(target, page(GOOD_TO, False))
    ok(f'{target}：正文没那句 → 拦', not s['allow'])

print('二、收件人和正文都对，target 写法不同，都当作"点发送"')
for target in ('顶部写着“发送”的按钮', '顶部写着“送”的按钮'):
    s = state(target, page(GOOD_TO, True))
    ok(f'{target}：放行，点在发送按钮', s['allow'] and s['location'] == SEND_XY)
    calls.clear()
    s.update(g.act(s))
    r = s['steps'][-1]['result']
    ok(f'  「做」记成 "{r}"', r == '已点发送')
    ok('  路口停下，不再来一圈', g.after_check(s) == END)
    calls.clear()
    mn.check(s)
    ok('  核对去「已发送」里数，不只看画面变没变', ('open_folder', '已发送') in calls)

print('三、包含匹配点到别的带"发送"的元素')
s = state('右边的“分别”', page(GOOD_TO, True))
ok(f'点到「分别发送」→ 拦（{s["block_reason"]}）', not s['allow'])

print('四、target 说点发送，实际点到的不是发送按钮')
items = [it for it in page(GOOD_TO, True) if '发送' not in it['text']] + [_t('发送通知', 500, 400, 560, 418)]
s = state('顶部写着“发送”的按钮', items)
ok(f'包含匹配点到「发送通知」→ 拦（{s["block_reason"]}）', not s['allow'])

print('五、普通点击不受影响')
s = state('顶部写着“回复”的按钮', [_t('回复', 292, 162, 320, 180)])
ok('点「回复」放行', s['allow'])
s.update(g.act(s))
ok(f'  「做」记成 "{s["steps"][-1]["result"]}"，路口继续', s['steps'][-1]['result'] == '已点击' and g.after_check(s) == 'look')

g.build_graph(mn.NODES)
ok('邮件整张图建得起来', True)
print('全部通过' if bad == 0 else f'{bad} 条没通过')
sys.exit(1 if bad else 0)
