"""主循环的图：看 → 想 → 定位 → 规矩 → 做 → 核对，没过就绕回去重来。

D6 第二步：「看」和「核对」换成真的，对着真微信截图、识别；「做」先不自动发，停下来请你手动发。
想、定位还是假的，规矩查的是真白名单。空图版（三条假路）在提交 ed404f55 里。

你要做的：填「看」「做」「核对」三个节点的 TODO（删掉 raise NotImplementedError）。
其他地方已经写好，不用改。

运行（在项目根目录，先在电脑上打开要发的那个会话）：
  python -m agent.core.graph 名字 "测试 1"
程序只截图、识别，不动键鼠。名字不在白名单里时，规矩会拦下，一步都不做。
"""
import json
import sys
from pathlib import Path

from langgraph.graph import StateGraph, START, END

from agent.core.check import count_msg, verify_sent
from agent.core.state import AgentState
from agent.tools import capture_window, find_windows, ocr, window_rect

MAX_STEPS = 5      # 最多走几步
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
    """
    print(f"看    （第 {state['step_count'] + 1} 步）")
    # TODO
    shot = capture_window(state['hwnd'])
    if shot is None:
        sys.exit('截图失败')
    items = ocr(shot.image)
    return {'picture':shot.image,'count_before':count_msg(items,state['task']['msg'])}

def think(state: AgentState) -> dict:
    """想：看着截图决定下一步做什么。以后这里叫 Qwen，输入是截图本身，不是文字。

    输出：{'action': 动作字典}，比如 {'type': 'click', 'target': '搜索框'}。
    """
    print("想")
    return {'action': {'type': 'click', 'target': '搜索框'}}


def locate(state: AgentState) -> dict:
    """定位：把动作里的 target 变成截图里的坐标。以后这里叫 UI-TARS。

    输出：{'location': [x, y]}。
    """
    print("定位")
    return {'location': [100, 100]}


def rules(state: AgentState) -> dict:
    """规矩：程序判断这一步许不许做，不问模型。

    输出：{'allow': True 或 False}。现在只查一条：task 里的 'to'（发给谁）在不在 WHITELIST 里。
    """
    print("规矩")
    return {'allow': state['task']['to'] in WHITELIST}


def act(state: AgentState) -> dict:
    """做：现在还不自动发，停下来请人在微信里手动发，发完按回车。

    输出：steps 和 step_count 两个键（和空图版一样）。
    提示：停下来等人按回车，用 input('提示文字')。提示里写清要发哪句话。
    """
    print("做")
    # TODO
    input(f"请在微信里输入{state['task']['msg']},发完请按回车")
    return {'steps':state['steps']+[{'action':state['action'],'location':state['location']}],'step_count':state['step_count'] +1}


def check(state: AgentState) -> dict:
    """核对：重新截一张图，程序独立判断发对没有，不信模型说的“完成”。

    输出：passed 和 failures 两个键。
    提示：
      - 一定要重新截图：state['picture'] 是「做」之前截的，看不到刚发的消息
      - 判断直接用 check.py 里的 verify_sent，它要三样东西：识别结果、task、count_before
      - 把 verify_sent 交回的原因打印出来，跑的时候才知道是哪条没过
    """
    # TODO
    shot = capture_window(state['hwnd'])
    if shot is None:
        sys.exit('截图失败')
    items = ocr(shot.image)
    alert = verify_sent(items,state['task'],state['count_before'])
    print(alert[1])
    if not alert[0]:
        failures = state['failures'] + 1
    return {'passed':alert[0],'failures':failures}

# ---------- 条件边：只选路，不改 state ----------

def after_rules(state: AgentState) -> str:
    """规矩之后走哪条路：允许就去「做」，不允许就停。"""
    if state['allow']:
        return 'act'
    return END


def after_check(state: AgentState) -> str:
    """核对之后走哪条路：通过、或步数/失败到上限就停，否则回到「看」重来。"""
    if state['passed'] or state['step_count'] >= MAX_STEPS or state['failures'] >= MAX_FAILURES:
        return END
    return 'look'


# ---------- 把节点和边连成图 ----------

def build_graph():
    """把 6 个节点和 2 个条件边连起来，交回编译好的图。"""
    g = StateGraph(AgentState)
    g.add_node('look', look)
    g.add_node('think', think)
    g.add_node('locate', locate)
    g.add_node('rules', rules)
    g.add_node('act', act)
    g.add_node('check', check)
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
    """开头的状态：11 个键都要有值，节点读不到键会报 KeyError。"""
    return {'task': task, 'steps': [], 'step_count': 0, 'failures': 0,
            'picture': None, 'action': {}, 'location': [], 'allow': False, 'passed': False,
            'hwnd': hwnd, 'count_before': 0}


def find_wechat() -> int:
    """找微信主窗口，交回 hwnd。藏在托盘或最小化的不强行打开（强行显示后可能不响应）。"""
    wins = [w for w in find_windows(title='微信', class_prefix='Qt') if w.visible and not w.minimized]
    wins = [w for w in wins if window_rect(w.hwnd)[2] - window_rect(w.hwnd)[0] > 300]  # 跳过托盘菜单等小窗口
    if not wins:
        sys.exit('没找到能截图的微信主窗口（藏在托盘或最小化了？先手动打开它）')
    return wins[0].hwnd


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')  # sys.exit 的中文提示走 stderr，不设会乱码
    if len(sys.argv) != 3:
        sys.exit('用法：python -m agent.core.graph 名字 "测试 1"')
    task = {'to': sys.argv[1], 'msg': sys.argv[2]}
    graph = build_graph()
    # 每走一个节点算一次，绕圈多了会超过默认上限 25 报错，所以调大一点
    end = graph.invoke(new_state(task, find_wechat()), {'recursion_limit': 50})
    print(f"\n结束：{'通过' if end['passed'] else '没通过'}，走了 {end['step_count']} 步，失败 {end['failures']} 次"
          f"{'，被规矩拦下' if not end['allow'] else ''}")


if __name__ == '__main__':
    main()
