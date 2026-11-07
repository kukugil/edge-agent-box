"""主循环的图：看 → 想 → 定位 → 规矩 → 做 → 核对，没过就绕回去重来。

D6 第三步：想、定位、做换成真的。想 = Qwen 看截图出动作；定位 = UI-TARS 找点 + OCR 校正；
做 = 真的点击、粘贴、按回车。看、核对、白名单是上一步写好的。手动版在提交 cccb6517 里。

你要做的：PLAN_PROMPT、think、locate、rules、act 五处 TODO，look 和 check 各补一小处（见各自的 TODO）。
参照物：agent/demos/baseline_loop.py 是 D1 跑通过 3/3 的手写版，行号写在各处提示里。
       照着它的思路写，不要整段复制：它是给文件传输助手写死的，这里要发给 task['to']。

运行（在项目根目录，先把微信主窗口打开，不要最小化）：
  python -m agent.core.graph 名字 "测试 1"
会真的动键鼠。跑的时候别碰鼠标键盘。名字不在白名单里时，规矩会拦下，一步都不做。
"""
import json
import sys
import time
from pathlib import Path

from langgraph.graph import StateGraph, START, END

from agent.core.check import LIST_RIGHT, chat_title, count_msg, verify_sent
from agent.core.state import AgentState
from agent.demos.baseline_loop import in_web_suggestions, parse_json  # 微信界面知识先借用，D11 挪进应用配置
from agent.tools import (activate, capture_window, chat, check_responsive, click, find_windows, ground,
                         find_text, load_config, norm, ocr, paste_text, press, screen_changed, set_window_size, snap_to_text, window_rect)
from agent.tools.models import image_url

CFG = load_config()   # 模型地址，在 agent/config.json

# TODO 提示词：从 baseline_loop.py 第 32–57 行抄过来，改三处：
#   1. 「任务」那行留 {task}，「已经执行的步骤」留 {history}，think 里会填进去
#   2. 删掉专讲“文件传输助手”的话和 {warning}；会话列表、输入框、搜索下拉框那几条常识留着
#   3. 动作只留 click / type / key / done / fail 五种（scroll 先不用），key 只写 enter
# 坑：提示词里 JSON 的大括号要写成 {{ }}，不然 .format() 会报错
PLAN_PROMPT = '''你在操作一台 Windows 电脑上的微信，图片是当前的微信窗口截图。
任务：{task}

已经执行的步骤：
{history}

根据当前截图决定下一步，只做一个动作。只输出一个 JSON，格式是下面之一：
{{"thought": "一句话：当前界面是什么、为什么这么做", "action": "click", "target": "要点击的元素，写清位置和外观"}}
{{"thought": "...", "action": "type", "text": "要输入的文字（输入到当前光标所在的输入框）"}}
{{"thought": "...", "action": "key", "key": "enter"}}
{{"thought": "...", "action": "done"}}      （截图里能确认任务已经完成）
{{"thought": "...", "action": "fail", "reason": "..."}}
target 里如果元素上有文字，用中文引号把文字括起来，例如：左上角写着“搜索”的搜索框。

微信界面常识（这个应用的“操作手册”）：
- 在会话列表里点开一个会话后，光标会自动进入消息输入框，可以直接 type。
- 消息输入框是聊天区底部、一排小图标（表情、文件夹、剪刀、麦克风）上方的大片白色区域，不是那排图标本身。
- 会话列表里找不到目标时，输出 fail，reason 写"列表里没有目标会话"。
- 搜索下拉框分两部分：上面“搜索网络结果”以及带放大镜图标、绿色文字的那几行是网络搜索建议，点了会打开网页，不要点；
  真正打开聊天的是下面“功能”“联系人”“群聊”等分组标题下、带头像或图标的那一项。

- 字只是输入到输入框里还不算完成，必须按 enter 发送；聊天区里出现了这条消息气泡，才能输出 done。

如果上一步没有达到预期，先根据截图纠正；同一个办法试了两次都不行，必须换一种办法。'''


# ---------- 微信专用的界面知识（D11 会挪进应用配置文件） ----------

MAX_STEPS = 8      # 最多走几步（点开会话、输入、回车，正常 3 步，留出重试的余量）
MAX_FAILURES = 3   # 最多失败几次

# 真白名单从本地文件读，名字不写进代码，这个文件也不进版本记录
CONTACTS = Path(__file__).resolve().parent.parent / 'test_contacts.local.json'
WHITELIST = json.loads(CONTACTS.read_text(encoding='utf-8'))['allowed_chats']


