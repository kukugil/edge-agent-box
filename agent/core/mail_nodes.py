"""邮件版的节点：和微信共用同一张图（看 → 想 → 定位 → 规矩 → 做 → 核对），只换其中几个节点。

感知分流：
  微信  看 = 截图 + OCR      定位 = UI-TARS + OCR 校正      核对 = 数聊天气泡
  邮件  看 = 截图 + 读控件树  定位 = 按引号里的文字在控件树里找  核对 = 去「已发送」数标题
「想」都是 Qwen 看截图；「做」直接用 graph.py 的 act（点击、粘贴、回车，对网页一样管用）。

任务：在收件箱打开「测试邮件 k」，点「回复」，在正文里写「测试回复 k」，点「发送」。
     收件人由回复自动带上，规矩在点「发送」前核对它只能是测试号自己。

你要做的：MAIL_PROMPT、think、look、rules、check 五处 TODO。
工具层已经写好（agent/tools/web.py）：read_page、locate_text、open_folder、find_mail_window、wait_page。
核对函数你刚写完（agent/core/mail_check.py）：recipients_ok、count_subject、verify_replied。

运行（在项目根目录，QQ 邮箱开在带无障碍参数的 Chrome 里，停在收件箱）：
  python -m agent.core.mail_nodes 3          回复「测试邮件 3」
会真的动键鼠。跑的时候别碰鼠标键盘。
"""
import json
import sys
import time
from pathlib import Path

from agent.core import graph                       # 复用 act、after_check、new_state、build_graph
from agent.core.mail_check import count_subject, recipients_ok, verify_replied
from agent.core.state import AgentState
from agent.demos.baseline_loop import parse_json
from agent.tools import activate, capture_window, chat, norm, screen_changed
from agent.tools.models import image_url
from agent.tools.web import body_text, find_mail_window, locate_text, open_folder, read_page
from agent.tools.web import _quoted, body_text, find_mail_window, locate_text, open_folder, read_page

CFG = graph.CFG
CONTACTS = Path(__file__).resolve().parent.parent / 'test_contacts.local.json'
ALLOWED = json.loads(CONTACTS.read_text(encoding='utf-8'))['allowed_mail']   # 只有测试号自己

# TODO 提示词：照 graph.py 的 PLAN_PROMPT 改，三处不同：
#   1. 开头改成“你在操作 Chrome 里的 QQ 邮箱网页版”
#   2. 动作只要 click / type / done / fail 四种（发送是点「发送」按钮，不按回车）
#   3. 常识换成邮件的，至少这三条：
#      - 收件箱列表里点邮件标题打开它；打开后顶部有「回复」按钮
#      - 点「回复」后，收件人和主题会自动填好，不要改；正文在主题下面的大片空白，先点正文再 type
#      - 正文写好后点顶部的「发送」按钮
# - 回复一封邮件必须按这个顺序，一步都不能跳：
#   1. 点开列表里标题为“测试邮件 N”的邮件
#   2. 点顶部写着“回复”的按钮（还没进回复页时，页面上没有正文框，不要点正文、不要点发送）
#   3. 点主题下面的“正文”输入区
#   4. type 任务里那句回复，text 原样照抄，一个字都不能改
#   5. 点顶部写着“发送”的按钮
# - 动作被拦下时，看拦下的原因做下一步，不要重复同一个动作
#   target 里要点的字一定用中文引号括起来，例如：顶部写着“回复”的按钮——定位就是按引号里的字找的
# 坑：JSON 的大括号写成 {{ }}
MAIL_PROMPT = '''你在操作Chrome 里的 QQ 邮箱网页版，图片是当前的网页窗口截图。
任务：{task}

已经执行的步骤：
{history}

根据当前截图决定下一步，只做一个动作。只输出一个 JSON，格式是下面之一：
{{"thought": "一句话：当前界面是什么、为什么这么做", "action": "click", "target": "要点击的元素，写清位置和外观"}}
{{"thought": "...", "action": "type", "text": "要输入的文字（输入到当前光标所在的输入框）"}}
{{"thought": "...", "action": "done"}}      （截图里能确认任务已经完成）
{{"thought": "...", "action": "fail", "reason": "..."}}
target 里要点的字用中文引号括起来，字要和页面上一模一样，例如：顶部写着“回复”的按钮、列表里标题为“测试邮件 3”的邮件。

QQ界面常识（这个应用的“操作手册”）：
- 收件箱列表里点邮件标题打开它；打开后顶部有「回复」按钮
- 收件箱里有很多封同一个发件人的邮件，还有标题以“回复：”开头的旧回复。要点的是标题正好是任务里那个标题的邮件，target 写成：列表里标题为“测试邮件 3”的邮件。不要点发件人名字，也不要打开“回复：”开头的邮件
- 点正文时 target 写：主题下面的“正文”输入区
- 点「回复」后，收件人和主题会自动填好，不要改；正文在主题下面的大片空白，先点正文再 type
- 正文写好后点顶部的「发送」按钮
- 回复一封邮件必须按这个顺序，一步都不能跳：
  1. 点开列表里标题为“测试邮件 N”的邮件
  2. 点顶部写着“回复”的按钮（还没进回复页时，页面上没有正文框，不要点正文、不要点发送）
  3. 点主题下面的“正文”输入区
  4. type 任务里那句回复，text 原样照抄，一个字都不能改
  5. 点顶部写着“发送”的按钮
- 动作被拦下时，看拦下的原因做下一步，不要重复同一个动作

如果上一步没有达到预期，先根据截图纠正；同一个办法试了两次都不行，必须换一种办法。
'''

