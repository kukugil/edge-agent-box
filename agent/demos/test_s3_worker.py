"""离线比对原版 CLI 的分支顺序，并检查凭据配置校验。"""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch

from agent.s3 import run_cli
from agent.s3.run_cli import load_config, validated_key
from agent.s3.worker import run_agent


class TestWorkerLoop(unittest.TestCase):
    def test_cli_branch_precedence_sleep_and_pre_action_screenshot(self):
        image = Mock()
        image.resize.return_value.save.side_effect = lambda stream, **kwargs: stream.write(b'scaled')
        info = {'plan': '想' * 320, 'plan_code': 'agent.type()'}
        agent = Mock()
        agent.predict.side_effect = [(info, ['raise AssertionError("next wait")']),
                                     (info, ['WAIT']), (info, ['assert True']),
                                     (info, ['  dOnE '])]
        emit = Mock()
        shots = Path('offline-only')
        with patch('agent.s3.worker.time.sleep') as sleep:
            result = run_agent(agent, '合成任务', (4800, 2400), shots, emit, Mock(return_value=image),
                               'LANCZOS')
        self.assertEqual(result, 'DONE')
        self.assertEqual(sleep.call_args_list, [call(5), call(1.0), call(1.0)])
        self.assertEqual(image.resize.call_args_list, [call((2400, 1200), 'LANCZOS')] * 4)
        self.assertEqual(image.save.call_args_list,
                         [call(shots / f'step-{n}.png') for n in range(1, 5)])
        self.assertEqual(agent.predict.call_args.kwargs,
                         {'instruction': '合成任务', 'observation': {'screenshot': b'scaled'}})
        events = [(item.args[0], item.kwargs['n']) for item in emit.call_args_list]
        self.assertEqual(events, [('thinking', 1), ('executed', 1),
                                  ('thinking', 2), ('executing', 2), ('executed', 2),
                                  ('thinking', 3), ('executing', 3), ('executed', 3),
                                  ('thinking', 4), ('executed', 4)])
        self.assertEqual(emit.call_args_list[6].kwargs['plan'], '想' * 300)

    def test_exact_fail_and_fifteen_step_limit(self):
        image = Mock()
        agent = Mock()
        with patch('agent.s3.worker.time.sleep') as sleep:
            agent.predict.return_value = ({}, [' fail\n'])
            self.assertEqual(run_agent(agent, '任务', (1920, 1080), Path('.'),
                                       Mock(), Mock(return_value=image), None), 'FAIL')
            agent.predict.assert_called_once()
            agent.reset_mock()
            agent.predict.return_value = ({}, ['raise AssertionError("NEXT")'])
            self.assertEqual(run_agent(agent, '任务', (1920, 1080), Path('.'),
                                       Mock(), Mock(return_value=image), None), 'STEP_LIMIT')
            self.assertEqual(agent.predict.call_count, 15)
            sleep.assert_not_called()


class TestCredentialProfile(unittest.TestCase):
    def test_checks_exact_id_model_and_url_before_credential_lookup(self):
        config = load_config()
        profile = {'id': '41c51c1795394680a6d2f8353462211d', 'model': 'deepseek-flash',
                   'base_url': 'https://api.deepseek.com'}
        with (TemporaryDirectory() as tmp, patch.dict('os.environ', {'LOCALAPPDATA': tmp}),
              patch('agent.tools.secrets.get_key', return_value='synthetic') as read):
            path = Path(tmp) / 'edge-agent' / 'assistant' / 'profiles.json'
            path.parent.mkdir(parents=True)
            for wrong in (dict(profile, id='other'), dict(profile, model='different'),
                          dict(profile, base_url='https://example.test')):
                path.write_text(json.dumps([wrong]), encoding='utf-8')
                with self.assertRaises(ValueError):
                    validated_key(config)
            read.assert_not_called()
            path.write_text(json.dumps([profile]), encoding='utf-8')
            self.assertEqual(validated_key(config), 'synthetic')
            read.assert_called_once_with('edge-assistant/41c51c1795394680a6d2f8353462211d')

    def test_cli_passes_key_only_to_child_environment(self):
        task = '打开 Chrome，搜索“深圳湾 航拍”'
        argv = ['run_cli.py', '--task', task, '--minimize-wechat']
        original_value = os.environ.get('DEEPSEEK_API_KEY')
        with (patch('sys.argv', argv), patch.object(run_cli, 'validated_key', return_value='fake-key'),
              patch.object(run_cli, 'minimize_wechat') as minimize,
              patch.object(run_cli.subprocess, 'run', return_value=Mock(returncode=7)) as child):
            self.assertEqual(run_cli.main(), 7)
        minimize.assert_called_once()
        command = child.call_args.args[0]
        self.assertEqual(Path(command[0]), Path('D:/Newitem/Agent-S/.venv/Scripts/agent_s.exe'))
        self.assertEqual(child.call_args.kwargs['cwd'], 'D:/Newitem/Agent-S')
        self.assertEqual(dict(zip(command[1::2], command[2::2])),
                         {'--task': task, '--provider': 'deepseek', '--model': 'deepseek-flash',
                          '--model_url': 'https://api.deepseek.com', '--ground_provider': 'vllm',
                          '--ground_model': 'ui-tars-1.5-7b-awq',
                          '--ground_url': 'http://192.168.10.106:30015/v1', '--ground_api_key': 'EMPTY',
                          '--grounding_width': '1932', '--grounding_height': '1092'})
        self.assertEqual(child.call_args.kwargs['env']['DEEPSEEK_API_KEY'], 'fake-key')
        self.assertEqual(os.environ.get('DEEPSEEK_API_KEY'), original_value)

    def test_theme_light_is_standalone(self):
        with (patch('sys.argv', ['run_cli.py', '--theme-light']),
              patch.object(run_cli, 'restore_light_theme') as theme,
              patch.object(run_cli, 'validated_key') as read,
              patch.object(run_cli.subprocess, 'run') as child):
            self.assertEqual(run_cli.main(), 0)
        theme.assert_called_once()
        read.assert_not_called()
        child.assert_not_called()


if __name__ == '__main__':
    unittest.main()
