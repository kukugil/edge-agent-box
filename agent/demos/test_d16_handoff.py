"""D16：资料不足时持久接管；只用测试联系人和临时目录。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core import reply, trigger
from agent.tools import history


def message(who, text):
    return {'who': who, 'kind': 'text', 'text': text, 'at': None}


class TestD16Handoff(unittest.TestCase):
    def test_unknown_personal_fact_is_saved_and_subsequent_message_is_held(self):
        with tempfile.TemporaryDirectory() as directory:
            contact = '测试联系人甲'
            first = message('them', '你明天几点开会？')
            view = {'ok': True, 'source': 'uia', 'reason': 'ok', 'msgs': [first]}
            root = Path(directory)
            with patch.object(trigger, 'WHITELIST', [contact]), \
                 patch.object(trigger, 'history_folder', return_value=root), \
                 patch.object(trigger.messages, 'wait_quiet', return_value=view), \
                 patch.object(trigger, 'ask_qwen', return_value=reply.HUMAN) as ask, \
                 patch.object(trigger, 'notify', return_value=True) as notify:
                decision = trigger.decide(0, contact)
                self.assertTrue(decision['send'])
                self.assertTrue(decision['pause'])
                self.assertEqual(decision['text'], reply.LOAN_HANDOFF)
                self.assertEqual(decision['tier'], 'handoff')
                marker = history.handoff_path(root, contact)
                details = history.handoff_details(marker, reply.loan_request)
                self.assertEqual(details['reason'], '资料不足或需要本人决定')
                self.assertEqual(details['request']['text'], first['text'])
                notify.assert_called_once()

                view['msgs'] = [first, message('them', '还在吗？')]
                held = trigger.decide(0, contact)
                self.assertFalse(held['send'])
                self.assertTrue(held['pause'])
                self.assertEqual(ask.call_count, 1)

                history.resume_handoff(marker, reply.loan_request)
                self.assertFalse(marker.exists())
                resumed = trigger.decide(0, contact)
                self.assertFalse(resumed['send'])
                self.assertEqual(ask.call_count, 1)

    def test_model_failure_does_not_create_handoff_or_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            contact = '测试联系人甲'
            view = {'ok': True, 'source': 'uia', 'reason': 'ok',
                    'msgs': [message('them', '你明天几点开会？')]}
            root = Path(directory)
            with patch.object(trigger, 'WHITELIST', [contact]), \
                 patch.object(trigger, 'history_folder', return_value=root), \
                 patch.object(trigger.messages, 'wait_quiet', return_value=view), \
                 patch.object(trigger, 'ask_qwen', side_effect=TimeoutError), \
                 patch.object(trigger, 'notify') as notify:
                decision = trigger.decide(0, contact)
                self.assertFalse(decision['send'])
                self.assertEqual(decision['tier'], 'error')
                self.assertFalse(history.handoff_path(root, contact).exists())
                notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
