"""托盘里勾选"哪些微信好友可以自动回复"的对话框。

好友来自微信"通讯录管理"窗口（agent.tools.contacts_manager），按微信标签分组，勾组 = 勾组里所有人。
同一个人有几个标签就出现在几个组里，勾一处其他地方跟着变。
"""

import os
import sys
from importlib.util import find_spec
from pathlib import Path

if find_spec('PySide6') is None:   # 和 agent/desktop.py 一样：PySide6 装在本机数据目录
    sys.path.insert(0, str(Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'edge-agent' / 'desktop-deps'))

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from agent.tools import whitelist

ROLE = Qt.UserRole


class ReadThread(QThread):
    """后台滚动读取通讯录管理。uiautomation 在新线程里要先初始化 COM。"""
    progress = Signal(str)
    done = Signal(object, str)     # (结果或 None, 出错原因)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel = False

    def run(self):
        import uiautomation as auto
        from agent.tools import contacts_manager
        with auto.UIAutomationInitializerInThread():
            try:
                result = contacts_manager.read_all(self.progress.emit, lambda: self.cancel)
                self.done.emit(result, '')
            except contacts_manager.ReadStopped as e:
                self.done.emit(None, str(e))
            except Exception as e:  # 读屏接口偶发异常，别让托盘崩
                self.done.emit(None, f'读取出错：{e}')