def think(state: AgentState) -> dict:
    """想：和 graph.think 一样，只换提示词和任务那句话。

    TODO：把 graph.py 的 think 复制过来，改两处：
      1. 任务文字：f"回复邮件“{task['subject']}”，正文写：{task['msg']}"
      2. PLAN_PROMPT 换成 MAIL_PROMPT
    """
    print('想')
    # TODO
    lines = []
    for i,s in enumerate(state['steps'],1):
        extra = s['action'].get('target') or s['action'].get('text') or s['action'].get('key') or ''
        lines.append(f"{i}. {s['action']['type']} {extra} → {s['result']}")
    history = '\n'.join(lines) or '(无)'

    task = state['task']
    task_text = f"回复邮件“{task['subject']}”，正文写:{task['msg']}"
    prompt = MAIL_PROMPT.format(task=task_text,history = history)

    raw = chat(CFG['vlm_url'], CFG['vlm_model'], [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': image_url(state['picture'])}},
            {'type': 'text', 'text': prompt}]}],
            max_tokens=300)
    a = parse_json(raw)
    if not a:
        return {'action':{'type':'fail','reason':'看不懂模型回复'}}
    a['type'] = a.pop('action', 'fail')
    print(f"  → {a['type']}  target={a.get('target', '')}  text={a.get('text', '')}")
    return {'action': a}


def look(state: AgentState) -> dict:
    """看：截图给「想」用，读控件树给「定位」「规矩」用。

    输出：{'picture': 截图, 'items': read_page 的结果}
    提示：capture_window(hwnd) 交回 Shot，图在 .image；None 说明截图失败。
         count_before 不在这里数：它要去「已发送」里数，main 里开跑之前已经数好放进 state 了。
    """
    print(f"看    （第 {state['step_count'] + 1} 步）")
    # TODO
    shot = capture_window(state['hwnd'])
    if shot is None:
        sys.exit('截图失败')
    items = read_page(state['hwnd'])
    return {'picture':shot.image,'items':items}
    
def locate(state: AgentState) -> dict:
    """定位：click 才要找点。不用 UI-TARS，按 target 里引号的字在控件树里找（已写好）。"""
    print('定位')
    a = state['action']
    if a.get('type') != 'click':
        return {'location': []}
    pt, note = locate_text(state['items'], a.get('target', ''))
    print(f'  {note}')
    return {'location': pt}


