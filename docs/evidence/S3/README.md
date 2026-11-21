# Agent S3 演示接入 · 离线交付

- **做完了什么**：接入原版 CLI、托盘运行和步骤回看，补齐离线测试。
- **结果好不好**：部分达标。S3 新测 21 项通过，D16 回归有 1 项卡在 Windows 凭据写入。
- **需要谁做什么**：Claude 在正常 Windows 登录会话复测凭据，并完成 A、B、C 三阶段真机通过线及连续验收。

## 改动

| 文件 | 内容 |
| --- | --- |
| `agent/s3/__init__.py`、`run_cli.py` | 核对本机模型配置后把凭据注入原版 CLI 子进程环境。提供单独的微信最小化和浅色恢复开关。 |
| `agent/s3/worker.py`、`runner.py`、`protocol.py`、`session.py` | 独立解释器运行、按字节收取事件、记录截图和日志、Job Object 停止兜底、等监听退出后启动及按原参数恢复。 |
| `agent/s3/panel.py`、`agent/desktop.py`、`agent/desktop_runner.py` | 托盘入口、等待与运行状态、窗口隐藏和回看、点击红圈。 |
| `agent/config.json`、`agent/demos/test_s3_{protocol,session,worker}.py` | S3 配置和离线用例。 |

分支 `codex/s3-demo`，起点和当前 HEAD 都是 `c960237a`。代码与本报告在工作树里。Git 无法写链接工作树的索引，所以没有本地提交。两份方案文档在开工时已是未跟踪文件。

```text
fatal: Unable to create 'D:/Newitem/端侧盒子/.git/worktrees/edge-agent-s3/index.lock': Permission denied
```

## 验证

在项目根目录的 PowerShell 会话运行。D16 按模块单独运行，避免测试间的替身互相影响。测试结果出现失败或错误时，以失败用例为准。测试代码使用合成输入，D16 托盘测试使用离屏 Qt 和假监听子进程。关闭 PowerShell 会话即可撤掉临时环境变量。

```powershell
# python 使用项目解释器。-B 禁止生成字节码缓存。-m unittest 运行测试框架。
# discover 查找用例，-s 指定演示目录，-p 限定 S3 文件，-v 逐项输出。
python -B -m unittest discover -s agent/demos -p 'test_s3_*.py' -v

# 设置 Python 输出编码，供 test_history 读子进程中文日志。
$env:PYTHONIOENCODING='utf-8'
# Qt 使用离屏平台，供 D16 托盘入口测试使用。
$env:QT_QPA_PLATFORM='offscreen'
# Get-ChildItem 枚举演示测试文件。Where-Object 排除新 S3 测试。
# python -B -m 按模块运行每个旧测试。不加 --qwen，保留离线路径。
foreach ($testModule in Get-ChildItem agent/demos -Filter 'test_*.py' | Where-Object { $_.BaseName -notlike 'test_s3_*' }) {
    $moduleName = 'agent.demos.' + $testModule.BaseName
    python -B -m $moduleName
}

# 用 unittest 的完整用例名单独重现凭据错误。
python -B -m unittest agent.demos.test_tray_entry.TestModelSettings.test_credential_round_trip
# git diff --check 只检查补丁中的空白问题。
git diff --check
```

- S3 新测 21 项通过、0 项失败。覆盖 CLI 参数和凭据来源、CLI 分支顺序与间隔、JSON 拆行、坐标和动作标签、假进程交接及红圈换算。
- D16 回归 244 项通过、1 项错误。所有变动的 Python 文件都通过语法编译，`git diff --check` 无报错。

| D16 模块（位于 `agent.demos`） | 通过 | 失败或错误 |
| --- | ---: | ---: |
| `test_act_route` | 43 | 0 |
| `test_anchor_check` | 35 | 0 |
| `test_d16_handoff` | 3 | 0 |
| `test_d16_reply_flow` | 10 | 0 |
| `test_d16_reply_sources` | 7 | 0 |
| `test_history` | 7 | 0 |
| `test_input_guards` | 29 | 0 |
| `test_mail_flow` | 9 | 0 |
| `test_reliable_reply` | 25 | 0 |
| `test_reply_tiers` | 10 | 0 |
| `test_send_button` | 46 | 0 |
| `test_send_flow` | 12 | 0 |
| `test_tray_entry` | 8 | 1 |

凭据失败单测原始输出如下。整组和单独执行均在同一处报错。调用 Windows 凭据管理器的代码和原用例无改动。

```text
E
======================================================================
ERROR: test_credential_round_trip (agent.demos.test_tray_entry.TestModelSettings.test_credential_round_trip)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "D:\Newitem\edge-agent-s3\agent\demos\test_tray_entry.py", line 96, in test_credential_round_trip
    secrets.put_key('sk-roundtrip', target)
  File "D:\Newitem\edge-agent-s3\agent\tools\secrets.py", line 42, in put_key
    raise ctypes.WinError(ctypes.get_last_error())
OSError: [WinError 1312] 指定的登录会话不存在。可能已被终止。

----------------------------------------------------------------------
Ran 1 test in 0.002s

FAILED (errors=1)
```

## 未验证项与实测重点

1. 从真实登录会话启动原版 CLI，核对预期的本机模型配置和凭据可读、DeepSeek 可收图、Spark 定位参数有效。微信最小化需按项目规则先通过响应检查。用任务 D 核对两个主题值能改回 1。
2. 托盘里用原监听参数启动、取消待启动、手动停止和退出。核对监听进程先退出、每轮最多恢复一次，以及停止后 2 秒内 worker 和子进程退出。特别看 Windows venv 启动器下 Job Object 的加入和回收。
3. 核对两窗隐藏、最后一步自动选中、红圈贴合实际点击。任务 B 和 D 各连续完成 3 次且每轮不超过 3 分钟，按实施方案核对界面和注册表的实际结果。阶段通过线由 Claude 实测。

## 风险与实现取舍

- 方案中的阶段 A、B 真机通过线按分工留给 Claude。未连接 DeepSeek 或 Spark，未启动托盘窗口，未执行 Agent S3 任务或操作桌面。
- 退出托盘时在 S3 收尾后直接走原退出流程，省去先恢复监听再马上停下的步骤。普通停止、取消和异常结束仍按原参数恢复。
- 思考事件附截图路径和原屏尺寸。完成、失败或跳过动作的步骤也保留截图，这样能回看最后一次模型看到的画面。
- 微信窗口检查沿用现有前台响应检查。微信已缩到任务栏或托盘时可以直接继续。若窗口可见但在后台，需先把微信切到前台再执行最小化开关。
- Worker 的 Python 日志和 stderr 分别保存在 `%LOCALAPPDATA%\edge-agent\logs`，原尺寸截图在 `%LOCALAPPDATA%\edge-agent\s3-runs`，演示后按方案清理。阶段 A 保留原版 CLI 自带的日志行为。源码显示它运行时会在 Agent-S 工作目录生成 `logs`。

## 证据路径

本报告保留命令、逐模块计数和完整失败堆栈。断言位于 `agent/demos/test_s3_*.py`。D16 离线运行方式取自 `docs/evidence/D16/README.md`，两个 D16/S3 工作树都没有 AGENTS.md 所列的交接笔记。
