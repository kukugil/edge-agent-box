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
    mn.read_page = lambda h, _p=s["items"]: _p   # 「做」里重读页面：没变
    s.update(mn.act(s))
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
s.update(mn.act(s))
ok(f'  「做」记成 "{s["steps"][-1]["result"]}"，路口继续', s['steps'][-1]['result'] == '已点击' and g.after_check(s) == 'look')

print('六、第 8 条：只认标题完全一致的原邮件，发前再核对主题')
inbox = [_t('测试邮件 10', 494, 230, 560, 248), _t('回复：测试邮件 3', 494, 260, 600, 278), _t('测试邮件 3', 494, 290, 560, 308)]
s = state('列表里标题为“测试邮件 3”的邮件', inbox)
ok(f'有完全一致的那封 → 放行，点在它上面（{s["location"]}）', s['allow'] and s['location'][1] == 299)
s = state('列表里标题为“测试邮件 3”的邮件', inbox[:2])
ok(f'只有「回复：测试邮件 3」→ 拦（{s["block_reason"]}）', not s['allow'])
s1 = {**g.new_state({'app': 'mail', 'to': ADDR, 'subject': '测试邮件 1', 'msg': '测试回复 1'}, 1),
      'action': {'type': 'click', 'target': '列表里标题为“测试邮件 1”的邮件'}, 'items': inbox}
s1.update(mn.locate(s1)); s1.update(mn.rules(s1))
ok(f'找"测试邮件 1"、只有"测试邮件 10" → 拦（{s1["block_reason"]}）', not s1['allow'])
wrong_subj = [dict(it, value='回复：测试邮件 30') if it['text'] == '主题' else it for it in page(GOOD_TO, True)]
s = state('顶部写着“发送”的按钮', wrong_subj)
ok(f'主题栏是"回复：测试邮件 30" → 不许发（{s["block_reason"]}）', not s['allow'])

print('七、第 19、21 条：逐字比')
s = g.new_state({'app': 'mail', 'to': ADDR, 'subject': '测试邮件 3', 'msg': '测试回复 3'}, 1)
s.update(action={'type': 'type', 'text': '测试回复3'}, items=page(GOOD_TO, False))
ok('打的字少个空格 → 拦', not mn.rules(s)['allow'])
s = state('顶部写着“发送”的按钮', page(GOOD_TO, False) + [_t('额外文字', 340, 310, 400, 328), _t('测试回复 3', 400, 310, 480, 328)])
ok(f'正文是"额外文字 + 这句" → 不许发', not s['allow'])

print('八、Codex 11/10 大洞 1：不认识的动作')
s = g.new_state({'app': 'mail', 'to': ADDR, 'subject': '测试邮件 3', 'msg': '测试回复 3'}, 1)
s.update(action={'type': 'noop'}, items=page(GOOD_TO, True))
ok('动作 noop → 拦', not mn.rules(s)['allow'])

print('九、Codex 11/10 大洞 3：规矩查完、点下去之前页面变了')
for name, changed in [('收件人被改成别人', page(BAD_TO, True)),
                      ('正文被清空', page(GOOD_TO, False)),
                      ('页面整个换了（读不到东西）', []),
                      ('发送按钮挪了位置', [dict(it, box=[900, 162, 930, 180]) if it['text'] == '发送' else it
                                         for it in page(GOOD_TO, True)])]:
    s = state('顶部写着“发送”的按钮', page(GOOD_TO, True))
    allowed = s['allow']
    mn.read_page = lambda h, _p=changed: _p
    calls.clear()
    s.update(mn.act(s))
    r = s['steps'][-1]['result']
    ok(f'{name} → 不点，记 "{r}"', allowed and not [c for c in calls if c[0] == 'click'] and r.startswith('发送许可作废'))
    ok('  路口停下', g.after_check(s) == END)
    calls.clear(); mn.check(s)
    ok('  没点就不去「已发送」里数', ('open_folder', '已发送') not in calls)

print('十一、11/11 真机：正文框同一起点多一块 "n" 残影')
ghost = page(GOOD_TO, True) + [_t('n', 340, 310, 349, 328)]   # 和 MSG 同一个起点
s = state('顶部写着“发送”的按钮', ghost)
ok(f'同起点的 "n" 残影不算 → 放行（{s["block_reason"] or "放行"}）', s['allow'])
extra = page(GOOD_TO, True) + [_t('额外', 430, 310, 470, 328)]   # 起点不同的真字
s = state('顶部写着“发送”的按钮', extra)
ok('别的位置多出的字照样拦', not s['allow'])

print('十二、11/11 真机：「已发送」按最上面几行判，不数可见区')
from agent.core.mail_check import judge_replied, top_subjects
def sent(*subjects, nav=()):
    rows = [_t(s, 494, 278 + 40 * i, 605, 296 + 40 * i) for i, s in enumerate(subjects)]
    return [_t(s, 52, 203 + 34 * i, 163, 221 + 34 * i) for i, s in enumerate(nav)] + rows   # 左侧栏标签不算
before_top = ['回复：测试邮件 2', '回复：测试邮件 3', '回复：测试邮件 1']
R1, R2, R3 = '回复：测试邮件 1', '回复：测试邮件 2', '回复：测试邮件 3'
ok('top_subjects 不算左侧栏标签', top_subjects(sent(R2, R3, R1, nav=(R1, R1)), 3) == before_top)
for name, after, want in [('正好新插进 1 封（旧行被挤出可见区也没关系）', sent(R1, R2, R3), 'ok'),
                          ('插进两封同标题 → 不确定', sent(R1, R1, R2), 'unknown'),
                          ('最上面不是这封 → 不确定', sent(R2, R3, R1), 'unknown'),
                          ('同一行读了两次也只算一行', sent(R1, R2, R3) + [_t(R1, 494, 280, 605, 298)], 'ok')]:
    got = judge_replied(after, R1, 1, top_before=before_top)[0]
    ok(f'{name} → {got}', got == want)

print('十、打开文件夹前 Chrome 切不到前台 → 一下都不点')
import agent.tools.web as web
import agent.tools.inputs as inp
import agent.tools.window as win
real = (web.read_page, inp.click, win.activate)
clicks = []
web.read_page = lambda h: [_t('已发送', 52, 305, 94, 323)]
inp.click = lambda x, y: clicks.append((x, y))
win.activate = lambda h: (False, '被别的窗口挡着')
ok('切不到前台：交回空、没点', web.open_folder(1, '已发送') == [] and clicks == [])
web.read_page, inp.click, win.activate = real

g.build_graph(mn.NODES)
ok('邮件整张图建得起来', True)
print('全部通过' if bad == 0 else f'{bad} 条没通过')
sys.exit(1 if bad else 0)
