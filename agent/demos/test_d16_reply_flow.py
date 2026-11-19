"""D16 现场反例：验证回复入口、来源隔离和文件传输助手模拟。"""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agent.core import reply, trigger


NOW = datetime(2026, 11, 17, 23, 14)


def message(who, text, **extra):
    return {'who': who, 'kind': 'text', 'text': text, 'at': NOW, **extra}


def view(msgs):
    return {'ok': True, 'source': 'uia', 'reason': 'ok', 'msgs': msgs}


class TestD16ReplyFlow(unittest.TestCase):
    def test_current_clock_does_not_call_model_or_history_ranker(self):
        msgs = [message('us', '九点，先广州后上海'), message('them', '几点了？')]
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
             patch.object(trigger, 'WHITELIST', ['测试联系人甲']), \
             patch.object(trigger.messages, 'wait_quiet', return_value=view(msgs)), \
             patch.object(trigger, 'remember', return_value=msgs), \
             patch.object(trigger, 'datetime') as clock, \
             patch.object(trigger, 'ask_qwen', side_effect=AssertionError('不应调模型')), \
             patch.object(trigger, 'rank_history', side_effect=AssertionError('不应检索旧安排')):
            clock.now.return_value = NOW
            decision = trigger.decide(0, '测试联系人甲')
        self.assertEqual(decision['text'], '现在23点14分')
        self.assertEqual(decision['tier'], 'clock')

    def test_unreadable_clock_does_not_send(self):
        msgs = [message('them', '几点了？')]
        with patch.object(trigger.messages, 'wait_quiet', return_value=view(msgs)), \
             patch.object(trigger, 'remember', return_value=msgs), \
             patch.object(trigger, 'datetime') as clock:
            clock.now.side_effect = OSError('clock unavailable')
            decision = trigger.decide(0, '测试联系人甲')
        self.assertFalse(decision['send'])
        self.assertFalse(decision['read_ok'])

    def test_two_different_questions_are_not_reduced_to_clock_only(self):
        msgs = [message('them', '几点了？'), message('them', '二维码是什么？')]
        for kind in ('current_time', 'general'):
            with self.subTest(kind=kind):
                decision = reply.write_reply_free(msgs, NOW, [],
                                                  lambda _: '{"kind":"' + kind + '"}')
                self.assertFalse(decision['send'])
                self.assertEqual(decision['tier'], 'handoff')

    def test_unknown_personal_state_is_handed_to_human(self):
        msgs = [message('them', '你在睡觉吗？')]
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
             patch.object(trigger, 'WHITELIST', ['测试联系人甲']), \
             patch.object(trigger.messages, 'wait_quiet', return_value=view(msgs)), \
             patch.object(trigger, 'remember', return_value=msgs), \
             patch.object(trigger, 'ask_qwen', return_value='{"kind":"personal"}'), \
             patch.object(trigger, 'notify', return_value=True):
            decision = trigger.decide(0, '测试联系人甲')
        self.assertEqual(decision['tier'], 'handoff')
        self.assertEqual(decision['text'], reply.LOAN_HANDOFF)

    def test_unattributed_old_arrangement_is_not_evidence(self):
        msgs = [message('us', '九点，先广州后上海'), message('them', '几点去？')]
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
             patch.object(trigger, 'WHITELIST', ['测试联系人甲']), \
             patch.object(trigger.messages, 'wait_quiet', return_value=view(msgs)), \
             patch.object(trigger, 'remember', return_value=msgs), \
             patch.object(trigger, 'ask_qwen', return_value='{"kind":"history"}'), \
             patch.object(trigger, 'rank_history', side_effect=AssertionError('来源不明不能检索')), \
             patch.object(trigger, 'notify', return_value=True):
            decision = trigger.decide(0, '测试联系人甲')
        self.assertEqual(decision['tier'], 'handoff')

    def test_clarification_stops_after_two_useful_rounds(self):
        def ask(prompt):
            if '只核对候选微信回复' in prompt[0]['content']:
                return '{"ok":true}'
            return '你说的1是指哪件事？' if '1' in prompt[1]['content'].split('【对方这次说的】')[-1] else '是指哪个文件？'

        first = message('them', '1')
        decision = reply.write_reply_free([first], NOW, [], ask, intent='clarify')
        self.assertEqual(decision['tier'], 'clarify')
        one = message('us', decision['text'], origin='agent', reply_kind='clarify')
        second = message('them', '就是那个')
        decision = reply.write_reply_free([first, one, second], NOW, [], ask, intent='clarify')
        self.assertEqual(decision['tier'], 'clarify')
        two = message('us', decision['text'], origin='agent', reply_kind='clarify')
        third = message('them', '还是那个')
        decision = reply.write_reply_free([first, one, second, two, third], NOW, [], ask, intent='clarify')
        self.assertFalse(decision['send'])
        self.assertEqual(decision['tier'], 'handoff')

    def test_clarification_count_survives_history_sync(self):
        def ask(prompt):
            system = prompt[0]['content']
            if '只判断当前这一条微信消息' in system:
                return '{"kind":"clarify"}'
            if '只核对候选微信回复' in system:
                return '{"ok":true}'
            current = prompt[1]['content'].split('【对方这次说的】')[-1]
            return '具体指哪份资料？' if '那个' in current else '这个1是指什么呀？'

        screen = [message('them', '1')]
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
             patch.object(trigger, 'WHITELIST', ['测试联系人甲']), \
             patch.object(trigger, 'ask_qwen', side_effect=ask), \
             patch.object(trigger, 'notify', return_value=True):
            for question in ('1', '就是那个'):
                if question != '1':
                    screen.append(message('them', question))
                with patch.object(trigger.messages, 'wait_quiet', return_value=view(screen)):
                    decision = trigger.decide(0, '测试联系人甲')
                self.assertEqual(decision['tier'], 'clarify')
                screen.append(message('us', decision['text']))
                trigger.remember('测试联系人甲', view(screen),
                                 sent_task={'msg': decision['text'], 'tier': 'clarify'})
            screen.append(message('them', '还是那个'))
            with patch.object(trigger.messages, 'wait_quiet', return_value=view(screen)):
                decision = trigger.decide(0, '测试联系人甲')
            self.assertEqual(decision['tier'], 'handoff')

    def test_simulation_excludes_baseline_and_keeps_repeated_question(self):
        old = [message('us', '几点了？'), message('us', '刚醒')]
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
             patch.object(trigger, 'WHITELIST', [trigger.SIM_NAME]), \
             patch.object(trigger.messages, 'read_chat', return_value=view(old)):
            trigger.sim_start(0)
            try:
                first = trigger.sim_view(trigger.SIM_NAME, view(old + [message('us', '几点了？')]))
                self.assertEqual([m['who'] for m in first['msgs']], ['none', 'none', 'them'])
                trigger.sim_sent({'to': trigger.SIM_NAME, 'msg': '现在23点14分', 'tier': 'clock'})
                sent = trigger.sim_view(trigger.SIM_NAME, view(old + [message('us', '几点了？'),
                                                                 message('us', '现在23点14分')]))
                self.assertEqual(sent['msgs'][-1]['origin'], 'agent')
                second = trigger.sim_view(trigger.SIM_NAME, view(old + [message('us', '几点了？'),
                                                                   message('us', '现在23点14分'),
                                                                   message('us', '几点了？')]))
                self.assertEqual(second['msgs'][-1]['who'], 'them')
                trigger.remember(trigger.SIM_NAME, second)
                paths = list((Path(directory) / 'edge-agent' / 'wechat-history-sim').glob('*/*.jsonl'))
                self.assertEqual(len(paths), 1)
                self.assertEqual(len(paths[0].read_text(encoding='utf-8').splitlines()), 3)
            finally:
                trigger.SIM['on'] = False

    def test_simulation_stops_when_scroll_makes_repeat_ambiguous(self):
        old = [message('us', '几点了？') for _ in range(3)]
        with patch.object(trigger, 'WHITELIST', [trigger.SIM_NAME]), \
             patch.object(trigger.messages, 'read_chat', return_value=view(old)):
            trigger.sim_start(0)
            try:
                result = trigger.sim_view(trigger.SIM_NAME, view(old[1:]))
                self.assertFalse(result['ok'])
            finally:
                trigger.SIM['on'] = False


if __name__ == '__main__':
    unittest.main()
