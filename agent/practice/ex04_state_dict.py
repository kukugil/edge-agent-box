"""练习 4：用字典当 agent 的状态。

这里用普通字典模拟 LangGraph 的规则，先不接模型、不碰窗口：节点读整个状态，只交回改动的部分，再合并进去。
讲解见 learning/lessons/0003-state-as-dict.html。

你要做的：把下面 3 个函数里的 TODO 填上（删掉 raise NotImplementedError）。其他地方已经写好，不用改。

运行（在项目根目录）：python -m agent.practice.ex04_state_dict
成功时最后一行打印：核对通过。
"""
import sys
from typing import TypedDict

MAX_STEPS = 5      # 最多走几步
MAX_FAILURES = 2   # 最多失败几次


class AgentState(TypedDict):
    task: str          # 这次要做的事
    steps: list[dict]  # 走过的每一步：{'action': 动作, 'ok': 成功没有}
    step_count: int    # 走了几步
    failures: int      # 失败几次


def apply_update(state: AgentState, update: dict) -> AgentState:
    """模拟 LangGraph 的默认合并：update 里有的键覆盖旧值，没有的键不动。已写好。"""
    return {**state, **update}


def last_action(state: AgentState) -> str | None:
    """取最后一步的动作。

    输出：动作文字；一步都没走时返回 None。
    提示：先判断 steps 是不是空的，再用 [-1] 取最后一条。
    """
    if not state['steps']:
        return None
    return state['steps'][-1]['action']

def record_step(state: AgentState, action: str, ok: bool) -> dict:
    """记下一步。这就是一个节点：读 state，只交回改动的部分。

    输出：只含 steps、step_count、failures 三个键的字典。
    提示：
      1. 新的一步是 {'action': action, 'ok': ok}
      2. 新的 steps 要包含旧的全部步骤和这一步。用 + 拼出新列表，不要 append，append 会改掉旧状态
      3. step_count 加 1；failures 只在 ok 是 False 时加 1
    """
    new_step = {'action':action,'ok':ok}
    new_failures = state['failures']
    if not ok:
        new_failures = state['failures']+1
    return {
        'steps':state['steps']+[new_step],
        'step_count':state['step_count']+1,
        'failures':new_failures
    }

def should_stop(state: AgentState) -> bool:
    """判断要不要停。

    输出：步数到了 MAX_STEPS，或者失败到了 MAX_FAILURES，返回 True；否则返回 False。
    """
    return state['step_count'] >= MAX_STEPS or state['failures'] >= MAX_FAILURES


def check(start: AgentState, end: AgentState, update: dict) -> None:
    """核对练习结果。已写好。"""
    if set(update) != {'steps', 'step_count', 'failures'}:
        sys.exit(f'record_step 要只交回 3 个键，现在交回的是：{sorted(update)}')
    if start['steps']:
        sys.exit('旧状态的 steps 被改了：是不是用了 append？')
    if len(end['steps']) != 3:
        sys.exit(f"应该记下 3 步，现在是 {len(end['steps'])} 步：是不是把旧步骤覆盖掉了？")
    if end['task'] != start['task']:
        sys.exit('task 被改了：节点不该动它')
    if (end['step_count'], end['failures']) != (3, 2):
        sys.exit(f"应该是走 3 步、失败 2 次，现在是 {end['step_count']} 步、{end['failures']} 次")
    if last_action(start) is not None or last_action(end) != '输入联系人':
        sys.exit('last_action 不对：没有步骤时要返回 None，有步骤时要返回最后一步的动作')
    print('核对通过')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    start: AgentState = {'task': '给测试好友发消息', 'steps': [], 'step_count': 0, 'failures': 0}
    state = start
    update = {}
    plan = [('点搜索框', True), ('输入联系人', False), ('输入联系人', False), ('按回车', True)]
    for action, ok in plan:
        if should_stop(state):
            print('停下')
            break
        update = record_step(state, action, ok)
        state = apply_update(state, update)
        print(f"第 {state['step_count']} 步：{last_action(state)}，失败 {state['failures']} 次")
    check(start, state, update)


if __name__ == '__main__':
    main()
