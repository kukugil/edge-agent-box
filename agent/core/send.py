"""把一句已经定好的回复发出去：固定流程 + 适配器（D15 方案二）。

为什么不再让模型看图决定步骤：回微信只有"确认会话 → 粘贴 → 读回 → 回车 → 核对"这几步，顺序是死的。
11/12 晚上让模型每步看截图决定，它看到聊天里有一句一样的旧话就说"做完了"，前后卡了 5 次。
步骤写死在 deliver 里，模型只负责写回复内容（reply.py）。

两种适配器，流程只有一份：
  WeChatUIA（agent/tools/wechat_uia.py）  控件树：会话选中状态、输入框的值都能直接读，主路
  VisionWeChat（本文件）                  看图：OCR 认标题、点输入框、剪贴板读回，控件树读不到时兜底，
                                           也给以后没有控件树的软件做样子
适配器要提供：kind、open、confirm、input_text、write、clear、focus_input、input_focused、count_mine。

安全规矩不变：标题（会话）对上才粘贴和回车；框里有别人的字不动；粘完读回一字不差才按回车；
按完由程序核对消息列表多出这一条，不信任何"完成"。没把握就不发、交给人。
"""
import time

from agent.core import messages
from agent.core.check import count_msg, count_ours, layout
from agent.tools import capture_window, journal, norm, press, read_focused_text

OK, RETRY, UNSURE, HUMAN = 'ok', 'retry', 'unsure', 'human'
CHECK_TRIES = 6        # 按完回车后核对几次，每次隔 0.7 秒：控件树一般 1 秒内就能看到新消息