# ---------- 节点：读整个 state，只交回改动的键 ----------

def look(state: AgentState) -> dict:
    """看：截一张微信窗口的图，顺便数一下发之前聊天区里有几条这句话。

    输入：整个状态，要用 state['hwnd'] 和 state['task']['msg']。
    输出：{'picture': 截图, 'count_before': 条数}。
    提示：
      - 截图：capture_window(hwnd) 交回一个 Shot，图在 shot.image；交回 None 说明截图失败
      - 识别：ocr(shot.image) 交回文字列表
      - 数条数：count_msg 你在 check.py 里写过，已经导入了
      - picture 存 shot.image 就行，以后「想」要把它交给 Qwen
    TODO（D6 第三步）：返回值里再加一个 'items': items，定位校正和规矩都要用这次的识别结果
    """
    print(f"看    （第 {state['step_count'] + 1} 步）")
    # TODO
    shot = capture_window(state['hwnd'])
    if shot is None:
        sys.exit('截图失败')
    items = ocr(shot.image)
    out = {'picture': shot.image, 'items': items}
    # 发之前的条数只在还没按过回车时数：按过以后再数，刚发的那条会被算进“之前”
    if not any(s['result'] == '已按回车' for s in state['steps']):
        out['count_before'] = count_msg(items, state['task']['msg'])
    return out

def think(state: AgentState) -> dict:
    """想：把截图和提示词交给 Qwen，让它决定下一步。

    输入：state['picture']（截图）、state['task']（发给谁、发什么）、state['steps']（走过的步）。
    输出：{'action': 动作字典}，'type' 键是动作种类，其余键原样带上，例如
         {'type': 'click', 'target': '左侧会话列表里的“某某”', 'thought': '...'}
         {'type': 'type', 'text': '测试 1'}   {'type': 'key', 'key': 'enter'}   {'type': 'done'}
    四步：
      1. 历史：把 state['steps'] 每条变成一行字，例如 "1. click 左侧会话列表里的“某某” → 已点击"。
         一条都没有时写 "（无）"。
      2. 任务文字：f"打开和“{to}”的聊天，发送消息：{msg}"，和历史一起 .format() 进 PLAN_PROMPT。
      3. 调模型：chat(CFG['vlm_url'], CFG['vlm_model'], messages, max_tokens=300)。
         messages 的写法照抄 baseline_loop.py 第 158–161 行：一张图（image_url(图)）加一段文字。
      4. parse_json(模型回复) 得到字典。模型的 JSON 里动作种类叫 'action'，改名成 'type'。
         解析不出来（得到空字典）时交回 {'type': 'fail', 'reason': '看不懂模型的回复'}。
    打印：只打印动作种类，不打印 thought 和 target，里面可能有聊天里的名字和内容。
    """
    print("想")
    # TODO
    lines = []
    for i,s in enumerate(state['steps'],1):
        extra = s['action'].get('target') or s['action'].get('text') or s['action'].get('key') or ''
        lines.append(f"{i}. {s['action']['type']} {extra} → {s['result']}")
    history = '\n'.join(lines) or '(无)'

    task = state['task']
    task_text = f"打开和{task['to']}的聊天，发送消息:{task['msg']}"
    prompt = PLAN_PROMPT.format(task=task_text,history = history)

    raw = chat(CFG['vlm_url'], CFG['vlm_model'], [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': image_url(state['picture'])}},
            {'type': 'text', 'text': prompt}]}],
            max_tokens=300)
    a = parse_json(raw)
    if not a:
        return {'action':{'type':'fail','reason':'看不懂模型回复'}}
    a['type'] = a.pop('action', 'fail')
    print(f"  → {a['type']}")
    return {'action': a}

def locate(state: AgentState) -> dict:
    """定位：动作是 click 时，把 target 那句话变成截图里的坐标。

    输入：state['action']、state['picture']、state['items']。
    输出：{'location': [x, y]}（截图内坐标）。不是 click，或者没找到，交回 {'location': []}。
    两步（baseline_loop.py 第 177–182 行）：
      1. ground(图, target, CFG['grounder_url'], CFG['grounder_model'], CFG['grounder_max_pixels'])
         交回 (点, 原始回复)，点是 None 说明 UI-TARS 没找到。
      2. snap_to_text(state['items'], target, 点) 交回 (校正后的点, 说明)：
         UI-TARS 常偏 20–30px，如果 target 里有带引号的文字，就把点拉到那段文字的中心。
    """
    print("定位")
    # TODO
    if state['action'].get('type') != 'click':
        return {'location':[]}
    target = state['action'].get('target','')
    pt,raw = ground(state['picture'], target,           # 把截图和描述交给 UI-TARS
                     CFG['grounder_url'], CFG['grounder_model'],
                     CFG['grounder_max_pixels'])
    if pt is None:
        return {'location': []}
    pt, note = snap_to_text(state['items'], target, pt)
    return {'location':[pt[0],pt[1]]}
     
