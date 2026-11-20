"""离线验证微信联系人文件和实际回复入口，使用临时目录与测试联系人。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core import trigger
from agent.tools import history


def message(who, text):
    """生成一条不带时间线的测试消息。"""
    return {'who': who, 'kind': 'text', 'text': text, 'at': None}


class TestHistory(unittest.TestCase):
    def test_contact_isolation_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = message('them', '今天方便吗？')
            same = message('them', '好的')
            screen = [first, same, same]
            self.assertEqual(history.sync(root, '测试联系人甲', screen), screen)
            self.assertEqual(history.sync(root, '测试联系人甲', screen), screen)
            self.assertEqual(history.sync(root, '测试联系人乙', [same]), [same])
            self.assertCountEqual([len(p.read_text(encoding='utf-8').splitlines())
                                   for p in root.glob('*.jsonl')], [3, 1])

    def test_overlap_after_resizing_and_earlier_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a, b, c = (message('them', word) for word in ('第一段', '第二段', '第三段'))
            history.sync(root, '测试联系人甲', [a, b])
            self.assertEqual(history.sync(root, '测试联系人甲',
                                         [message('us', '更早的话'), a, b, c]), [a, b, c])
            self.assertEqual(history.sync(root, '测试联系人甲', [a, b]), [])
            self.assertEqual(history.sync(root, '测试联系人甲', [c]), [a, b, c])

    def test_resyncs_after_a_screenful_of_new_messages(self):
        """11/19 好友测试：一次来了一屏多，屏上已没有上次记下的最后一条，而那几条又没有时间（分隔条滚出去了）。
        旧逻辑只比"屏上第一条"和"记录最后一条"的时间，从此一条都不追加。"""
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            early = {**message('them', '早些的话'), 'at': datetime(2026, 11, 19, 19, 37)}
            history.sync(root, '测试联系人甲', [early, message('us', '回过了'), message('them', '好的')])
            later = [message('them', '分隔条上面的话'),
                     {**message('them', '新的一屏'), 'at': datetime(2026, 11, 19, 20, 37)},
                     {**message('us', '新回复'), 'at': datetime(2026, 11, 19, 20, 37)}]
            self.assertEqual(len(history.sync(root, '测试联系人甲', later)), 6)
            # 接上之后照常按重叠追加
            follow = later[1:] + [message('them', '再来一句')]
            self.assertEqual(len(history.sync(root, '测试联系人甲', follow)), 7)
            # 没有时间能证明更新的屏不追加（可能是往上翻的旧屏）
            self.assertEqual(history.sync(root, '测试联系人甲', [message('them', '不知道新旧')]),
                             [message('them', '不知道新旧')])
            self.assertEqual(len(next(root.glob('*.jsonl')).read_text(encoding='utf-8').splitlines()), 7)

    def test_repeated_handoff_text_is_still_marked_as_agent(self):
        """同一句交接话术发第二次，屏上有两条一样的：标最下面那条，不能当成本人插话。"""
        with tempfile.TemporaryDirectory() as directory:
            title = trigger.WHITELIST[0]
            with patch.object(trigger, 'history_folder', lambda t, s: Path(directory)):
                said = '这个我晚点回你'
                first = [message('them', '问一'), message('us', said)]
                trigger.remember(title, {'ok': True, 'source': 'uia', 'msgs': first}, sent_task={'msg': said})
                second = first + [message('them', '问二'), message('us', said)]
                rows = trigger.remember(title, {'ok': True, 'source': 'uia', 'msgs': second}, sent_task={'msg': said})
            self.assertEqual([row.get('origin') for row in rows if row['who'] == 'us'], ['agent', 'agent'])

    def test_restart_restores_context_through_reply_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'LOCALAPPDATA': directory}
            root = Path(directory) / 'edge-agent' / 'wechat-history'
            ack = {**message('us', '收到，测试编号是青松47'), 'origin': 'human_confirmed'}
            history.sync(root, '测试联系人甲', [message('them', '我们用青松47'), ack])
            # 启动新解释器。窗口只保留最后一句旧回复和新问题。
            child = '''
import json
from unittest.mock import patch
from agent.core import trigger as t
view = {'ok': True, 'source': 'uia', 'reason': 'ok', 'msgs': [
    {'who': 'us', 'kind': 'text', 'text': '收到，测试编号是青松47', 'at': None},
    {'who': 'them', 'kind': 'text', 'text': '编号是什么？', 'at': None},
]}
def ask(prompt):
    if '只判断当前这一条微信消息' in prompt[0]['content']:
        return '{"kind":"history"}'
    if '只核对候选微信回复' in prompt[0]['content']:
        return '{"ok":true}'
    print(json.dumps(prompt, ensure_ascii=True))
    return '青松47'
with patch.object(t, 'WHITELIST', ['测试联系人甲']), patch.object(t.messages, 'wait_quiet', return_value=view), patch.object(t, 'ask_qwen', side_effect=ask):
    assert t.decide(0, '测试联系人甲')['text'] == '青松47'
'''
            run = subprocess.run([sys.executable, '-B', '-c', child], env=env,
                                 capture_output=True, text=True, encoding='utf-8', check=True)
            prompt = json.loads(run.stdout.splitlines()[-1])[-1]['content']
            self.assertIn('对方：我们用青松47', prompt)
            self.assertIn('编号是什么？', prompt)
            self.assertEqual(len(next(root.glob('*.jsonl')).read_text(encoding='utf-8').splitlines()), 3)

    def test_unverified_input_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory}):
            view = {'ok': True, 'source': 'uia', 'msgs': [message('them', '测试')]}
            trigger.remember('不在测试名单', view)
            trigger.remember(trigger.WHITELIST[0], {**view, 'ok': False})
            trigger.remember(trigger.WHITELIST[0], {**view, 'source': 'ocr'})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unanswered_old_messages_do_not_expand_live_batch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory}):
            contact = trigger.WHITELIST[0]
            first = message('them', '旧消息')
            anchor = message('them', '重叠消息')
            latest = message('them', '新消息')
            view = {'ok': True, 'source': 'uia'}
            trigger.remember(contact, {**view, 'msgs': [first, anchor]})
            self.assertEqual(trigger.remember(contact, {**view, 'msgs': [anchor, latest]}),
                             [anchor, latest])


if __name__ == '__main__':
    unittest.main()
