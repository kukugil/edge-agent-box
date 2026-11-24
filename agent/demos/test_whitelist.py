"""回复名单：拆行、保存、检查和托盘对话框的离线测试。全部用合成名字，不读本机名单。

python -B -m unittest agent.demos.test_whitelist -v   （对话框用例需要 QT_QPA_PLATFORM=offscreen）
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from agent.tools import whitelist
from agent.tools.contacts_manager import parse_row


class TestParseRow(unittest.TestCase):
    def test_three_fields(self):
        row = parse_row('小明 明哥 同事')
        self.assertEqual((row['nickname'], row['remark'], row['tags'], row['title'], row['ok']),
                         ('小明', '明哥', ['同事'], '明哥', True))

    def test_empty_remark_uses_nickname(self):
        row = parse_row('小红  ')
        self.assertEqual((row['title'], row['tags'], row['ok']), ('小红', [], True))

    def test_several_tags(self):
        self.assertEqual(parse_row('甲 乙 同事，朋友')['tags'], ['同事', '朋友'])

    def test_space_in_name_is_ambiguous(self):
        # 昵称带空格：分不清哪个空格是名字里的，不能猜
        row = parse_row('Tom Lee 汤姆 ')
        self.assertFalse(row['ok'])
        self.assertEqual(row['title'], '')

    def test_known_tag_with_spaces(self):
        # 11/20 真机：有个标签是几个表情中间隔空格，单按空格拆会多出几段
        tags = ['同事', '朋友', '🙏 🙏 🙏']
        row = parse_row('甲 阿甲 🙏 🙏 🙏', tags)
        self.assertEqual((row['ok'], row['title'], row['tags']), (True, '阿甲', ['🙏 🙏 🙏']))
        row = parse_row('甲 阿甲 朋友,🙏 🙏 🙏', tags)
        self.assertEqual(row['tags'], ['朋友', '🙏 🙏 🙏'])
        self.assertEqual(parse_row('甲  🙏 🙏 🙏', tags)['title'], '甲')

    def test_tag_with_spaces_unknown_stays_ambiguous(self):
        self.assertFalse(parse_row('甲 阿甲 🙏 🙏 🙏')['ok'])

    def test_unknown_tag_without_space_ok(self):
        self.assertEqual(parse_row('甲 阿甲 新标签', ['同事'])['tags'], ['新标签'])

    def test_space_in_name_still_ambiguous_with_tags(self):
        self.assertFalse(parse_row('Tom Lee 汤姆 同事', ['同事'])['ok'])

    def test_empty_nickname_refused(self):
        self.assertFalse(parse_row(' 备注 ')['ok'])


class FakeList:
    """一屏 view 行的回收列表。滚完以后前 lag 次读仍是旧画面，模拟界面刷新慢（11/20 真机只读到 424/589 的原因）。"""

    def __init__(self, total=589, view=10, start=200, lag=6, rows_per_tick=1, per_event=None, dead=None):
        self.names = [f'人{i:03d}  ' for i in range(total)]
        self.view, self.top, self.lag, self.per = view, start, lag, rows_per_tick
        self.per_event = per_event      # 不管滚几格，一次滚轮固定走这么多行（11/20 真机约 1 行）
        self.dead = dict(dead or {})    # {行号: 次数}：滚到这里后接下来几次滚轮没反应
        self.shown, self.pending = self._snap(), 0

    def _snap(self):
        return [(i * 48, self.names[self.top + i]) for i in range(min(self.view, len(self.names) - self.top))]

    def wheel(self, n):
        if self.dead.get(self.top):
            self.dead[self.top] -= 1
            return
        step = (1 if n > 0 else -1) * self.per_event if self.per_event else n * self.per
        self.top = max(0, min(len(self.names) - self.view, self.top + step))
        self.pending = self.lag

    def read(self):
        if self.pending:
            self.pending -= 1
        else:
            self.shown = self._snap()
        return list(self.shown)


class TestScan(unittest.TestCase):
    def test_reads_everything_despite_slow_refresh(self):
        from agent.tools.contacts_manager import scan
        fake = FakeList()
        order, gaps, capped = scan(fake.read, fake.wheel, sleep=lambda s: None)
        self.assertEqual(order, fake.names)
        self.assertEqual(gaps, 0)

    def test_too_big_step_reports_gaps(self):
        from agent.tools.contacts_manager import scan
        fake = FakeList(lag=0, rows_per_tick=5)          # 一次滚 15 行，一屏只有 10 行
        order, gaps, capped = scan(fake.read, fake.wheel, sleep=lambda s: None)
        self.assertGreater(gaps, 0)
        self.assertLess(len(order), len(fake.names))

    def test_one_row_per_wheel_beyond_old_cap(self):
        # 11/20 真机：一次滚轮只走 1 行，旧上限 400 次停在 424 人
        from agent.tools.contacts_manager import scan
        fake = FakeList(lag=1, per_event=1, start=420)
        order, gaps, capped = scan(fake.read, fake.wheel, sleep=lambda s: None, expected=589)
        self.assertEqual((order, gaps, capped), (fake.names, 0, False))

    def test_wheel_dead_spot_is_not_bottom(self):
        from agent.tools.contacts_manager import scan
        fake = FakeList(lag=0, per_event=1, dead={414: 3})
        order, _, capped = scan(fake.read, fake.wheel, sleep=lambda s: None)
        self.assertEqual((order, capped), (fake.names, False))

    def test_cap_is_reported(self):
        from agent.tools.contacts_manager import scan
        fake = FakeList(lag=0, per_event=1)
        order, _, capped = scan(fake.read, fake.wheel, sleep=lambda s: None, max_pages=50)
        self.assertTrue(capped)
        self.assertLess(len(order), len(fake.names))

    def test_one_screen_list(self):
        from agent.tools.contacts_manager import scan
        fake = FakeList(total=7, start=0)
        order, _, capped = scan(fake.read, fake.wheel, sleep=lambda s: None)
        self.assertEqual(order, fake.names)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'contacts.local.json'

    def tearDown(self):
        self.dir.cleanup()

    def test_save_keeps_mail_and_other_keys(self):
        self.path.write_text(json.dumps({'allowed_chats': ['旧'], 'allowed_mail': ['a@b'], 'x': 1}), encoding='utf-8')
        whitelist.save_allowed(['新一', '新二'], self.path)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data, {'allowed_chats': ['新一', '新二'], 'allowed_mail': ['a@b'], 'x': 1})
        self.assertEqual(list(Path(self.dir.name).glob('*.tmp')), [])

    def test_load_broken_file_is_empty(self):
        self.path.write_text('{坏', encoding='utf-8')
        self.assertEqual(whitelist.load_allowed(self.path), [])

    def test_check(self):
        rows = [parse_row('甲 阿甲 '), parse_row('乙 阿甲 '), parse_row('丙  ')]
        self.assertEqual(whitelist.check(['丙', '文件传输助手'], rows), [])
        self.assertTrue(any('不止一个人' in p for p in whitelist.check(['阿甲'], rows)))
        self.assertTrue(any('重复' in p for p in whitelist.check(['丙', '丙 '], rows)))
        self.assertTrue(any('空' in p for p in whitelist.check([], rows)))


class TestDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.contacts = Path(self.dir.name) / 'contacts.local.json'
        self.cache = Path(self.dir.name) / 'cache.json'
        self.contacts.write_text(json.dumps({'allowed_chats': ['文件传输助手', '外人'], 'allowed_mail': ['m@x']},
                                            ensure_ascii=False), encoding='utf-8')
        rows = [parse_row(t) for t in ['甲 阿甲 同事，朋友', '乙  同事', '丙 重名 ', '丁 重名 ', 'Tom Lee  ']]
        whitelist.save_cache({'rows': rows, 'expected': 5, 'gaps': 0}, self.cache)

    def tearDown(self):
        self.dir.cleanup()

    def make(self, editable=True):
        from agent.ui.whitelist_dialog import WhitelistDialog
        return WhitelistDialog(lambda: editable, contacts_path=self.contacts, cache_path=self.cache)

    def leaf(self, dialog, title, enabled=True):
        from PySide6.QtCore import Qt
        return [i for i in dialog.leaves() if i.data(0, Qt.UserRole) == title
                and bool(i.flags() & Qt.ItemIsEnabled) == enabled]

    def test_existing_names_checked_and_unknown_kept(self):
        dialog = self.make()
        self.assertEqual(sorted(dialog.selected()), ['外人', '文件传输助手'])

    def test_same_person_in_two_tags_syncs(self):
        from PySide6.QtCore import Qt
        dialog = self.make()
        first, second = self.leaf(dialog, '阿甲')
        first.setCheckState(0, Qt.Checked)
        self.assertEqual(second.checkState(0), Qt.Checked)
        self.assertIn('阿甲', dialog.selected())

    def test_group_check_selects_members(self):
        from PySide6.QtCore import Qt
        dialog = self.make()
        group = next(dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())
                     if dialog.tree.topLevelItem(i).text(0).startswith('同事'))
        group.setCheckState(0, Qt.Checked)
        self.assertTrue({'阿甲', '乙'} <= set(dialog.selected()))

    def test_duplicates_and_ambiguous_not_selectable(self):
        dialog = self.make()
        self.assertEqual(len(self.leaf(dialog, '重名', enabled=False)), 2)
        self.assertEqual(self.leaf(dialog, '重名'), [])
        texts = [dialog.tree.topLevelItem(i).text(0) for i in range(dialog.tree.topLevelItemCount())]
        self.assertTrue(any(t.startswith('名字带空格') for t in texts))

    def test_search_hides_others(self):
        dialog = self.make()
        dialog.search.setText('阿甲')
        visible = [i.text(0) for i in dialog.leaves() if not i.isHidden()]
        self.assertTrue(visible and all('阿甲' in t for t in visible))

    def test_save_writes_selection_and_keeps_mail(self):
        from PySide6.QtCore import Qt
        dialog = self.make()
        self.leaf(dialog, '乙')[0].setCheckState(0, Qt.Checked)
        with patch('agent.ui.whitelist_dialog.QMessageBox.information'):
            dialog.save()
        data = json.loads(self.contacts.read_text(encoding='utf-8'))
        self.assertEqual(sorted(data['allowed_chats']), ['乙', '外人', '文件传输助手'])
        self.assertEqual(data['allowed_mail'], ['m@x'])

    def test_running_listener_blocks_read_and_save(self):
        dialog = self.make(editable=False)
        self.assertFalse(dialog.read_button.isEnabled())
        self.assertFalse(dialog.save_button.isEnabled())
        before = self.contacts.read_text(encoding='utf-8')
        dialog.save()
        self.assertEqual(self.contacts.read_text(encoding='utf-8'), before)

    def test_manual_add(self):
        dialog = self.make()
        dialog.manual.setText('手填的')
        dialog.add_manual()
        self.assertIn('手填的', dialog.selected())


if __name__ == '__main__':
    unittest.main()
