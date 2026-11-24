# 电脑助手与托盘换新验收记录

**做完了什么**　接通 UI-TARS 电脑助手、三页托盘主窗口和手动恢复自动回复。  
**结果好不好**　离线达标。新增测试 37 项通过，既有测试 16 个入口通过。  
**需要谁做什么**　Claude 负责真机验收和提交。

## 改动

- `agent/computer/uitars.py` 实现提示词、图片历史、动作解析、落点换算和快捷键映射。`worker.py` 用独立解释器运行，原图与日志留在 `%LOCALAPPDATA%\edge-agent\computer\runs\`。
- `agent/computer/runner.py`、`protocol.py`、`session.py` 实现进程交接、停止标志和强杀回退。收到 `done` 后等 worker 退出。用户点“完成，恢复自动回复”才消费保存的监听参数。
- `agent/ui/main_window.py`、`computer_page.py` 和 `agent/desktop.py` 接通微信、邮件、电脑助手三页，保留模型设置、托盘菜单、角标、事项详情和单实例入口。邮箱事项继续读同一本账。
- `agent/config.json` 增加 `computer` 配置。`pad_top` 默认 `0`，`s3` 段保留。`agent/core/`、`agent/s3/` 和 `agent/desktop_runner.py` 的 Git 差异为空。
- `agent/demos/test_computer_uitars.py`、`test_computer_worker.py`、`test_computer_session.py`、`test_computer_window.py` 是按方案和摘录编写的新用例。覆盖第 7 轮的图片裁剪、解析和落点、25 步、真实 Qt 管道配合合成 Python 子进程、停止和恢复时序、三页与待处理事项。

## 验证

在 `D:\Newitem\edge-agent-s3` 的 PowerShell 执行。测试应以 `OK` 或退出码 `0` 收尾。参数互斥检查预期退出码 `2`。若出现意外错误，保留输出并停在离线环境检查。关闭这次 PowerShell 会话可撤销临时环境变量。

```powershell
# offscreen 使用 Qt 离屏平台。PYTHONIOENCODING 让 test_history 的子进程用 UTF-8 输出。
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONIOENCODING='utf-8'
# python 使用当前测试解释器。-B 不写字节码。-m 按模块运行。
# unittest discover 查找测试。-s 指定目录，-p 只匹配新增测试，-v 逐项输出。
python -B -m unittest discover -s agent/demos -p 'test_computer_*.py' -v
# -E 忽略 Python 环境配置。-X utf8 明确启用 UTF-8。--help 只检查 worker 脚本入口。
python -B -E -X utf8 agent/computer/worker.py --help
# --listen 要求立即监听。--reply-db 指定测试账本。两者同用应在建窗口前被拒绝。
python -B -m agent.desktop --listen --reply-db synthetic.db
# git 的 -c 只覆盖本次命令的换行设置。diff --check 检查已跟踪改动的空白。
git -c core.autocrlf=false diff --check
```

既有测试逐模块使用 `python -B -m agent.demos.<模块>`，避免旧脚本互相污染。各子进程用临时 `LOCALAPPDATA`，并通过 `PYTHONPATH` 保留本机的 PySide6 路径。有两处例外，具体入口和完整测试输出见下表及 [regression.log](regression.log)。

- `test_anchor_check` 用 `python -B -c "from unittest.mock import patch; import runpy; patch('agent.core.trigger.notify', return_value=True).start(); runpy.run_module('agent.demos.test_anchor_check', run_name='__main__')"`。`-c` 执行这一段 Python，用替身接收通知，`run_module` 继续执行原测试。
- `test_tray_entry` 用 `python -B -m unittest` 分别指定 `TestListenEntry`、`TestRunner` 和 `TestModelSettings` 的 `test_user_model_overrides_default_and_key_goes_only_to_that_url`、`test_bad_settings_are_refused`、`test_broken_user_file_falls_back_to_default`。每个名字加上 `agent.demos.test_tray_entry.` 前缀，凭据往返交给 Claude。

| 测试 | 通过 | 失败 |
| --- | ---: | ---: |
| 新增测试 | 37 项 | 0 |
| `test_d16_handoff`、`test_d16_reply_flow`、`test_d16_reply_sources`、`test_history` | 3 + 10 + 13 + 7 项 | 0 |
| `test_anchor_check`、`test_mail_flow`、`test_reliable_reply` | 35 + 9 + 25 项 | 0 |
| `test_tray_entry`、`test_s3_protocol`、`test_s3_session`、`test_s3_worker` | 8 + 9 + 7 + 5 项 | 0 |
| `test_input_guards`、`test_reply_tiers`、`test_send_flow` | 29 + 10 + 12 项 | 0 |
| `test_act_route`、`test_send_button` | 2 个入口均输出“全部通过” | 0 |

首次用一个 `unittest discover` 发现所有旧脚本时，两个脚本导入后直接执行并以 `SystemExit: 0` 退出，还留下全局测试替身。该次命令也误运行了真实凭据往返，并调用了旧锚点用例的 Windows 通知路径。凭据写入被系统拒绝。旧通知代码启动了隐藏的 PowerShell 进程，气泡是否显示未核实。隔离复测屏蔽了通知、排除了凭据往返，其余旧测试全部通过。首次测试的原始失败内容保存在 [regression.log](regression.log)。

## 未验证项与偏离

- `type` 按 v2 修正引号、逗号、中文、转义和末尾回车。非空但解析不出的动作按摘录作空动作处理。模型整条回复无有效文本则显示“解析失败”。
- `thinking` 默认发送摘录中的 `disabled`，可通过 `computer.thinking=false` 省略此字段。是否需要关闭由 Spark 单步冒烟决定。
- `scroll_clicks=4` 是待校准值。NutJS 摘录调用 `scrollUp/Down(500)`，PyAutoGUI 使用滚轮格数。实际滚动距离尚未对齐。拖拽速度也需要比较。
- 正常停止可在等待和动作边界清理按键与剪贴板文本。同步模型请求最长可等 30 秒，用户停止时 Runner 会在 5 秒后走强杀回退。操作系统强杀会跳过 Python 清理。
- 本轮未启动产品托盘、未接 Spark、未运行 Agent-S 环境的真实截图或键鼠。分支 `codex/tray-v2` 起点 `3168fe67`。本轮未提交。

## Claude 真机重点

1. 单屏 100% 缩放下用同一张截图对照 UI-TARS Desktop 的请求、动作和实际落点。先确认 Spark 接收 `thinking`。用 Chrome 地址栏、当前标签、关闭标签按钮每个目标各请求三次，对照 `pad_top=0` 和 `56`。只有偏差超过 10px 且补边能修正才调整默认值。
2. 用 B 站页面对齐滚动距离，检查拖拽、中文输入和终止时的状态。用实际 Windows 进程确认等待请求期间停止、Job 回收、剪贴板与松键表现。
3. B 站和深色模式各连续成功 3 次，每次不超过 3 分钟。程序分别核对 Chrome 停在视频播放页、注册表两个主题值为 `0`。录备用视频。
4. 用测试账号回归微信、QQ 邮箱、托盘角标、单击和双击、单实例锁、模型设置与凭据往返。确认退出先停助手。任务结束后确认打开结果页并选中最后一步。点恢复后核对监听参数、恢复次数和窗口焦点。

## 11/21 真机冒烟（Claude）

脚本只请求、解析，不执行动作。用的是 `agent.computer.uitars` 的提示词、消息拼装、请求参数和坐标换算。标准答案来自 Chrome 的 UIA 控件矩形。屏幕 1920×1080，DPI 100%，Chrome 最大化。

| 目标 | 白边 0px | 白边 56px |
|---|---|---|
| 地址栏，框 (157,51)–(1734,75) | 3/3 在框内，纵向偏差 −4px | 3/3 在框内，纵向偏差 +2~+6px |
| 当前标签，框 (504,0)–(760,41) | 3/3 在框内，纵向偏差 +0.5px | 3/3 在框内，纵向偏差 +0.5px |
| 标签关闭按钮，框 (718,6)–(746,34) | 3/3 在框内，纵向偏差 +1px | 3/3 在框内，纵向偏差 +1px |

- Spark 接受 `thinking: disabled` 参数，`stream: false` 正常。单次请求 1.5–3.2 秒。
- 结论：全屏原图时，贴顶边的元素**没有**偏下的问题。旧实验里偏 28px，应该和截局部图、压缩到约 100 万像素有关。`pad_top` 保持 0。

## 11/21 操作演示层门槛实验（Claude）

方案：`C:\Users\Administrator\.claude\plans\buzzing-tinkering-thompson.md`（v2，已经 Codex 只读审核）。测试效果层：PySide6 全屏透明窗口，`WindowTransparentForInput` + `WA_TranslucentBackground`，扩展样式 `0x80800a8`（分层 + 穿透）。截图用 worker 同一个解释器（Agent-S `.venv`）的 `pyautogui.screenshot()`，另用 `ImageGrab.grab(include_layered_windows=True)` 近似录屏软件。

| 项目 | 结果 |
|---|---|
| 点击穿透（脚本点击，落在透明区和圆环线上） | 通过：3 轮计数 +3、+2、+2（第一轮多 1 次，原因未查），前台窗口不变 |
| 不设 WDA，worker 截图 | **能截到效果层**（品红采样点 10019，与录屏式截法相同） |
| `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`，显示后立刻 / 1 秒后调用 | **失败**：返回 0，错误码 8，实际亲和值仍为 0，截图照样有效果层 |
| `WDA_MONITOR`，1 秒后调用 | 同样失败，错误码 8 |

结论：按方案判定"两种都截得到"，停止，不启用特效。WDA 在 Qt 透明分层窗口上设不上（推测与 Qt 用 UpdateLayeredWindow 画透明窗有关，未查证）。