def rules(state: AgentState) -> dict:
    """规矩：程序判断这一步许不许做，不问模型。

    输出：{'allow': True/False, 'block_reason': 一句话原因}。允许时原因写空字符串。
    原因要让模型看得懂，「做」会把它记进 steps，模型下一圈就知道为什么被拦、该怎么改。
    按顺序查，哪条不过就停在哪条：
      1. task['to'] 不在 WHITELIST → 拦（条件边看到这种情况会直接结束，一步都不做）
      2. 标题：chat_title(state['items']) 用 norm() 比，不等于 task['to'] 时，
         type 和 key 一律拦（原因写“当前会话不是目标会话，先在左侧会话列表点开目标”）
      3. type：文字 norm() 后必须等于 task['msg']，防止模型自己编一句话发出去
      4. key：只许 enter，其他键（尤其 esc，会把微信藏进托盘）一律拦
      5. click：
         - target 里有“关闭”“最小化”“最大化”“退出” → 拦
         - location 是空的 → 拦（定位失败，原因写“没找到要点的位置”）
         - location 的 y < 32 → 拦（点在窗口标题栏，离关闭按钮太近）
         - in_web_suggestions(state['items'], location) 为 True → 拦（会打开网页）
      done 和 fail 不用拦，直接允许。
    """
    print("规矩")
    task, a, items = state['task'], state['action'], state['items']
    kind = a.get('type')

    def block(why):                                   # 拦下：不许做，记原因
        print(f'  拦下：{why}')
        return {'allow': False, 'block_reason': why}
    # 1. 收件人不在白名单：条件边会直接结束
    if task['to'] not in WHITELIST:
        return block('收件人不在白名单')
    # 2. 打字、回车前，当前会话必须是目标会话，不然会发错人
    if kind in ('type', 'key') and norm(chat_title(items)) != norm(task['to']):
        return block('当前会话不是目标会话，先在左侧会话列表点开目标')
    # 3. 打的字必须就是要发的那句，不许模型自己编
    if kind == 'type' and norm(a.get('text', '')) != norm(task['msg']):
        return block(f'输入的文字必须正好是：{task["msg"]}')
    # 4. 只许按回车（esc 会把微信藏进托盘）
    if kind == 'key' and str(a.get('key', '')).lower() != 'enter':
        return block('只能按 enter')
    # 4.2 回车只许紧跟在“这次输入、而且核对过输入框里有这句话”之后。
    #     不然输入框里原来的草稿（用户自己打的字）会被直接发出去
    if kind == 'key':
        # 第 1 件：从后往前找最后一个"真正执行了"的步骤，被拦的跳过
        done_results = ('已点击', '已输入', '已按回车')   # 执行了的步骤，result 是这几种之一
        last = None
        for s in reversed(state['steps']):
            if s['result'] not in done_results:      # 被拦的：result 是拦下原因
                continue                             # 跳过它，接着往前找
            last = s
            break                                    # 找到了，不用再往前

        # 第 2 件：输入框里有没有要发的那句话。和「核对」一样比条数，不靠 CHAT_BOTTOM 的位置：
        #   会话列表右边和这句话完全相同的条数，比聊天区气泡里的多，多出来的就在输入框里
        right = [it for it in items if it['box'][0] > LIST_RIGHT and norm(it['text']) == norm(task['msg'])]
        in_box = len(right) > count_msg(items, task['msg'])

        if not (last and last['result'] == '已输入' and in_box):
            return block(f'按 enter 之前必须先 type 输入“{task["msg"]}”，并且输入框里确实有这句话；输入框里原有的字不能发')
    # 4.5 还没按过回车就说完成：聊天区里的同样消息是以前发的，不算
    if kind == 'done' and not any(s['action'].get('type') == 'key' and s['result'] == '已按回车'
                                  for s in state['steps']):
        return block('这次还没按过 enter，消息没发出去；聊天区里已有的同样消息是以前发的，不算。先输入再按 enter')
    # 5. 点击的安全检查
    if kind == 'click':
        target, loc = a.get('target', ''), state['location']
        if any(w in target for w in ('关闭', '最小化', '最大化', '退出')):
            return block('不能点关闭、最小化、最大化、退出')
        if '发送' in target:   # 点发送按钮也会发出去，但焦点会跑到按钮上，后面再输入就落空；核对也只认回车
            return block('不要点「发送」按钮，字输入好后用 key enter 发送')
        if not loc:
            return block('没找到要点的位置，换一种描述')
        if loc[1] < 32:
            return block('点的位置在窗口标题栏，太危险')
        if in_web_suggestions(items, loc):
            return block('点的是网络搜索建议，会打开网页，改点联系人那一项')
    return {'allow': True, 'block_reason': ''}       # 都过了，放行


