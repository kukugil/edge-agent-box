# 托盘虚拟形象：KuroCat 接入方案（v2，11/24 Claude，已经 Codex 只读审核）

## 定稿（v2，覆盖下文 v1 里冲突的地方）

- **接到 `edge-agent-s3` 的 `codex/tray-v2`**。主工作树托盘没有监听开关；tray-v2 有监听状态入口（`main_window.py:401` 监听变化、`:488` 刷新状态、`:620` 汇总待办）。
- **状态只分三种**：没开 / 监听中 / 有转人工待处理。"正在发送"没有独立信号，首版不做。
- **状态用耳机和项圈表达，不用高亮色**：Codex 实测 `accentColor` 只在按键时亮，猫静止时看不出来。耳机、项圈是 KuroCat 的全局设置（`update_settings` 的 `headphoneMode`、`collarColor`），我们只用一只猫，所以没关系。皮肤只在启动时装一次，运行中不反复 `upsert_cat`（可能每次写盘重绘）。
- **穿透锁不能当安全保证**：微信那边点击前查的是 `WindowFromPoint`（`wechat_uia.py:254`），截图走 `PrintWindow`（`computer/worker.py:286`、`tools/window.py:238`）。实验 3 要分别验证 UIA 命中、窗口命中、实际点击三项，外加截图里有没有猫；通过线是零误点、OCR 和定位结果和没开猫时一样。没通过就退一步：自动运行期间把猫挪到不和微信、邮箱窗口重叠的位置，挪不开就隐藏。
- **挪窗口**：`SetWindowPos` 先测 5 次，再补测重启 KuroCat 后、托盘拖到另一块屏后各 1 次。
- **退出恢复**：托盘退出时把 `catBreed`、`headphoneMode`、`collarColor`、`clickThroughLocked` 改回启动前的值。
- **长期**：KuroCat 闭源，不进盒子交付版。Codex 建议的"在书房场景里放一张原创猫图、随镜头动"作为方案 D 的第一步，演示后另排。
- **执行顺序**：① 用户定形象，装静态皮肤（只调接口，不改代码）→ ② 跑四个最小实验 → ③ 过线后写 `agent/ui/pet_bridge.py`（后台线程、1 秒超时、状态去重、KuroCat 不在就什么也不做）和 `agent/assets/pet/edge-cat.json`，`main_window.py` 只在上面三个挂点各加一行 → ④ 假服务离线测试：请求内容、超时不卡界面、KuroCat 不在时托盘正常、退出恢复。

---

# 草案 v1

## 一、核实到的事实（11/24 01:30 本机）

| 项 | 结论 | 怎么查到的 |
|---|---|---|
| 程序 | KuroCat 5.0.1，`%LOCALAPPDATA%\KuroCat\KuroCat.exe`，单文件 4.6MB，Tauri + WebView2，闭源 | 安装目录、`com.kurocat.pet\EBWebView`；本机和网上都没找到源码 |
| 形象 | 一只跟着键盘、鼠标动的猫（按键冒泡、按下的键高亮），3D 渲染 | `settings.json`：`showKeyBubbles`、`renderMode: 3d` |
| 控制接口 | `POST http://127.0.0.1:27420/`，JSON-RPC 格式的 MCP，只听本机 | `GET /` 返回 `{"name":"kurocat","mcpEndpoint":"/mcp"}`，实际 `/mcp` 是 404，根路径才通 |
| 能调的 7 个工具 | `list_cats`、`get_cat_schema`、`upsert_cat`、`switch_cat`、`delete_cat`、`get_settings`、`update_settings` | `tools/list` |
| 能自己设计到什么程度 | 只能改**毛色、花纹（6 种）、眼睛、耳朵、尾巴、胡须、斑点、表情符号、项圈、耳机**。猫的身形和动作改不了 | `get_cat_schema`；改完即时生效 |
| 改不了的 | 没有"说话气泡写字""摆表情/动作""移动位置"的接口。位置只存在 `settings.json` 的 x、y | `update_settings` 参数表里没有 x、y |
| 托盘的 Qt | 托盘用 `%LOCALAPPDATA%\edge-agent\desktop-deps` 里的 PySide6-Essentials 6.12，**没有 WebEngine**，嵌不了网页 | 查 `QtWebEngineWidgets.pyd` 不存在 |
| OBS 模式 | 有 `obsEnabled`/`obsPort 27421`，现在关着，不清楚给出什么页面 | `settings.json` |

**结论**：KuroCat 只能作为"独立的透明小窗 + 远程换装"，不能真正塞进托盘窗口里；"自己设计形象"在它里面就是设计一套猫的皮肤。

