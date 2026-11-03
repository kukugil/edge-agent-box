from typing import TypedDict


class AgentState(TypedDict):
    task: dict          # 这次要做的事
    steps: list[dict]  # 走过的每一步
    step_count: int    # 走了几步
    failures: int      # 失败几次
    picture: object    # 截图
    action: dict       # 下一步的动作
    location: list      # 截图坐标
    allow:  bool       # 步骤是否允许
    passed: bool       # 程序是否通过