def act(state: AgentState) -> dict:
    """做：规矩允许就真的动键鼠；不允许就什么都不做，只把原因记下来。

    输出：steps 和 step_count 两个键。steps 新加的一条要多一个 'result'（一句话结果），
         think 拼历史时要用，例如 '已点击'、'已输入'、'已按回车'，或者 block_reason。
    步骤：
      1. 不允许 → result = state['block_reason']，跳到第 4 步
      2. 先 activate(state['hwnd'])：窗口要在前台，键鼠才落在微信上。交回 (成功没有, 原因)
      3. 按动作种类做：
         - click：location 是截图内坐标，鼠标要屏幕坐标。
                  l, t, _, _ = window_rect(state['hwnd'])，点 (l + x, t + y)
         - type：paste_text(文字)，它会先全选清空再粘贴，残留草稿不会一起发出去
         - key：press('enter')
         - done / fail：什么都不做，result 写“模型说完成了”或“模型说做不了”
      4. time.sleep(1)，等界面画完再让「核对」截图
    """
    print("做")
    a = state['action']
    kind = a.get('type')
    if not state['allow']:                           # 1. 被拦：不动键鼠，只记原因
        result = state['block_reason']
    else:
        activate(state['hwnd'])                      # 2. 微信放到前台
        if kind == 'click':                          # 3. 按种类做
            l, t, _, _ = window_rect(state['hwnd'])  #    截图坐标 + 窗口左上角 = 屏幕坐标
            x, y = state['location']
            click(int(l + x), int(t + y))
            result = '已点击'
        elif kind == 'type':
            paste_text(a.get('text', ''))
            result = '已输入'
        elif kind == 'key':
            press('enter')
            result = '已按回车'
        elif kind == 'done':
            result = '模型说完成了'
        else:
            result = '模型说做不了：' + str(a.get('reason', ''))
    print(f'  {result}')
    time.sleep(1)                                    # 4. 等界面画完
    # 把这一步连同结果记下来，think 下一圈拼历史用
    return {'steps': state['steps'] + [{'action': a, 'result': result}],
            'step_count': state['step_count'] + 1}


def check(state: AgentState) -> dict:
    """核对：重新截一张图，程序独立判断发对没有，不信模型说的“完成”。

    输出：done 和 failures 两个键。
    提示：
      - 一定要重新截图：state['picture'] 是「做」之前截的，看不到刚发的消息
      - 判断直接用 check.py 里的 verify_sent，它要三样东西：识别结果、task、count_before
      - 把 verify_sent 交回的原因打印出来，跑的时候才知道是哪条没过
    TODO（D6 第三步）：模型说 'done' 时也要跑 verify_sent，程序复查它说的对不对。
         改一下 if 的条件：动作是按回车，或者动作是 done。
    """
    # TODO
    shot = capture_window(state['hwnd'])
    if shot is None:
        sys.exit('截图失败')
    items = ocr(shot.image)
    failures = state['failures']
    done = state['done']
    step_ok = state['step_ok']
    a = state['action']
    if (a.get('type') == 'key' and a.get('key') == 'enter') or a.get('type') == 'done':   # 回车或模型说完成：程序复查
        alert = verify_sent(items,state['task'],state['count_before'])
        print(alert[1])
        done = alert[0]
        step_ok = done
        if not alert[0]:
            failures += 1      
    elif a.get('type') == 'type':
        # 打字：看输入框（聊天区下面）里有没有出现这句话。只打印有没有，不打印内容
        # 右边（会话列表以外）出现这句话的条数，比聊天区气泡里的条数多，多出来的就在输入框里
        from agent.core.check import LIST_RIGHT
        right = [it for it in find_text(items, state['task']['msg']) if it['box'][0] > LIST_RIGHT]
        step_ok = len(right) > count_msg(items, state['task']['msg'])
        print(f"  核对：输入框里{'有' if step_ok else '没有'}要发的字")
        if not step_ok:
            failures += 1
    else:
        step_ok = screen_changed(state['picture'],shot.image)
        print(f"  核对：画面{'变了' if step_ok else '没变'}")
        if not step_ok:
            failures += 1
    return {'done':done,'failures':failures,'step_ok':step_ok}
            
