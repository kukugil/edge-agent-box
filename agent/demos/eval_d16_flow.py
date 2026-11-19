"""D16 全链路离线评估：真实 Qwen、历史同步与检索，不操作微信。"""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agent.core import reply, trigger
from agent.tools import history


CONTACT = '测试联系人甲'
TONE = ['几点到？', '刚起', '不去了']
CASES = [
    ('clock_with_old_trip', [('us', '九点，先广州后上海', 'agent')], '几点了？', 'clock'),
    ('unknown_old_trip', [('us', '九点，先广州后上海', None)], '几点去？', ('hold', 'clarify', 'skip')),
    ('confirmed_old_trip', [('us', '九点，先广州后上海', 'human_confirmed')], '几点去？', 'free'),
    ('confirmed_old_trip_far',
     [('them', '明天几点去？', None), ('us', '九点，先广州后上海', 'human_confirmed')]
     + [('them', f'其他问题{i}', None) for i in range(7)] + [('us', '好', 'agent')],
     '明天几点去？', 'free'),
    ('personal_status', [], '你在睡觉吗？', 'hold'),
    ('personal_invitation', [], '出去嘛', 'handoff'),
    ('short_ambiguity', [], '1', 'clarify'),
    ('mixed_clock_and_knowledge', [], '几点了？\n二维码是什么？', 'skip'),
    ('live_weather', [], '今天天气怎么样？', 'hold'),
    ('general_knowledge', [], '二维码是什么？用一句话说', 'free'),
]


def run_case(name, old, question, expected):
    """输入合成会话和预期去向，返回决定；各题使用独立临时历史。"""
    with tempfile.TemporaryDirectory() as directory:
        at = datetime.now()
        prior = [{'who': who, 'kind': 'text', 'text': text, 'at': at,
                  **({'origin': origin} if origin else {})}
                 for who, text, origin in old]
        current = {'who': 'them', 'kind': 'text', 'text': question, 'at': at}
        root = Path(directory) / 'edge-agent' / 'wechat-history'
        if prior:
            history.sync(root, CONTACT, prior)
        view = {'ok': True, 'source': 'uia', 'reason': 'ok', 'msgs': prior + [current]}
        with patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
             patch.object(trigger, 'WHITELIST', [CONTACT]), \
             patch.object(trigger, 'TONE', TONE), \
             patch.object(trigger.messages, 'wait_quiet', return_value=view), \
             patch.object(trigger, 'notify', return_value=True):
            decision = trigger.decide(0, CONTACT)
        tier = decision.get('tier')
        if decision['pause'] and decision['text'] == reply.LOAN_HANDOFF:
            tier = 'handoff'
        expected_tiers = (expected,) if isinstance(expected, str) else expected
        if tier not in expected_tiers:
            raise AssertionError(f'{name}: expected {expected}, got {tier}')
        if name == 'unknown_old_trip' and any(x in decision['text'] for x in ('九点', '广州', '上海')):
            raise AssertionError('答复使用了未经确认的旧行程')
        if name == 'clock_with_old_trip':
            if not decision['text'].startswith('现在') or any(x in decision['text'] for x in ('广州', '上海')):
                raise AssertionError('当前钟点答复混入了旧行程')
        if name in ('confirmed_old_trip', 'confirmed_old_trip_far'):
            if '九点' not in decision['text'] or any(x in decision['text'] for x in ('广州', '上海')):
                raise AssertionError('只问时间时未准确只答时间')
        if name == 'short_ambiguity' and not decision['text'].endswith(('？', '?')):
            raise AssertionError('含糊短句没有明确提问')
        return {'case': name, 'expected': expected, 'tier': tier,
                'text': decision['text']}


def main():
    for case in CASES:
        print(json.dumps(run_case(*case), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