class WhitelistDialog(QDialog):
    """输入：can_edit() 返回 False 时（自动回复在跑）不许读取和保存；hide_others/show_others 在读取时让开屏幕。"""

    def __init__(self, can_edit, hide_others=lambda: None, show_others=lambda: None, parent=None,
                 contacts_path=whitelist.CONTACTS, cache_path=whitelist.CACHE):
        super().__init__(parent)
        self.setObjectName('whitelistDialog')
        self.setWindowTitle('自动回复名单')
        self.resize(520, 640)
        self.can_edit = can_edit
        self.hide_others, self.show_others = hide_others, show_others
        self.contacts_path, self.cache_path = contacts_path, cache_path
        self.syncing = False
        self.thread = None

        layout = QVBoxLayout(self)
        tip = QLabel('先在微信里打开 通讯录 → 通讯录管理（左栏选"全部"），再点"从微信读取"。'
                     '读取时程序会用鼠标滚动那个窗口，大约一两分钟，期间请别动鼠标；一动就会停下。')
        tip.setWordWrap(True)
        tip.setObjectName('secondary')
        layout.addWidget(tip)
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        self.read_button = QPushButton('从微信读取', objectName='plain')
        self.read_button.clicked.connect(self.start_read)
        row.addWidget(self.read_button)
        layout.addLayout(row)
        self.search = QLineEdit(placeholderText='搜索名字或标签')
        self.search.textChanged.connect(self.apply_filter)
        layout.addWidget(self.search)
        self.tree = QTreeWidget(objectName='whitelistTree')
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self.on_changed)
        layout.addWidget(self.tree, 1)
        manual = QHBoxLayout()
        self.manual = QLineEdit(placeholderText='手动添加：输入会话标题（有备注填备注）')
        manual.addWidget(self.manual, 1)
        add = QPushButton('添加', objectName='plain')
        add.clicked.connect(self.add_manual)
        manual.addWidget(add)
        layout.addLayout(manual)
        bottom = QHBoxLayout()
        self.count = QLabel()
        bottom.addWidget(self.count, 1)
        cancel = QPushButton('取消', objectName='plain')
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self.save_button = QPushButton('保存')
        self.save_button.clicked.connect(self.save)
        bottom.addWidget(self.save_button)
        layout.addLayout(bottom)

        self.cache = whitelist.load_cache(self.cache_path)
        self.build(whitelist.load_allowed(self.contacts_path))
        self.sync_buttons()

    # ---------- 列表 ----------
    def rows(self):
        return (self.cache or {}).get('rows', [])

    def build(self, checked_names):
        """按缓存重建树。checked_names：要打勾的显示名。"""
        checked = set(checked_names)
        self.syncing = True
        self.tree.clear()
        known = set()
        top = QTreeWidgetItem(self.tree, [whitelist.SIM_NAME + '（自己测试用）'])
        self._leaf(top, whitelist.SIM_NAME, checked)
        known.add(whitelist.SIM_NAME)
        groups = {}
        unsure = []
        for row in self.rows():
            if not row.get('ok'):
                unsure.append(row['raw'])
                continue
            for tag in row['tags'] or ['无标签']:
                groups.setdefault(tag, []).append(row)
            known.add(row['title'])
        dup = self.duplicates()
        for tag in sorted(groups, key=lambda t: (t == '无标签', t)):
            group = QTreeWidgetItem(self.tree, [f'{tag}（{len(groups[tag])}）'])
            group.setFlags(group.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            group.setCheckState(0, Qt.Unchecked)
            for row in sorted(groups[tag], key=lambda r: r['title']):
                label = row['title'] if not row['remark'] else f"{row['remark']}　（昵称 {row['nickname']}）"
                item = QTreeWidgetItem(group, [label])
                self._leaf(item, row['title'], checked)
                if row['title'] in dup:
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                    item.setText(0, label + '　—— 和别人同名，不能选')
        others = [name for name in checked if name not in known]
        if others:
            group = QTreeWidgetItem(self.tree, [f'名单里已有、通讯录里没对上（{len(others)}）'])
            group.setFlags(group.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            group.setCheckState(0, Qt.Unchecked)
            for name in sorted(others):
                self._leaf(QTreeWidgetItem(group, [name]), name, checked)
        if unsure:
            group = QTreeWidgetItem(self.tree, [f'名字带空格、拆不准，不能勾（{len(unsure)}）：要回复请手动添加'])
            group.setFlags(group.flags() & ~Qt.ItemIsUserCheckable)
            for raw in unsure:
                item = QTreeWidgetItem(group, [raw])
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
        self.syncing = False
        self.show_status()
        self.update_count()
        self.apply_filter(self.search.text())

    def _leaf(self, item, title, checked):
        item.setData(0, ROLE, title)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked if title in checked else Qt.Unchecked)

    def duplicates(self):
        counts = {}
        for row in self.rows():
            if row.get('ok'):
                counts[row['title']] = counts.get(row['title'], 0) + 1
        return {title for title, n in counts.items() if n > 1}

    def leaves(self):
        out = []
        def walk(item):
            if item.data(0, ROLE):
                out.append(item)
            for i in range(item.childCount()):
                walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return out

    def selected(self):
        """输出打勾的显示名，去重、保持出现顺序。"""
        names = []
        for item in self.leaves():
            title = item.data(0, ROLE)
            if item.checkState(0) == Qt.Checked and item.flags() & Qt.ItemIsEnabled and title not in names:
                names.append(title)
        return names

    def on_changed(self, item, _column):
        """同一个人在别的标签组里的勾跟着变。"""
        title = item.data(0, ROLE)
        if self.syncing or not title:
            self.update_count()
            return
        self.syncing = True
        state = item.checkState(0)
        for other in self.leaves():
            if other is not item and other.data(0, ROLE) == title and other.flags() & Qt.ItemIsEnabled:
                other.setCheckState(0, state)
        self.syncing = False
        self.update_count()

    def apply_filter(self, text):
        text = text.strip()
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if group.childCount() == 0:
                group.setHidden(bool(text) and text not in group.text(0))
                continue
            group_hit = bool(text) and text in group.text(0)
            visible = 0
            for j in range(group.childCount()):
                child = group.child(j)
                hide = bool(text) and not group_hit and text not in child.text(0)
                child.setHidden(hide)
                visible += not hide
            group.setHidden(visible == 0)
            group.setExpanded(bool(text))

    def add_manual(self):
        name = self.manual.text().strip()
        if not name:
            return
        keep = self.selected()
        if name not in keep:
            keep.append(name)
        self.manual.clear()
        self.build(keep)

    def update_count(self):
        self.count.setText(f'已选 {len(self.selected())} 个会话')

    def show_status(self):
        if not self.cache:
            self.status.setText('还没从微信读过好友。')
            return
        rows = self.rows()
        ok = sum(1 for row in rows if row.get('ok'))
        text = f"上次读取 {self.cache['updated']}：{len(rows)} 人，拆不准 {len(rows) - ok} 人"
        if self.cache.get('expected') and self.cache['expected'] != len(rows):
            text += f"；微信显示 {self.cache['expected']} 人，对不上，可以再读一次"
        self.status.setText(text)

    def sync_buttons(self):
        editable = self.can_edit() and self.thread is None
        self.read_button.setEnabled(editable)
        self.save_button.setEnabled(editable)
        if not self.can_edit():
            self.status.setText('自动回复正在运行：先停止自动回复，才能读取和保存名单。')

    # ---------- 读取 ----------
    def start_read(self):
        if not self.can_edit():
            self.sync_buttons()
            return
        self.keep = self.selected()
        self.thread = ReadThread(self)
        self.thread.progress.connect(self.status.setText)
        self.thread.done.connect(self.read_done)
        self.sync_buttons()
        self.status.setText('正在读取，请别动鼠标……')
        # 让开屏幕：托盘窗口挡在通讯录管理上面时，滚轮会滚到我们自己身上
        self.hide_others()
        self.hide()
        self.thread.start()

    def read_done(self, result, why):
        self.thread.wait()
        self.thread = None
        self.show_others()
        self.show()
        self.raise_()
        self.activateWindow()
        if result is not None:
            self.cache = whitelist.save_cache(result, self.cache_path)
            self.build(self.keep)
            if result.get('gaps'):
                self.status.setText(self.status.text() + f"；滚动时可能跳过了几行（{result['gaps']} 处）")
            if result.get('capped'):
                self.status.setText(self.status.text() + '；滚动次数到了上限，没滚到底')
        else:
            self.status.setText(why)
        self.sync_buttons()

    def reject(self):
        if self.thread is not None:
            self.thread.cancel = True
            return
        super().reject()

    # ---------- 保存 ----------
    def save(self):
        if not self.can_edit():
            self.sync_buttons()
            return
        names = self.selected()
        problems = whitelist.check(names, self.rows())
        if problems:
            QMessageBox.warning(self, '名单没保存', '\n'.join(problems))
            return
        whitelist.save_allowed(names, self.contacts_path)
        QMessageBox.information(self, '已保存', f'名单已保存，共 {len(names)} 个会话。\n'
                                '重新开始自动回复后生效；托盘里的待办列表要重开托盘才会跟着变。')
        self.accept()


def listener_idle() -> bool:
    """自动回复没在跑时交回 True：试着拿监听进程持有的那把文件锁，拿得到说明没人在监听。"""
    import msvcrt
    from agent.core.wechat_reply_flow import database_path
    lock = database_path('send').with_name('wechat-reply.listener.lock')
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open('a+b') as stream:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return True
    except OSError:
        return False


if __name__ == '__main__':
    # 不开托盘、单独看名单界面：python -m agent.ui.whitelist_dialog
    from PySide6.QtWidgets import QApplication
    from agent.ui.main_window import STYLE
    app = QApplication(sys.argv)
    dialog = WhitelistDialog(listener_idle)
    dialog.setStyleSheet(STYLE)
    dialog.show()
    sys.exit(app.exec())