# ---------- 条件边：只选路，不改 state ----------

def after_rules(state: AgentState) -> str:
    if not state['allow'] and state['block_reason'] == '收件人不在白名单':
        return END
    return 'act'


def after_check(state: AgentState) -> str:
    """核对之后走哪条路：通过、或步数/失败到上限就停，否则回到「看」重来。"""
    if state['done'] or state['step_count'] >= MAX_STEPS or state['failures'] >= MAX_FAILURES:
        return END
    return 'look'


# ---------- 把节点和边连成图 ----------

def build_graph(nodes=None):
    """把 6 个节点和 2 个条件边连起来，交回编译好的图。nodes 不传就用微信的节点。"""
    nodes = nodes or {'look': look, 'think': think, 'locate': locate,
                      'rules': rules, 'act': act, 'check': check}
    g = StateGraph(AgentState)
    g.add_node('look', nodes['look'])
    g.add_node('think', nodes['think'])
    g.add_node('locate', nodes['locate'])
    g.add_node('rules', nodes['rules'])
    g.add_node('act', nodes['act'])
    g.add_node('check', nodes['check'])
    g.add_edge(START, 'look')
    g.add_edge('look', 'think')
    g.add_edge('think', 'locate')
    g.add_edge('locate', 'rules')
    g.add_conditional_edges('rules', after_rules)
    g.add_edge('act', 'check')
    g.add_conditional_edges('check', after_check)
    return g.compile() 


# ---------- 下面已经写好 ----------

def new_state(task: dict, hwnd: int) -> AgentState:
    """开头的状态：14 个键都要有值，节点读不到键会报 KeyError。"""
    return {'task': task, 'steps': [], 'step_count': 0, 'failures': 0,
            'picture': None, 'action': {}, 'location': [], 'allow': False, 'done': False,'step_ok': False,
            'hwnd': hwnd, 'count_before': 0, 'items': [], 'block_reason': ''}


WIN_SIZE = (960, 800)   # check.py 里那几条坐标线是按这个大小量的，开跑前把窗口设成它


def _wechat_windows() -> list:
    wins = [w for w in find_windows(title='微信', class_prefix='Qt') if w.visible and not w.minimized]
    return [w for w in wins if window_rect(w.hwnd)[2] - window_rect(w.hwnd)[0] > 300]  # 跳过托盘菜单等小窗口


def find_wechat() -> int:
    """找微信主窗口，交回 hwnd。

    藏在托盘或最小化时，按微信自己的快捷键 Ctrl+Alt+W 唤出来——和人点托盘图标一样，是正常打开，
    不用 Win32 强行显示（强行显示后可能不响应）。
    """
    wins = _wechat_windows()
    if not wins:
        print('微信不在前面，按 Ctrl+Alt+W 唤出')
        press('ctrl+alt+w')
        time.sleep(1.5)
        wins = _wechat_windows()
    if not wins:
        sys.exit('没找到微信主窗口：Ctrl+Alt+W 没唤出来（快捷键被改了？先手动打开它）')
    return wins[0].hwnd


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')  # sys.exit 的中文提示走 stderr，不设会乱码
    if len(sys.argv) != 3:
        sys.exit('用法：python -m agent.core.graph 名字 "测试 1"')
    task = {'to': sys.argv[1], 'msg': sys.argv[2]}
    hwnd = find_wechat()
    # 动键鼠之前先确认微信真能响应：强行显示过的微信可能画面在、点了没反应
    activate(hwnd)
    ok, why = set_window_size(hwnd, *WIN_SIZE)
    if not ok:
        sys.exit(f'窗口大小不对，核对会不准：{why}')
    ok, why = check_responsive(hwnd)
    if not ok:
        sys.exit(f'微信窗口不能响应：{why}')
    graph = build_graph()
    # 每走一个节点算一次，绕圈多了会超过默认上限 25 报错，所以调大一点
    end = graph.invoke(new_state(task, hwnd), {'recursion_limit': 80})
    print(f"\n结束：{'通过' if end['done'] else '没通过'}，走了 {end['step_count']} 步，失败 {end['failures']} 次"
          f"{'，被规矩拦下' if not end['allow'] else ''}")


if __name__ == '__main__':
    main()
