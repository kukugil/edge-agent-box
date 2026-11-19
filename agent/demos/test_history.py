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
            self.assertIn('对方:我们用青松47', prompt)
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