## 二、几种接法

| 方案 | 做法 | 判断 |
|---|---|---|
| A 伴随窗口（推荐首版） | KuroCat 保持自己的小窗；托盘启动时装上我们设计的猫，按托盘状态换装（换高亮色、项圈、耳机），并把猫窗挪到托盘窗口角上"坐着" | 改动最小，坏了不影响托盘；猫会跟着程序自己的键鼠动作动，"猫在帮你打字"效果是白送的 |
| B 把猫窗塞进托盘 | 用 `SetParent` 把别的进程的 WebView2 窗口挂到 Qt 控件里 | 跨进程挂 WebView2 不受支持，透明和重绘容易坏，不做 |
| C 嵌 OBS 页面 | 打开 OBS 模式，托盘用 QWebEngineView 显示 27421 的页面 | 要装几百 MB 的 PySide6-Addons，页面内容未知，先放着 |
| D 自己画形象 | 在托盘书房场景里用 Qt 自己画一个角色 | 完全可控、以后能搬到 RK3588；工作量大，作为长期方向 |

## 三、方案 A 的具体内容

1. **我们的猫**：`agent/assets/pet/edge-cat.json`，一份 `upsert_cat` 用的皮肤（id `edge-box-cat`）。配色跟托盘书房纸色走（暖米色底 + 深棕描边），名字和表情符号由用户定。
2. **状态 → 外观**（只换颜色类字段，不换猫，避免闪）：

   | 托盘状态 | 外观 |
   |---|---|
   | 没开始 / 已停止 | 默认配色，`headphoneMode: off` |
   | 监听中 | 高亮色绿，戴耳机（"在听"） |
   | 正在发送 | 高亮色橙 |
   | 转人工待处理 | 高亮色红 + 红项圈 |
   | KuroCat 没开 | 什么都不做，托盘照常 |

3. **新代码**：`agent/ui/pet_bridge.py`，一个小类：`available()`、`ensure_cat()`、`show_state(state)`、`dock_to(rect)`。所有调用 1 秒超时、在后台线程、失败只记日志不弹窗；状态没变不重复调用。托盘只在已有的状态变化处调一行。
4. **摆位置**：按进程找到 KuroCat 顶层窗口，用 `SetWindowPos` 挪到托盘窗口右下角外侧；托盘窗口移动时跟着挪。
5. **离线测试**：假的 27420 服务，验证换装请求内容、超时不卡界面、KuroCat 不在时托盘正常。

## 四、必须守住的安全点

- **猫窗不能挡住微信和邮箱**：它是置顶窗口。挡住目标时，点击前的 ElementFromPoint 回查会落到猫身上，程序会拒绝点击（安全但失败）；截图里有猫也会干扰 OCR 和 UI-TARS 定位。所以自动运行期间：猫只停在托盘窗口范围内，并打开 `clickThroughLocked`；猫窗和微信窗口有重叠时，先把猫挪开再操作。
- **按键冒泡**：KuroCat 会显示正在按的键。程序回复走剪贴板粘贴，屏幕上只会冒出 Ctrl+V 和回车，不会露出回复内容；用户自己输入密码时有它自带的 `privacyPasswordGuard`（已开）。
- 不改 KuroCat 的安装文件，只通过接口改皮肤和设置；退出托盘时把设置恢复原样（记住原来的 `catBreed`、`clickThroughLocked`）。

## 五、先做的最小实验（决定方案 A 能不能成）

| # | 验证什么 | 通过线 |
|---|---|---|
| 1 | `SetWindowPos` 能挪动猫窗，KuroCat 不把它弹回去 | 挪 5 次，5 次停在目标位置 |
| 2 | 只改颜色字段的 `upsert_cat` 延迟和闪烁 | 连换 10 次，每次 < 300ms，肉眼不闪 |
| 3 | `clickThroughLocked` 打开后，猫窗下面的微信控件 ElementFromPoint 能取到 | 5 个点全取到微信控件 |
| 4 | KuroCat 关掉时托盘启动、开始、停止都正常 | 3 次全正常 |

## 六、待定

- 接到哪个托盘：主工作树 `codex/ufo2-desktop-assistant`（现在在跑的 `agent.desktop`），还是 `edge-agent-s3` 的 `codex/tray-v2`（书房场景）。
- 今天 11/24 演示：这件事不挡演示。建议演示后再做；演示前最多只装上一套静态皮肤（不改托盘代码）。
- KuroCat 闭源，许可不明，只适合本机演示，不能进盒子交付版；长期走方案 D。
