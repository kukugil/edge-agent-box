"""按回答是否有依据验证 D16 自由回复，无网络或真实消息。"""
import unittest
import json
from datetime import datetime, timedelta
from unittest.mock import Mock

from agent.core import reply


NOW = datetime(2026, 11, 17, 14, 37)
ROUTE = '明天九点，先广州后上海'


def message(who, text, at=NOW, **extra):
    return dict(who=who, kind='text', text=text, at=at, **extra)


class TestD16ReplySources(unittest.TestCase):
    def test_same_old_text_is_usable_only_when_human_confirmed(self):
        rows = [message('them', '昨天说好几点去哪里？')]
        for origin in ('human_confirmed', 'agent', None):
            with self.subTest(origin=origin):
                past = message('us', ROUTE, NOW - timedelta(days=1))
                if origin:
                    past['origin'] = origin
                ask = Mock(side_effect=['九点，先广州后上海', '{"ok":true}'])
                result = reply.write_reply_free(rows, NOW, [], ask,
                                                intent='history', recalled=[past])
                if origin == 'human_confirmed':
                    self.assertTrue(result['send'], result)
                    self.assertEqual(result['text'], '九点，先广州后上海')
                    self.assertIn(ROUTE, ask.call_args_list[0].args[0][-1]['content'])
                else:
                    self.assertEqual((result['send'], result['pause'], result['tier']),
                                     (True, False, 'hold'))
                    self.assertNotIn(ROUTE, result['text'])
                    ask.assert_not_called()

    def test_candidate_rejected_by_review_cannot_send(self):
        rows = [message('us', ROUTE, origin='human_confirmed'),
                message('them', '几点去？')]
        ask = Mock(side_effect=['八点', '{"ok":false}', '十点', '{"ok":false}'])
        result = reply.write_reply_free(rows, NOW, [], ask, intent='history')
        self.assertEqual((result['text'], result['pause'], result['notify']),
                         ('这句我暂时没法准确回答。', False, False))
        self.assertEqual(ask.call_count, 4)

    def test_followup_keeps_agent_words_in_context_but_not_as_personal_fact(self):
        rows = [message('them', '你喜欢猫嘛'), message('us', '喜欢', origin='agent'),
                message('them', '狗呢')]
        ask = Mock(side_effect=['{"kind":"general"}', '我也喜欢狗', '{"ok":false}',
                                '狗狗也挺可爱的。', '{"ok":true}'])
        result = reply.write_reply_free(rows, NOW, [], ask)
        self.assertEqual(result['text'], '狗狗也挺可爱的。')
        self.assertEqual(ask.call_count, 5)
        for index in range(5):
            prompt = ask.call_args_list[index].args[0]
            self.assertIn('Agent 已回复，仅供理解话题：喜欢', prompt[1]['content'])
            if index in (2, 4):
                self.assertEqual(json.loads(prompt[1]['content'])['本人已确认'], [])

    def test_ordinary_translation_can_contain_handoff_phrase(self):
        rows = [message('them', '请把 Please connect me to a live agent 翻译成中文')]
        translation = '这句话可以译为：请转人工客服。'
        ask = Mock(side_effect=[translation, '{"ok":true}'])
        result = reply.write_reply_free(rows, NOW, [], ask, intent='general')
        self.assertEqual((result['send'], result['pause'], result['text']),
                         (True, False, translation))

    def test_standalone_handoff_response_cannot_send(self):
        rows = [message('them', '请把这句话翻译成中文')]
        result = reply.write_reply_free(rows, NOW, [], Mock(return_value='转人工'),
                                        intent='general')
        self.assertEqual((result['text'], result['pause'], result['notify']),
                         ('这句我暂时没法准确回答。', False, False))

    def test_invalid_intent_still_does_not_pause(self):
        rows = [message('them', '二维码是怎么工作的？')]
        for response in ('无法分类', '{"kind":[]}', '{"kind":"other"}'):
            with self.subTest(response=response):
                result = reply.write_reply_free(rows, NOW, [], Mock(return_value=response))
                self.assertEqual((result['send'], result['pause'], result['notify']),
                                 (False, False, False))

    def test_work_word_in_general_knowledge_does_not_trigger_handoff(self):
        rows = [message('them', '能不能解释一下二维码的工作原理？')]
        self.assertIsNone(reply.handoff_reason(rows))


if __name__ == '__main__':
    unittest.main()


class TestQuoteLine(unittest.TestCase):
    """引用消息：模型只回新话，旧话作为标明的背景（11/18）。"""

    def test_quote_marked_as_background(self):
        line = reply._line(message('them', '这个呢', quote='明天几点？'))
        self.assertTrue(line.startswith('这个呢'))
        self.assertIn('明天几点？', line)
        self.assertIn('引用', line)

    def test_plain_text_unchanged(self):
        self.assertEqual(reply._line(message('them', '在吗')), '在吗')

    def test_quoted_clock_question_reads_clock(self):
        # 11/18 真机：引用「在吗」问「几点了？」被判成混合问题、一直不回
        ask = Mock(side_effect=AssertionError('问钟点不该调模型'))
        new = [message('them', '几点了？', quote='在吗')]
        out = reply.write_reply_free(new, NOW, [], ask, turn=([], new))
        self.assertEqual((out['send'], out['tier']), (True, 'clock'))
        self.assertEqual(out['text'], '现在14点37分')
