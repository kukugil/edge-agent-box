"""离线验证托盘入口：监听子进程的开始/停止、模型设置和 key 只发给对应地址。不碰微信。

运行：python -m unittest agent.demos.test_tray_entry
"""
import os
import sys
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from agent.core import listen, trigger, wechat_reply_flow  # noqa: E402
from agent.tools import models, secrets  # noqa: E402
from agent.tools.reply_store import ReplyStore  # noqa: E402

# 假监听：按真监听的样子输出几行，看到停止文件就收尾退出
FAKE = r'''
import sys, time
from pathlib import Path
stop = Path(sys.argv[sys.argv.index('--stop-file') + 1])
print('状态：正在启动，接入 3 个会话', flush=True)
print('状态：监听中', flush=True)
print('批次 #1 ok · 消息列表多了这一句', flush=True)
for _ in range(200):
    if stop.exists():
        print('已停止监听', flush=True)
        print('已停止', flush=True)
        sys.exit(0)
    time.sleep(0.05)
sys.exit(3)
'''


class TestListenEntry(unittest.TestCase):
    def test_contacts_put_file_transfer_first_and_reject_duplicates(self):
        self.assertEqual(listen.contacts(['甲', '文件传输助手', '乙'], '文件传输助手'), ['文件传输助手', '甲', '乙'])
        self.assertIsNone(listen.contacts(['甲', '甲'], '文件传输助手'))
        self.assertIsNone(listen.contacts([], '文件传输助手'))

    def test_listener_stops_between_rounds(self):
        with TemporaryDirectory() as tmp:
            store = ReplyStore(Path(tmp) / 'reply.db', mode='send')
            logs = []
            adapter = Mock(unique=Mock(return_value=True), preview=Mock(return_value=None))
            with (patch.object(wechat_reply_flow, 'WeChatUIA', return_value=adapter),
                  patch.object(trigger, 'prepare', return_value=True),
                  patch.object(wechat_reply_flow, 'check_responsive', return_value=(True, '')),
                  patch.object(wechat_reply_flow.Listener, '_single_window', return_value=True),
                  patch.object(wechat_reply_flow.Listener, 'process') as process):
                wechat_reply_flow.Listener(7, ['甲'], store, preview=False, log=logs.append).run(60, lambda: True)
            self.assertEqual(logs, ['已停止监听'])
            process.assert_not_called()

    def test_bring_up_gives_up_when_stopped(self):
        with patch.object(listen, 'find_windows', return_value=[]), \
             patch.object(listen.graph, '_wechat_windows', return_value=[]), \
             patch.object(listen.time, 'sleep'), patch.object(listen, 'say'):
            self.assertIsNone(listen.bring_up('文件传输助手', lambda: False, tries=3))
            self.assertIsNone(listen.bring_up('文件传输助手', lambda: True))


class TestModelSettings(unittest.TestCase):
    def test_user_model_overrides_default_and_key_goes_only_to_that_url(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'LOCALAPPDATA': tmp}), \
             patch.object(secrets, 'get_key', return_value='sk-test'):
            default = models.load_config()
            self.assertEqual(models.auth_headers(default['llm_url']), {})
            models.save_user_config(' https://api.example.test/v1 ', ' qwen-test ')
            config = models.load_config()
            self.assertEqual((config['llm_url'], config['llm_model']), ('https://api.example.test/v1', 'qwen-test'))
            self.assertEqual(config['vlm_model'], 'qwen-test')
            self.assertEqual(config['grounder_url'], default['grounder_url'])   # 定位模型不跟着换
            self.assertEqual(models.auth_headers('https://api.example.test/v1'), {'Authorization': 'Bearer sk-test'})
            self.assertEqual(models.auth_headers(default['llm_url']), {})
            self.assertNotIn('sk-test', models.user_config_path().read_text(encoding='utf-8'))

    def test_bad_settings_are_refused(self):
        for url, model in (('ftp://x/v1', 'm'), ('https://user:pw@x/v1', 'm'), ('https://x/v1?key=1', 'm'),
                           ('https://x/v1', ' ')):
            with self.assertRaises(ValueError):
                models.check_model(url, model)

    def test_broken_user_file_falls_back_to_default(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'LOCALAPPDATA': tmp}):
            models.user_config_path().parent.mkdir(parents=True)
            models.user_config_path().write_text('[1, 2', encoding='utf-8')
            self.assertEqual(models.load_user_config(), {})

    def test_credential_round_trip(self):
        target = f'edge-agent/test-{uuid.uuid4().hex}'
        try:
            self.assertEqual(secrets.get_key(target), '')
            secrets.put_key('sk-roundtrip', target)
            self.assertEqual(secrets.get_key(target), 'sk-roundtrip')
        finally:
            secrets.delete_key(target)
        self.assertEqual(secrets.get_key(target), '')
        secrets.delete_key(target)    # 删两次不报错


class TestRunner(unittest.TestCase):
    def test_status_lines(self):
        from agent.desktop_runner import status_of
        self.assertEqual(status_of('状态：监听中'), '监听中')
        self.assertEqual(status_of('已停止：没能把微信切到前台并打开会话'), '已停止：没能把微信切到前台并打开会话')
        self.assertTrue(status_of('有待回复，微信被最小化：等键鼠停 60 秒后再接管').startswith('监听中'))
        self.assertIsNone(status_of('  [uia] 会话已确认（0.3s）'))

    def test_start_then_stop_child_process(self):
        from agent.desktop_runner import Runner     # 先导入它：它负责把本机的 PySide6 加进路径
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'LOCALAPPDATA': tmp}):
            runner = Runner(program=sys.executable, args=['-u', '-c', FAKE])
            seen = []
            runner.changed.connect(lambda: seen.append(runner.status))
            loop = QEventLoop()

            def wait(check, ms=5000):
                timer = QTimer()
                timer.timeout.connect(lambda: (check() or timer.isActive() is False) and loop.quit())
                timer.start(20)
                QTimer.singleShot(ms, loop.quit)
                loop.exec()
                timer.stop()
            runner.start()
            wait(lambda: runner.status == '监听中' and len(runner.recent) >= 3)
            self.assertEqual(runner.status, '监听中')
            stopped = []
            runner.stop(then=lambda: stopped.append(True))
            self.assertEqual(runner.status, '正在停止：等这一轮做完')
            wait(lambda: not runner.running() and stopped)
            self.assertFalse(runner.running())
            self.assertEqual(stopped, [True])
            self.assertEqual(runner.status, '已停止')
            self.assertFalse(runner.stop_file.exists())
            self.assertIn('已停止监听', runner.log_path.read_text(encoding='utf-8'))
            self.assertIn('正在启动，接入 3 个会话', seen)
        app.processEvents()


if __name__ == '__main__':
    unittest.main()
