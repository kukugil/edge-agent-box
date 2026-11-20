"""微信的控件树适配器：用控件树确认会话、写输入框、读回、发送（D15 方案二）。

为什么走控件树：11/12 晚上看图那条路（模型看截图决定下一步、OCR 认标题、剪贴板 Ctrl+A/C 读回）
连着卡了 5 次。控件树能直接给出"哪个会话被选中""输入框里现在是什么"，不用猜。
前提：系统屏幕阅读器标志打开、微信重启过（见 chatlog.py）。读不到时 available() 交回 False，
调用方改用看图适配器（agent/core/send.py 的 VisionWeChat）。

控件（11/12–13 微信 4.1.15.56 实测）：
  会话行   ListItemControl  AutomationId = 'session_item_' + 会话名，支持选中状态和 Select()
  输入框   EditControl      AutomationId = 'chat_input_field'，ValuePattern 可读可写
  发送按钮 ButtonControl    类名 mmui::XOutlineButton，Name = '发送'（目前不用，按回车发）
坑：会话行的 AutomationId 带人名，不能打印。
"""
import time

import uiautomation as auto

INPUT_ID = 'chat_input_field'
ITEM_PREFIX = 'session_item_'


class WeChatUIA:
    """一个微信窗口的控件树操作。每个方法只做一件事，失败交回 False / None，不抛异常。"""

    kind = 'uia'

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._root = auto.ControlFromHandle(hwnd)
        self._box = None

    def _input(self):
        # 找一次要几十毫秒，记住；控件被销毁（切会话后可能重建）就重新找
        try:
            if self._box is not None and self._box.BoundingRectangle.width() > 0:
                return self._box
        except Exception:
            pass
        box = self._root.EditControl(AutomationId=INPUT_ID, searchDepth=30)
        self._box = box if box.Exists(1) else None
        return self._box

    def _item(self, title: str):
        it = self._root.ListItemControl(AutomationId=ITEM_PREFIX + title, searchDepth=30)
        return it if it.Exists(1) else None

    def preview(self, title: str) -> str | None:
        """会话行的整行文字（名字、最后一条预览、时间），当门铃用：手机上看过后角标会消失，预览不会。
        只查一次不等待：找不到的会话每轮都等 1 秒，会拖慢整个监听。内容带人名和聊天原文，不能打印。"""
        try:
            item = self._root.ListItemControl(AutomationId=ITEM_PREFIX + title, searchDepth=30)
            return item.Name if item.Exists(0, 0) else None
        except Exception:
            return None

    def unique(self, title: str) -> bool:
        """同一控件标识匹配多行时，不能用第一行当作收件人。"""
        try:
            expected = ITEM_PREFIX + title
            count = 0
            for item, _ in auto.WalkControl(self._root, includeTop=False, maxDepth=30):
                if item.AutomationId == expected:
                    count += 1
                    if count > 1:
                        return False
            return count == 1
        except (OSError, RuntimeError, AttributeError):
            return False

    def available(self) -> bool:
        """输出：这个窗口能不能走控件树（输入框找得到、能读值）。"""
        try:
            box = self._input()
            return box is not None and box.GetValuePattern() is not None
        except Exception:
            return False

    def selected(self, title: str) -> bool:
        """输入会话名，输出它是不是当前选中（右边开着）的会话。会话行不在列表里（滚出去了）也算 False。"""
        try:
            it = self._item(title)
            return bool(it and it.GetSelectionItemPattern().IsSelected)
        except Exception:
            return False

    def open(self, title: str) -> bool:
        """选中会话。已经选中就不动。输出：选中了没有。

        先用控件树的 Select()（不动鼠标）；没生效再点会话行中心。会话行滚出了可见范围就滚动会话列表找回来，
        还找不到交回 False，让调用方改用看图那条路找（看图的 find_row 会在列表里找字）。
        """
        if self.selected(title):
            return True
        it = self._item(title) or self._scroll_to(title)
        if it is None:
            return False
        try:
            it.GetSelectionItemPattern().Select()
        except Exception:
            pass
        time.sleep(0.4)
        if not self.selected(title):
            r = it.BoundingRectangle
            if r.width() <= 0:
                return False
            from agent.tools import click
            click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            time.sleep(0.5)
        self._box = None                 # 切了会话，输入框控件可能换了
        return self.selected(title)

    def _session_list(self):
        """会话列表的屏幕范围：取任意一个看得见的会话行的父控件。找不到交回 None。"""
        try:
            for item, _ in auto.WalkControl(self._root, includeTop=False, maxDepth=30):
                if item.AutomationId.startswith(ITEM_PREFIX):
                    rect = item.GetParentControl().BoundingRectangle
                    return rect if rect.width() > 0 and rect.height() > 0 else None
        except (OSError, RuntimeError, AttributeError):
            return None
        return None

    def _scroll_to(self, title: str, pages: int = 15):
        """会话列表只渲染看得见的十来行（11/19 真机：文件传输助手被挤下去后控件树里就没有这一行）。
        先滚到最上面，再一屏一屏往下找。只滚滚轮、不点搜索框：搜索框会弹出网页搜索建议，规矩里不许点。"""
        rect = self._session_list()
        if rect is None:
            return None
        from agent.tools import scroll
        x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        for _ in range(3):
            scroll(x, y, -15)
            time.sleep(0.2)
        for _ in range(pages):
            it = self._item_now(title)
            if it is not None:
                return it
            scroll(x, y, 3)
            time.sleep(0.3)
        return self._item_now(title)

    def scroll_to(self, title: str) -> bool:
        """会话行不在可见范围时滚动会话列表找回。找到交回 True。"""
        return self._scroll_to(title) is not None

    def _item_now(self, title: str):
        """只查一次不等待，滚动查找时用。"""
        try:
            it = self._root.ListItemControl(AutomationId=ITEM_PREFIX + title, searchDepth=30)
            return it if it.Exists(0, 0) and it.BoundingRectangle.width() > 0 else None
        except Exception:
            return None

    def input_text(self) -> str | None:
        """输出输入框里现在的字；读不到交回 None（不是空串，空串表示框是空的）。"""
        try:
            box = self._input()
            return None if box is None else (box.GetValuePattern().Value or '')
        except Exception:
            return None

    def write(self, text: str) -> bool:
        """把 text 粘贴进输入框（调用方要先确认框是空的、焦点在框上）。输出：粘没粘，不代表写对了，要再读回。

        为什么粘贴、不用 ValuePattern.SetValue：SetValue 绕过了微信自己的输入处理，
        框里显示有字，按回车时微信内部可能认为是空的；粘贴是真的键盘事件，D8 起一直这么发，没出过问题。
        读回不再用剪贴板 Ctrl+A/C（11/12 真机读回落空过），改读控件树的值。
        """
        from agent.tools import press
        try:
            auto.SetClipboardText(text)
            time.sleep(0.05)
            press('ctrl+v')
            time.sleep(0.2)
            return True
        except Exception:
            return False

    def clear(self) -> bool:
        """清空输入框（只在框里是我们自己刚粘的字时调用）。输出：清空了没有。"""
        from agent.tools import press
        if not self.input_focused():
            return False
        press('ctrl+a')
        press('delete')
        time.sleep(0.15)
        return self.input_text() == ''

    def focus_input(self) -> bool:
        """让输入框拿到键盘焦点，按回车才会落在它上面。输出：焦点是不是在输入框上了。"""
        try:
            box = self._input()
            if box is None:
                return False
            box.SetFocus()
            time.sleep(0.1)
            return self.input_focused()
        except Exception:
            return False

    def input_focused(self) -> bool:
        """输出：键盘焦点在不在聊天输入框，而且微信主窗口在前台（不是微信别的小窗口）。"""
        import ctypes
        try:
            return (ctypes.windll.user32.GetForegroundWindow() == self.hwnd
                    and auto.GetFocusedControl().AutomationId == INPUT_ID)
        except Exception:
            return False
