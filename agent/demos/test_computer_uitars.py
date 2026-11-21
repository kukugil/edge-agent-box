"""对照 UI-TARS 0.2.4 摘录和方案测试内核与事件。"""

import json
from pathlib import Path
import re
import unittest

from agent.computer import uitars
from agent.computer.protocol import EventLines


class KernelTests(unittest.TestCase):
    def test_prompt_verbatim_and_history(self):
        ref = Path(__file__).resolve().parents[2] / 'docs/reference/ui-tars-desktop-0.2.4-摘录.js'
        tick = chr(96)
        raw = re.search(r'const getSystemPromptV1_5 = .*? => ' + tick +
                        r'(.*?)' + tick + ';', ref.read_text(encoding='utf-8'), re.DOTALL)[1]
        raw = re.sub(r'\$\{language[^}]+\}', 'Chinese', raw)
        raw = re.sub(r'\$\{"([^"]+)"\}', r'\1', raw).replace('\\' + tick, tick).replace('\\\\', '\\')
        expected = raw.replace('## User Instruction', '## History Messages\n\n\n## User Instruction')
        self.assertEqual(uitars.instruction('打开网页'), expected + '打开网页')
        history = [('human' if i % 2 else 'assistant', f'消息{i}') for i in range(32)]
        prompt = uitars.instruction('任务', history, language='en')
        self.assertIn('Use English', prompt)
        self.assertNotIn('human: 消息1\n', prompt)
        self.assertIn('assistant: 消息2\n', prompt)
        self.assertTrue(prompt.endswith('human: 消息31\n\n## User Instruction\n任务'))

    def test_seventh_round_keeps_only_images_3_to_7_and_all_six_texts(self):
        turns = []
        for i in range(1, 8):
            turns.append(uitars.image_message(str(i)))
            if i < 7:
                turns.append({'role': 'assistant', 'content': f'回复{i}'})
        chat = uitars.messages('任务', turns)
        self.assertEqual(chat[0], {'role': 'user', 'content': '任务'})
        self.assertEqual([m['content'][0]['image_url']['url'] for m in chat[1:] if m['role'] == 'user'],
                         [f'data:image/png;base64,{i}' for i in range(3, 8)])
        self.assertEqual([m['content'] for m in chat if m['role'] == 'assistant'],
                         [f'回复{i}' for i in range(1, 7)])
        self.assertEqual(len(turns), 13)

    def test_summary_and_request(self):
        self.assertEqual(uitars.summary('Reflection: 判断\n复查\nAction_Summary: 打开\nAction: wait()'),
                         'Action_Summary: 打开\nAction: wait()')
        params = uitars.request_params('model', [])
        self.assertEqual((params['temperature'], params['top_p'], params['max_tokens'], params['stream']),
                         (0, 0.7, 65535, False))
        self.assertEqual(params['extra_body'], {'thinking': {'type': 'disabled'}})

    def test_alternate_headers_and_multiple_actions(self):
        actions = uitars.parse_prediction(
            "Reflection: 第一次没点中\nAction_Summary: 重试\nAction："
            "left_double(point='<point>70 80</point>')\n\n"
            "right_single(start_box='<bbox>10 20 30 40</bbox>')\n\n"
            "drag(start_point=(1,2), end_point=[3,4])\n\ncall_user()"
        )
        self.assertEqual([a.name for a in actions], ['left_double', 'right_single', 'drag', 'call_user'])
        self.assertEqual((actions[0].thought, actions[0].reflection), ('重试', '第一次没点中'))
        self.assertEqual(actions[2].params, {'start_box': '(1,2)', 'end_box': '[3,4]'})
        self.assertEqual(uitars.parse_prediction('Action_Summary: 等一下 Action: wait()')[0].thought, '等一下')
        self.assertEqual(uitars.parse_prediction("scroll(direction='down')")[0].name, 'scroll')

    def test_type_quotes_commas_newline_and_action_inside_text(self):
        reply = r'''Thought: 输入
Action: type(content='中文，Joe\'s \"a,b\" Action: ok\\路径\n')'''
        self.assertEqual(uitars.parse_prediction(reply)[0].params['content'],
                         '中文，Joe\'s "a,b" Action: ok\\路径\n')
        self.assertEqual(uitars.parse_prediction("type(content='一\n\nAction: 二\n')")[0].params['content'],
                         '一\n\nAction: 二\n')
        self.assertEqual([a.name for a in uitars.parse_prediction("type(content='中')\n\nfinished()")],
                         ['type', 'finished'])

    def test_empty_malformed_and_unknown(self):
        for value in ('', ' \n', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '解析失败'):
                uitars.parse_prediction(value)
        cases = [('看不懂', ''), ('Action:', ''), ("click(start_box='x)", ''),
                 ('future_action()', 'future_action'), ('Thought: 检查\nAction: finished()', 'finished')]
        for text, name in cases:
            with self.subTest(text=text):
                self.assertEqual(uitars.parse_prediction(text)[0].name, name)

    def test_resize_coordinate_pad_and_box_center(self):
        screen = (1920, 1080)
        self.assertEqual(uitars.smart_resize(1080, 1920), (1932, 1092))
        self.assertEqual(uitars.smart_resize(1136, 1920), (1932, 1148))
        self.assertEqual(uitars.screen_point('<|box_start|>(966,546)<|box_end|>', screen, screen), (960, 540))
        self.assertEqual(uitars.screen_point('<bbox>0 0 1932 1092</bbox>', screen, screen), (960, 540))
        self.assertEqual(uitars.screen_point('<point>966 574</point>', screen, (1920, 1136), 56), (960, 512))
        self.assertIsNone(uitars.screen_point('(nan,2)', screen, screen))

    def test_hotkeys(self):
        valid = {'ctrl', 'win', 'enter', 'left', 'pagedown', ',', 'a'}
        self.assertEqual(uitars.hotkeys('Ctrl+META return ARROWLEFT PAGE DOWN bogus comma a', valid),
                         ['ctrl', 'win', 'enter', 'left', 'pagedown', ',', 'a'])


class EventTests(unittest.TestCase):
    def test_split_utf8_and_step_limits(self):
        items = [{'event': 'start'},
                 *({'event': 'executed', 'n': n, 'thought': '中文', 'screen': [1920, 1080],
                    'shot': 'frame.png', 'action': 'click', 'point': [2, 3], 'seconds': 1.2}
                   for n in (0, 1, *range(16, 27))),
                 {'event': 'done', 'result': 'CALL_USER'}]
        source = '\n'.join(json.dumps(x, ensure_ascii=False) for x in items).encode('utf-8')
        reader = EventLines(25)
        actual = [event for byte in source for event in reader.feed(bytes((byte,)))]
        actual.extend(reader.feed(final=True))
        self.assertEqual(actual, [items[0], *items[2:-2], items[-1]])

    def test_ignores_invalid_json_and_results(self):
        reader = EventLines(25)
        self.assertEqual(list(reader.feed(b'log\r\n{"event":"done","result":"FAIL"}\n'
                                          b'{"event":"thinking","n":true}\n{"event":', final=True)), [])
        for result in ('DONE', 'CALL_USER', 'STEP_LIMIT', 'ERROR', 'STOPPED'):
            self.assertEqual(len(list(reader.feed(json.dumps({'event': 'done', 'result': result}).encode(),
                                                  final=True))), 1)


if __name__ == '__main__':
    unittest.main()
