# 端侧 AI 电脑操作助手

在 Windows 电脑上读屏幕和控件信息，自动操作电脑回复微信和 QQ 邮箱。模型跑在局域网的 DGX Spark 上：Qwen3.8-27B 负责看图和决定下一步，UI-TARS-1.5-7B 负责找点击位置。主程序跑在电脑上。

主循环是一张 LangGraph 图：**看 → 想 → 定位 → 规矩 → 做 → 核对**，没核对通过就绕回去重来。

## 进度

| 阶段 | 状态 |
|---|---|
| 工具层：截图、OCR、定位、键鼠、窗口、读网页控件树 | 完成 |
| 主循环（微信）：10 轮验收 | 通过，见 `docs/D8-acceptance.md` |
| 邮件核对和邮件分支 | 进行中 |

日程和每一步的通过线见 `docs/端侧AI操作盒子-计划书-v7.md`。

## 目录

| 路径 | 内容 |
|---|---|
| `agent/tools/` | 工具层，用法看 `agent/tools/__init__.py` 开头的说明 |
| `agent/core/` | 主循环：状态 `state.py`、图 `graph.py`、微信核对 `check.py`、邮件核对 `mail_check.py` |
| `agent/demos/` | 各工具的演示、手写版基线、发测试邮件的脚本 |
| `agent/practice/` | 学习练习 |
| `agent/config.json` | 模型服务地址 |
| `docs/` | 计划书、验收记录、交接笔记、学习笔记 |
| `experiments/` | 早期实验脚本（数据没有上传） |
| `learning/` | 学习记录，不是项目代码 |

## 跑起来

**1. 装依赖**（Windows，Python 3.12，在项目根目录）

```bash
python -m pip install -r requirements.txt
```

**2. 模型地址**：`agent/config.json` 里填的是内网 Spark 的地址，要在同一个局域网里才连得上。换成自己的 OpenAI 兼容服务也行。

**3. 白名单**：新建 `agent/test_contacts.local.json`。这个文件不上传，每人写自己的测试联系人。程序只会给名单里的会话和邮箱发消息。

```json
{
  "说明": "只放测试用的联系人",
  "allowed_chats": ["测试好友A", "测试好友B"],
  "allowed_mail": ["你的测试邮箱@qq.com"]
}
```

**4. 跑**

```bash
python -m agent.core.check
```

```bash
python -m agent.core.graph 测试好友A "测试 1"
```

- 第一条是离线测试，不动键鼠。
- 第二条会真的操作微信：先把微信主窗口打开（不要最小化），跑的时候别碰鼠标键盘。

**邮件**：QQ 邮箱要开在带无障碍参数的 Chrome 里，网页内容才读得到，参数见 `agent/tools/web.py` 开头。

## 安全规矩

- 只用测试账号，不碰真实工作群。只给白名单里的会话和邮箱发消息。
- 当前会话标题和目标一致，才允许输入或发送。
- 不按 Esc（微信会藏到托盘），不点关闭、最小化和网页搜索建议。
- 模型说"完成"不算数，由程序重新截图独立核对。
- 微信截图不留在项目里。