class VisionWeChat:
    """看图适配器：只用截图、OCR、鼠标键盘。慢一些，也看不到焦点，所以读回那关更严。"""

    kind = 'vision'

    def __init__(self, hwnd: int):
        self.hwnd = hwnd

    def open(self, title: str) -> bool:
        from agent.core.trigger import open_chat       # 晚导入：trigger 导入了本文件
        return open_chat(self.hwnd, title) is not None

    def title_ok(self, title: str) -> bool:
        # 连认两帧都对才算：整张认标题时好时坏（11/11、11/12），裁小块再加一帧更稳
        from agent.core.trigger import open_title
        for _ in range(2):
            shot = capture_window(self.hwnd)
            t = open_title(shot) if shot is not None else ''
            if not (t and norm(t) == norm(title)):
                return False
        return True

    def input_text(self) -> str | None:
        # 坑：交回 '' 分不清"框是空的"和"焦点不在框里"，所以写完必须读回到整句才算数
        return read_focused_text()

    def write(self, text: str) -> bool:
        import uiautomation as auto
        auto.SetClipboardText(text)
        time.sleep(0.05)
        press('ctrl+v')
        time.sleep(0.2)
        return True

    def clear(self) -> bool:
        press('ctrl+a')
        press('delete')
        time.sleep(0.15)
        return read_focused_text() == ''

    def focus_input(self) -> bool:
        from agent.core.check import LIST_RIGHT
        from agent.tools import click
        shot = capture_window(self.hwnd)
        if shot is None:
            return False
        lay = layout(shot.image)
        click(*shot.to_screen((LIST_RIGHT + lay['right']) // 2, lay['bottom'] + 60))   # 分隔线下 50 像素
        time.sleep(0.3)
        return True

    def input_focused(self) -> bool | None:
        return None                       # 看图看不到焦点

    def count_mine(self, title: str, msg: str) -> int | None:
        from agent.core.trigger import chat_items
        shot = capture_window(self.hwnd)
        if shot is None or not self.title_ok(title):
            return None
        return count_msg(chat_items(shot), msg, layout(shot.image))


def _uia_count(hwnd: int, title: str, msg: str) -> int | None:
    r = messages.read_chat(hwnd, title)
    return count_ours(r['msgs'], msg) if r['ok'] and r['source'] == 'uia' else None


def pick_adapter(hwnd: int, force_vision: bool = False):
    """输入窗口和是否强制看图，输出这次用的适配器。控件树读得到就用控件树。"""
    if not force_vision:
        from agent.tools.wechat_uia import WeChatUIA
        a = WeChatUIA(hwnd)
        if a.available():
            # 发前发后都按控件树数我方消息，核对口径一致
            a.count_mine = lambda title, msg: _uia_count(hwnd, title, msg)
            a.title_ok = lambda title: _uia_title_ok(a, hwnd, title)
            return a
    return VisionWeChat(hwnd)


def _uia_title_ok(a, hwnd: int, title: str) -> bool:
    # 控件树说选中了还不够：再裁标题条认一眼，读得清又对不上就不认（两样证据都不矛盾才算）
    if not a.selected(title):
        return False
    from agent.core.trigger import open_title
    shot = capture_window(hwnd)
    t = open_title(shot) if shot is not None else ''
    return not t or norm(t) == norm(title)


def _focused(a) -> bool:
    """焦点核对：控件树能看就必须在输入框；看图看不到，就重新点一下输入框。"""
    if a.input_focused():
        return True
    a.focus_input()
    f = a.input_focused()
    return f is None or f


def deliver(a, task: dict, log=print) -> tuple[str, str]:
    """固定流程发一句话。

    输入：a 适配器；task {'to': 会话名, 'msg': 要发的话, 可选 journal/id/who}；log 打印函数
    输出：(结果, 原因)，结果四种：
      OK      核对到消息列表多了这一句
      RETRY   肯定没发（没按回车，或按了但字还在框里、已清掉），可以重试
      UNSURE  按了回车但没核对上，可能已经发了：不重试，交给人
      HUMAN   没发，但情况不对（框里有别人的字、读回对不上又清不掉），重试也一样，交给人
    """
    title, msg = task['to'], task['msg']
    t0 = time.time()

    def step(name):
        log(f'  [{a.kind}] {name}（{time.time() - t0:.1f}s）')

    if not a.open(title):
        return RETRY, '没打开目标会话'
    if not a.title_ok(title):
        return RETRY, '会话没对上或读不清'
    step('会话已确认')
    before = a.count_mine(title, msg)
    if before is None:
        return RETRY, '发前数不清这句已有几条'
    if not _focused(a):
        return RETRY, '焦点不在聊天输入框（前台可能是别的窗口）'
    draft = a.input_text()
    if draft is None:
        return RETRY, '读不到输入框'
    if draft:
        return HUMAN, f'输入框里已有 {len(draft)} 个字（可能是你的草稿），没动'
    a.write(msg)
    back = a.input_text()
    if back != msg:
        # 框原来是空的，现在里面的字只能是我们粘的，清掉再交回
        cleared = a.clear() if back else True
        if not cleared:
            return HUMAN, '粘贴后读回不对，也没清干净，请看一眼输入框'
        return RETRY, '粘贴后读回不是这句，已清空'
    step('已粘贴并读回一致')
    # 按回车前最后一刻：会话、焦点、框里的字，三样再查一遍，查完马上按
    if not (a.title_ok(title) and _focused(a) and a.input_text() == msg):
        return (RETRY, '回车前会话、焦点或输入框变了，已清空') if a.clear() else \
               (HUMAN, '回车前情况变了，输入框里还有这句没清掉，请看一眼')
    journal.record(task, 'about_to_send')
    press('enter')
    step('已按回车')
    for _ in range(CHECK_TRIES):
        time.sleep(0.7)
        n = a.count_mine(title, msg)
        if n is not None and n > before:
            journal.record(task, 'ok')
            step('核对到这条已发出')
            return OK, '消息列表多了这一句'
    if a.input_text() == msg:
        journal.record(task, 'fail')
        return (RETRY, '回车没发出去，字还在框里，已清空') if a.clear() else \
               (HUMAN, '回车没发出去，字还在框里，请看一眼')
    journal.record(task, 'unknown')
    return UNSURE, '按了回车但没核对到新消息，可能已发'
