"""主循环的图：看 → 想 → 定位 → 规矩 → 做 → 核对，没过就绕回去重来。

D6 第一步先搭“空图”：每个节点不碰微信、不叫模型，只打印一句、交回一两个假值。
目的是先把走向连对，下一步再把节点一个个换成调用 agent.tools。

你要做的：把带 TODO 的地方填上（删掉 raise NotImplementedError）。
「看」节点已经写好，当示范；main 和 check 已经写好，不用改。

运行（在项目根目录）：python -m agent.core.graph
成功时最后一行打印：三条路都走对了。
"""
import sys

from langgraph.graph import StateGraph, START, END

from agent.core.state import AgentState

MAX_STEPS = 5      # 最多走几步
MAX_FAILURES = 3   # 最多失败几次

# 假白名单。以后换成读 agent/test_contacts.local.json，名字不写进代码。
WHITELIST = ['测试好友A', '测试好友B']


# ---------- 节点：读整个 state，只交回改动的键 ----------

def look(state: AgentState) -> dict:
    """看：截一张图。

    输入：整个状态。
    输出：{'picture': 截图}。现在先交一个假字符串，以后换成 capture_window。
    """
    print(f"看    （第 {state['step_count'] + 1} 步）")
    return {'picture': '假截图'}


def think(state: AgentState) -> dict:
    """想：看着截图决定下一步做什么。以后这里叫 Qwen，输入是截图本身，不是文字。

    输出：{'action': 动作字典}，比如 {'type': 'click', 'target': '搜索框'}。
    """
    # TODO: 打印一句「想」，交回一个假动作
    print(f"想")
    return  {'action': {'type': 'click', 'target': '搜索框'}}
    
def locate(state: AgentState) -> dict:
    """定位：把动作里的 target 变成截图里的坐标。以后这里叫 UI-TARS。

    输出：{'location': [x, y]}。
    """
    # TODO: 打印一句「定位」，交回一个假坐标
    print(f"定位")
    return  {'location':[100,100]}

def rules(state: AgentState) -> dict:
    """规矩：程序判断这一步许不许做，不问模型。

    输出：{'allow': True 或 False}。
    提示：现在只查一条——task 里的 'to'（发给谁）在不在 WHITELIST 里。
    """
    # TODO
    print("规矩")
    return {'allow':state['task']['to'] in WHITELIST}

def act(state: AgentState) -> dict:
    """做：执行动作。现在不真点，只记一步。

    输出：steps 和 step_count 两个键。
    提示：和练习 4 的 record_step 一样，用 + 拼新列表，不要 append。
         新的一步可以记成 {'action': state['action'], 'location': state['location']}。
    """
    # TODO
    print("做")
    return {'step_count':state['step_count']+1,'steps':state['steps']+[{'action': state['action'], 'location': state['location']}]}

def check(state: AgentState) -> dict:
    """核对：程序独立判断这一步成没成功，不信模型说的“完成”。

    输出：passed 和 failures 两个键。
    提示：现在用假的判断——task 里的 'fake_pass_at' 不是 None，
         并且 step_count 已经到了它，就算通过；否则不通过，failures 加 1。
    """
    # TODO
    if state['task']['fake_pass_at'] is not None:
        if state['step_count'] >= state['task']['fake_pass_at']:
            return {'passed':True,'failures':state['failures']}
    return {'passed':False,'failures':state['failures']+1}

# ---------- 条件边：只选路，不改 state ----------

def after_rules(state: AgentState) -> str:
    """规矩之后走哪条路。

    输出：允许就返回 'act'，不允许返回 END。
    """
    # TODO
    if state['allow']:
        return 'act'
    return END

def after_check(state: AgentState) -> str:
    """核对之后走哪条路。

    输出：通过了返回 END；没过但步数或失败次数到上限，也返回 END；否则返回 'look' 重来。
    """
    # TODO
    if state['passed'] or state['step_count'] >= MAX_STEPS or state['failures'] >= MAX_FAILURES:
        return END
    return 'look'


# ---------- 把节点和边连成图 ----------

def build_graph():
    """把 6 个节点和 2 个条件边连起来，交回编译好的图。

    提示：
      - 加节点：g.add_node('名字', 函数)
      - 固定的边：g.add_edge('从', '到')，起点用 START
      - 选路的边：g.add_conditional_edges('从', 选路函数)
        选路函数返回什么名字，就走到哪个节点
    """
    g = StateGraph(AgentState)
    g.add_node('look', look)
    # TODO: 加剩下 5 个节点
    g.add_node('think',think)
    g.add_node('locate',locate)
    g.add_node('rules',rules)
    g.add_node('act',act)
    g.add_node('check',check)
    g.add_edge(START, 'look')
    # TODO: 连 look → think → locate → rules，再接两个条件边
    g.add_edge('look','think')
    g.add_edge('think','locate')
    g.add_edge('locate','rules')
    g.add_edge('act','check')
    g.add_conditional_edges('rules',after_rules)
    g.add_conditional_edges('check',after_check)
    return g.compile()


# ---------- 下面已经写好 ----------

def new_state(task: dict) -> AgentState:
    """开头的状态：11 个键都要有值，节点读不到键会报 KeyError。"""
    return {'task': task, 'steps': [], 'step_count': 0, 'failures': 0,
            'picture': None, 'action': {}, 'location': [], 'allow': False, 'passed': False,
            'hwnd':None,'count_before':0}


def verify(name: str, end: AgentState, want: dict) -> None:
    """核对一条路跑完后的状态。"""
    got = {k: end[k] for k in want}
    if got != want:
        sys.exit(f'{name} 不对：应该是 {want}，实际是 {got}')
    print(f'{name}：对\n')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')  # sys.exit 的中文提示走 stderr，不设会乱码
    graph = build_graph()
    # 每走一个节点算一次，绕圈多了会超过默认上限 25 报错，所以调大一点
    config = {'recursion_limit': 50}

    print('== 路 1：第 2 步核对通过 ==')
    end = graph.invoke(new_state({'to': '测试好友A', 'msg': '测试 1', 'fake_pass_at': 2}), config)
    verify('路 1', end, {'passed': True, 'step_count': 2, 'failures': 1})

    print('== 路 2：一直不通过，失败到上限停下 ==')
    end = graph.invoke(new_state({'to': '测试好友A', 'msg': '测试 1', 'fake_pass_at': None}), config)
    verify('路 2', end, {'passed': False, 'step_count': MAX_FAILURES, 'failures': MAX_FAILURES})

    print('== 路 3：发给白名单外的人，规矩拦下 ==')
    end = graph.invoke(new_state({'to': '某个群', 'msg': '测试 1', 'fake_pass_at': 1}), config)
    verify('路 3', end, {'allow': False, 'step_count': 0})

    print('三条路都走对了')


if __name__ == '__main__':
    main()