def rules(state: AgentState) -> dict:
    """规矩：程序判断这一步许不许做，不问模型。

    输出：{'allow': True/False, 'block_reason': 一句话原因}
    按顺序查：
      1. task['to'] 不在 ALLOWED → 拦，原因必须正好写 '收件人不在白名单'（条件边认这句话，直接结束）
      2. type：文字 norm() 后必须等于 task['msg']
      3. key：一律拦（邮件不用按键，回车可能在主题里提前触发别的东西）
      4. click：
         - location 是空的 → 拦（“没找到要点的位置，target 里的字要和页面上一模一样，用引号括起来”）
         - target 里有“删除”“关闭”“退出”“举报”“拒收” → 拦
         - target 里有“发送”时，点之前要过两关：
             a. recipients_ok(items, ALLOWED[0]) 不通过 → 拦，原因用它交回的
             b. 正文里要有这句话：这次 items 里能找到 norm(text) 或 norm(value) 等于 norm(task['msg']) 的 → 才放行
                找不到就拦（“正文里还没有要发的那句话，先点正文再 type”）
      done 和 fail 直接允许。
    原因里不要写地址。
    """
    print('规矩')
    # TODO
    task,a,items = state['task'],state['action'],state['items']
    kind = a.get('type')
    def block(why):
        print(f"阻塞原因：{why}")
        return {'allow':False ,'block_reason':why}
    # 收件人不在白名单
    if task['to'] not in ALLOWED:
        return block('收件人不在白名单')
    # 动作是type，但字段不对
    if kind == 'type' and norm(a.get('text')) != norm(task['msg']):
        return block(f'正文只能写：{task["msg"]}')
    # 动作是 key
    if kind == 'key':
        return block('动作是 key')
    if kind == 'click':
        # 动作是click但是没找到对的位置
        if not state['location']:
            return block('动作是click但是没找到对的位置')
        # 动作是click，但是target里面有危险的词
        qs = _quoted(a.get('target',''))
        want = norm(qs[-1]) if qs else norm(a.get('target',''))
        if any(w in want for w in ('删除','关闭','退出','举报','拒收')):
            return block('动作是click，但是target里面有危险的词')
        if '发送' in a.get('target',''):
            ok,why = recipients_ok(items,ALLOWED[0])
            # 点击发送，但是收件人不对
            if not ok:
                return block(why)
            # 点击发送，但是正文里没有那句话
            m = norm(task['msg'])
            # 只看正文框里自己写的部分：引用的原邮件里可能带着上次的“测试回复 k”，整页找会被骗过
            if m not in norm(body_text(items)):
                return block('正文里还没有要发的那句话，先点正文再 type')
    return {'allow': True, 'block_reason': ''}

def check(state: AgentState) -> dict:
    """核对：不信模型说的“完成”，自己去「已发送」里数。

    输出：done、failures、step_ok 三个键。
    分两种：
      1. 这一步点的是「发送」，或者模型说 done：
         items = open_folder(state['hwnd'], '已发送')   # 会点左边「已发送」，等列表换好
         ok, why = verify_replied(items, '回复：' + task['subject'], state['count_before'])
         打印 why；done = ok；不通过 failures + 1
         坑：发送后要等 2–3 秒再去「已发送」，不然列表里还没有。
      2. 其他步：截一张新图，screen_changed(state['picture'], 新图) 没变就 failures + 1
    """
    # TODO
    a = state['action']
    task = state['task']
    failures = state['failures']
    done = False
    step_ok = False
    key_step = state['allow'] and (
    (a.get('type') == 'click' and '发送' in a.get('target', ''))
    or a.get('type') == 'done')
    if key_step:
        subject = '回复：' + task['subject']
        before = state['count_before']
        for i in range(10):
            items = open_folder(state['hwnd'], '已发送')
            if count_subject(items, subject) > before:
                break
            time.sleep(1)
        ok, why = verify_replied(items, subject, before)
        print(f'核对:{why}')
        if ok:
            return {'done':True,'failures':failures,'step_ok':True}
        return {'failures' : failures + 1}
    else:
        shot = capture_window(state['hwnd'])
        if shot is None:
            sys.exit('截图失败')
        step_ok = screen_changed(state['picture'],shot.image)
        print(f"  核对：画面{'变了' if step_ok else '没变'}")
        if not step_ok:
            return {'failures' : failures + 1,'step_ok':step_ok}
        return {'done': done, 'failures': failures, 'step_ok': step_ok}

    

NODES = {'look': look, 'think': think, 'locate': locate, 'rules': rules, 'act': graph.act, 'check': check}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    if len(sys.argv) != 2:
        sys.exit('用法：python -m agent.core.mail_nodes 编号      例如 3 表示回复「测试邮件 3」')
    k = sys.argv[1]
    task = {'app': 'mail', 'to': ALLOWED[0], 'subject': f'测试邮件 {k}', 'msg': f'测试回复 {k}'}
    hwnd = find_mail_window()
    if not hwnd:
        sys.exit('没找到开着 QQ 邮箱的 Chrome 窗口（要带无障碍参数启动，窗口不能最小化）')
    activate(hwnd)
    # 发之前先去「已发送」数一下：同一封以前回复过的话，那里本来就有同样的标题
    items = open_folder(hwnd, '已发送')
    if not items:
        sys.exit('打不开「已发送」')
    state = graph.new_state(task, hwnd)
    state['count_before'] = count_subject(items, '回复：' + task['subject'])
    print(f"「已发送」里原来有 {state['count_before']} 封同标题的")
    if not open_folder(hwnd, '收件箱'):
        sys.exit('回不到「收件箱」')
    end = graph.build_graph(NODES).invoke(state, {'recursion_limit': 80})
    print(f"\n结束：{'通过' if end['done'] else '没通过'}，走了 {end['step_count']} 步，失败 {end['failures']} 次")


if __name__ == '__main__':
    main()
