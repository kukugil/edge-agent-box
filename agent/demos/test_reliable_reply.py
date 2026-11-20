"""可恢复微信回复的独立需求样例。只用临时账本与替身，不碰微信。"""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from agent.core import reply, reply_turn, send, trigger, wechat_reply_flow
from agent.demos.test_send_flow import Fake
from agent.tools.reply_store import ReplyStore

NOW = datetime(2026, 11, 17, 17, 0)
A, B = '测试会话甲', '测试会话乙'


def incoming(text):
    return dict(who='them', kind='text', at=NOW, text=text)


def outgoing(text, origin='agent'):
    return dict(who='us', kind='text', at=NOW, text=text, origin=origin)


def plan(*parts):
    return json.dumps({'parts': [dict(ids=ids, kind=kind, meaning=meaning, missing=missing)
                                 for ids, kind, meaning, missing in parts]}, ensure_ascii=False)


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'reply.sqlite3'
        self.store = ReplyStore(self.path, mode='send')

    def test_unread_survives_open_and_process_restart(self):
        self.store.discover(A, 1)
        restored = ReplyStore(self.path, mode='send')
        self.assertEqual(restored.scheduled([B, A]), [A])
        restored.observe(A, [incoming('旧已读'), incoming('这条未读')])
        self.assertEqual(restored.pending(A)['incoming'], [incoming('这条未读')])

    def test_startup_baseline_and_late_correction(self):
        old = [incoming('旧已读')]
        first = old + [incoming('用昨天那版')]
        self.store.observe(A, old)
        self.assertEqual(self.store.scheduled([A]), [])
        self.store.observe(A, first)
        token = self.store.pending(A)['id']
        self.store.observe(A, first + [incoming('最后一版')])
        saved = self.store.pending(A)
        self.assertEqual(saved['id'], token)
        self.assertEqual([r['text'] for r in saved['incoming']], ['用昨天那版', '最后一版'])

    def test_manual_outgoing_does_not_cover_pending_inbound(self):
        self.store.discover(A, 1)
        self.store.observe(A, [incoming('第一条')])
        self.store.observe(A, [incoming('第一条'), outgoing('稍等', None), incoming('第二条')])
        self.assertEqual([r['text'] for r in self.store.pending(A)['incoming']], ['第一条', '第二条'])

    def test_uncertain_restart_cannot_resend_turn_and_keeps_issue(self):
        self.store.discover(A, 1)
        self.store.observe(A, [incoming('帮我审批')])
        turn = self.store.pending(A)['id']
        self.store.audit(turn, 'about_to_send', '我晚点亲自回复你', issues=['帮我审批'])
        restored = ReplyStore(self.path)
        restored.recover()
        self.assertEqual(restored.scheduled([A, B]), [])
        self.assertEqual(restored.review_items(A)[0]['id'], turn)
        self.assertEqual(restored.open_issues(A)[0]['text'], '帮我审批')
        restored.discover(B, 1)
        self.assertEqual(restored.scheduled([A, B]), [B])

    def test_two_identical_new_bubbles_are_retained_with_anchor(self):
        base = [outgoing('一个不同的锚点')]
        self.store.observe(A, base)
        self.store.observe(A, base + [incoming('在吗'), incoming('在吗')])
        self.assertEqual(len(self.store.pending(A)['incoming']), 2)

    def test_unalignable_screen_falls_back_to_last_outgoing_without_locking(self):
        """11/19 好友测试：对不上就锁会话，后面的正常来信全没回。改为以我方最后一条为界、不锁。"""
        self.store.observe(A, [outgoing('锚点'), incoming('在吗'), incoming('在吗')])
        self.store.discover(A, 1)
        # 重复短句让对齐有两种解释：界后三条里前两条上次已经在屏上，只登记新多出的一条
        self.assertEqual(self.store.observe(A, [incoming('在吗'), incoming('在吗'), incoming('在吗')]), 'anchor')
        self.assertEqual([m['text'] for m in self.store.pending(A)['incoming']], ['在吗'])
        self.assertEqual(self.store.review_items(A), [])
        self.assertTrue(self.store.can_reply(A))

    def test_first_contact_without_badge_keeps_only_messages_after_start(self):
        """手机看过后电脑角标被清掉，只能靠预览门铃（数量未知）：启动前的旧话不回，启动后的回。"""
        self.store.discover(B, -1)
        old = dict(incoming('昨天的旧话'), at=NOW - timedelta(hours=20))
        self.store.observe(B, [old, outgoing('昨天回过了'), old, incoming('在吗')], since=NOW)
        self.assertEqual([m['text'] for m in self.store.pending(B)['incoming']], ['在吗'])
        self.store.discover(A, -1)
        self.store.observe(A, [old], since=NOW)
        self.assertIsNone(self.store.pending(A))
        # 没有时间分隔条、也没有角标数：只认最后一条
        self.store.discover('丙', -1)
        self.store.observe('丙', [dict(incoming('早'), at=None), dict(incoming('在吗'), at=None)], since=NOW)
        self.assertEqual([m['text'] for m in self.store.pending('丙')['incoming']], ['在吗'])

    def test_read_failures_never_lock_the_session(self):
        self.store.discover(A, 1)
        for _ in range(5):
            self.store.watch_failed(A, '读不到')
        self.assertEqual(self.store.scheduled([A]), [A])
        self.assertTrue(self.store.can_reply(A))
        self.store.observe(A, [incoming('这条未读')])
        self.assertEqual([m['text'] for m in self.store.pending(A)['incoming']], ['这条未读'])

    def test_longest_overlap_wins_over_repeated_fixed_receipt(self):
        """11/18 真机：滚屏一行后重叠 10 条，固定回执凑巧让首尾也对上 1 条。"""
        receipt = outgoing(reply.LOAN_HANDOFF)
        old = [outgoing(f'旧话{i}') for i in range(8)]
        screen = [outgoing('最上面'), receipt, *old, incoming('借钱'), receipt]
        self.store.observe(A, screen)
        self.store.observe(A, screen[1:] + [incoming('你喜欢猫吗')])
        self.assertEqual([m['text'] for m in self.store.pending(A)['incoming']], ['你喜欢猫吗'])
        self.assertEqual(self.store.review_items(A), [])
        # 最长重叠只有两条时仍不猜
        from agent.tools.reply_store import appended
        self.assertIsNone(appended([receipt, receipt], [receipt, receipt, incoming('y')]))

    def test_retry_exhausted_and_independent_issue_resolution(self):
        self.store.discover(A, 1)
        self.store.observe(A, [incoming('第一条')])
        turn = self.store.pending(A)['id']
        self.store.retry(turn, '暂时不能生成')
        self.store.retry(turn, '仍不能生成')
        self.assertEqual(self.store.review_items(A)[0]['id'], turn)
        self.store.observe(A, [incoming('第一条'), incoming('审批甲'), incoming('审批乙')])
        turn = self.store.pending(A)['id']
        self.store.finish(turn, 'preview', '测试', issues=['审批甲', '审批乙'])
        self.assertTrue(self.store.resolve(A, turn, 0))
        self.assertEqual([item['text'] for item in self.store.open_issues(A)], ['审批乙'])
        self.assertFalse(self.store.resolve(A, turn, 0))

    def test_preview_cannot_be_reused_to_send(self):
        with self.assertRaises(ValueError):
            ReplyStore(self.path, mode='preview')


class ReplyTests(unittest.TestCase):
    def decide(self, history, rows, *outputs, issues=()):
        ask = Mock(side_effect=outputs)
        result = reply_turn.decide(history, rows, issues, NOW, ['行，我看看'], ask, Mock(return_value='[]'))
        return result, ask

    def test_correction_requested_and_reviewed_against_same_object(self):
        rows = [incoming('翻译订单记录'), incoming('我说最后一版，标题是订单汇总')]
        result, ask = self.decide([], rows,
                                  plan(([1, 2], 'general', '只翻译订单汇总', '')),
                                  'Order Summary', '{"ok":true}')
        self.assertEqual(result['text'], 'Order Summary')
        for call in ask.call_args_list[1:]:
            self.assertIn('只翻译订单汇总', call.args[0][-1]['content'])

    def test_unresolved_reference_asks_one_targeted_question(self):
        history = [incoming('比较这两份'), outgoing('初稿和定稿都看过了？', None)]
        result, _ = self.decide(history, [incoming('刚才那个')],
                                plan(([1], 'clarify', '指代不清', '初稿还是定稿')),
                                '你说初稿还是定稿？', '{"ok":true}')
        self.assertEqual((result['tier'], result['text']), ('clarify', '你说初稿还是定稿？'))

    def test_two_independent_requests_and_later_normal_message(self):
        result, ask = self.decide([], [incoming('帮我审批'), incoming('PDF 怎么转图片')],
                                  plan(([1], 'handoff', '本人审批', ''),
                                       ([2], 'general', 'PDF 转图片', '')),
                                  '用导出功能选 PNG 就行。', '{"ok":true}')
        self.assertEqual(result['issues'], ['帮我审批'])
        self.assertIn('PNG', result['text'])
        self.assertNotIn('帮我审批', ask.call_args_list[1].args[0][-1]['content'])
        next_turn, _ = self.decide([], [incoming('另外，二维码是什么')],
                                  plan(([1], 'general', '二维码定义', '')),
                                  '一种用图案编码信息的条码。', '{"ok":true}',
                                  issues=[{'text': '帮我审批'}])
        self.assertEqual(next_turn['issues'], [])
        self.assertEqual(next_turn['tier'], 'free')

    def test_exact_title_correction_passes_only_new_request_to_generation(self):
        rows = [incoming('把标题订单记录翻译成英文'), incoming('我说的是最后一版，标题是订单汇总')]
        result, ask = self.decide([], rows, 'Order Summary', '{"ok":true}')
        self.assertEqual(result['text'], 'Order Summary')
        for call in ask.call_args_list:
            prompt = call.args[0][-1]['content']
            self.assertIn('订单汇总', prompt)
            if prompt.startswith('{'):
                self.assertNotIn('订单记录', json.loads(prompt)['当前消息'])
            else:
                self.assertNotIn('订单记录', prompt)

    def test_missing_final_title_has_a_specific_clarification(self):
        history = [incoming('把昨天那版的标题订单记录翻译成英文'), outgoing('Order Records')]
        result, _ = self.decide(history, [incoming('我说的是最后一版')],
                                '最后一版的标题发我一下？', '{"ok":true}')
        self.assertEqual(result['tier'], 'clarify')

    def test_singular_reference_with_explicit_alternatives_is_not_expanded(self):
        history = [incoming('给我两个词'), outgoing('Sunny. / Rainy.')]
        result, _ = self.decide(history, [incoming('刚才那个，法语怎么说')],
                                plan(([1], 'general', '两个词都翻译', '')),
                                '你说 Sunny 还是 Rainy？', '{"ok":true}')
        self.assertEqual(result['tier'], 'clarify')

    def test_invalid_partition_does_not_silently_lose_item(self):
        result, _ = self.decide([], [incoming('一'), incoming('二')],
                                plan(([1], 'general', '一', '')))
        self.assertEqual(result['status'], 'retry')


class ListenerTests(unittest.TestCase):
    def test_minimized_wechat_waits_for_idle_then_wakes(self):
        """11/19 真机：人切窗误把微信最小化，监听跟着退出。改为：有待回复时等键鼠停够再由程序切回微信；换了窗口仍停。"""
        from agent.tools.window import WindowInfo
        main = WindowInfo(7, '微信', 'Qt', 1, True, False)
        mini = WindowInfo(7, '微信', 'Qt', 1, True, True)
        other = WindowInfo(8, '微信', 'Qt', 1, True, False)

        class Done(Exception):
            pass
        state = {'calls': 0, 'mini': False, 'idle': 0}

        def prepare(hwnd):
            state['calls'] += 1
            state['mini'] = state['calls'] == 1    # 启动后人把微信最小化；第二次是程序唤醒、窗口还原
            return True

        def idle():
            state['idle'] += 1
            return 5 if state['idle'] <= 3 else 90
        with TemporaryDirectory() as tmp:
            store = ReplyStore(Path(tmp) / 'reply.db', mode='send')
            store.discover(A, 1)
            store.observe(A, [incoming('在吗')])
            logs = []
            adapter = Mock(unique=Mock(return_value=True), preview=Mock(return_value=None))
            with (patch.object(wechat_reply_flow, 'WeChatUIA', return_value=adapter),
                  patch.object(wechat_reply_flow.time, 'sleep'),
                  patch.object(trigger, 'prepare', side_effect=prepare),
                  patch.object(wechat_reply_flow, 'idle_seconds', side_effect=idle),
                  patch.object(wechat_reply_flow, 'check_responsive', return_value=(True, '')),
                  patch.object(wechat_reply_flow, 'capture_window', return_value=None),
                  patch.object(wechat_reply_flow.graph, '_wechat_windows',
                               side_effect=lambda: [] if state['mini'] else [main]),
                  patch.object(wechat_reply_flow, 'find_windows', return_value=[mini]),
                  patch.object(wechat_reply_flow.Listener, 'process', side_effect=Done) as process):
                with self.assertRaises(Done):
                    wechat_reply_flow.Listener(7, [A], store, preview=False, log=logs.append).run(60)
            self.assertEqual(logs, ['有待回复，微信被最小化：等键鼠停 60 秒后再接管', '90 秒没人碰键鼠，把微信切回前台'])
            self.assertEqual(state['calls'], 2)
            process.assert_called_once_with(A)
            with (patch.object(wechat_reply_flow, 'WeChatUIA', return_value=adapter),
                  patch.object(wechat_reply_flow.time, 'sleep'),
                  patch.object(trigger, 'prepare', return_value=True),
                  patch.object(wechat_reply_flow, 'check_responsive', return_value=(True, '')),
                  # 主窗口换成了另一个：不是最小化，必须停
                  patch.object(wechat_reply_flow.graph, '_wechat_windows', side_effect=[[main], [other], [other]]),
                  patch.object(wechat_reply_flow, 'find_windows', return_value=[mini, other])):
                with self.assertRaises(RuntimeError):
                    wechat_reply_flow.Listener(7, [A], store, preview=False, log=logs.append).run(60)

    def test_preview_change_rings_doorbell_when_badge_was_cleared(self):
        """手机上看过的消息，电脑角标会消失；会话行预览变了也要去看。"""
        with TemporaryDirectory() as tmp:
            store = ReplyStore(Path(tmp) / 'reply.db', mode='send')
            store.observe(A, [outgoing('上次')])
            adapter = Mock(preview=Mock(side_effect=['甲 上次 17:00', '甲 在吗 17:05']))
            with patch.object(wechat_reply_flow, 'WeChatUIA', return_value=adapter):
                listener = wechat_reply_flow.Listener(7, [A], store, preview=False)
                listener._poll_previews()
                self.assertEqual(store.scheduled([A]), [])
                listener.next_preview = 0
                listener._poll_previews()
            self.assertEqual(store.scheduled([A]), [A])

    def test_open_failure_backs_off_instead_of_locking(self):
        with TemporaryDirectory() as tmp:
            store = ReplyStore(Path(tmp) / 'reply.db', mode='send')
            store.discover(A, 1)
            adapter = Mock(unique=Mock(return_value=True), open=Mock(return_value=False))
            logs = []
            with (patch.object(wechat_reply_flow, 'WeChatUIA', return_value=adapter),
                  patch.object(wechat_reply_flow.Listener, '_single_window', return_value=True),
                  patch.object(trigger, 'prepare', return_value=True)):
                listener = wechat_reply_flow.Listener(7, [A], store, preview=False, log=logs.append)
                for _ in range(3):
                    listener.process(A)
            self.assertTrue(store.can_reply(A))
            self.assertEqual(store.scheduled([A]), [A])
            self.assertEqual(listener._ready([A]), [])      # 退避期内先不重试
            self.assertIn('第 3 次', logs[-1])

    def test_file_transfer_simulation_does_not_answer_own_reply(self):
        """模拟模式：角标不作数、旧气泡算边界；手机来的算来信，程序自己发的回复不再登记成来信。"""
        def bubble(text):
            return dict(who='us', kind='text', at=NOW, text=text)
        screens = [[bubble('旧话')], [bubble('旧话'), bubble('在吗')],
                   [bubble('旧话'), bubble('在吗'), bubble('在的')]]
        name = trigger.SIM_NAME
        saved = dict(trigger.SIM)
        self.addCleanup(lambda: trigger.SIM.update(saved))
        with TemporaryDirectory() as tmp:
            store = ReplyStore(Path(tmp) / 'reply.db', mode='send')
            adapter = Mock(unique=Mock(return_value=True), selected=Mock(return_value=True))
            read = lambda i: {'ok': True, 'source': 'uia', 'reason': 'ok', 'msgs': [dict(m) for m in screens[i]]}
            badge = [{'title': name, 'dot_only': False, 'box': (0, 0, 1, 1)}]
            with (patch.object(wechat_reply_flow, 'WeChatUIA', return_value=adapter),
                  patch.object(wechat_reply_flow.Listener, '_single_window', return_value=True),
                  patch.object(trigger, 'WHITELIST', [name]),
                  patch.object(trigger, 'open_title', return_value=name),
                  # 真机 11/18：先看到角标 1、读屏时手机消息还没出来，账本判边界对不上并锁会话
                  patch.object(wechat_reply_flow, 'find_unread_rows', side_effect=[badge, badge, []]),
                  patch.object(wechat_reply_flow, '_badge_count', return_value=1),
                  # trigger 和新流程共用同一个 messages 模块：依次是开模拟、基线、手机来信、我方回复
                  patch.object(trigger.messages, 'read_chat', side_effect=[read(0), read(0), read(1), read(2)])):
                print(trigger.sim_start(7))
                listener = wechat_reply_flow.Listener(7, [name], store, preview=False)
                listener._collect(None)
                self.assertIsNone(store.pending(name))
                listener._collect(None)
                turn = store.pending(name)
                self.assertEqual([m['text'] for m in turn['incoming']], ['在吗'])
                self.assertEqual(store.scheduled([name]), [name])
                store.finish(turn['id'], 'sent', 'ok')
                trigger.sim_sent({'to': name, 'msg': '在的', 'tier': ''})
                listener._collect(None)
                self.assertIsNone(store.pending(name))
                self.assertEqual(store.review_items(name), [])

    def test_simulation_only_touches_file_transfer_when_mixed_with_friend(self):
        """好友和文件传输助手一起跑：模拟改标只作用于文件传输助手，好友角标照常登记。"""
        name = trigger.SIM_NAME
        saved = dict(trigger.SIM)
        self.addCleanup(lambda: trigger.SIM.update(saved))
        sim_old = [dict(who='us', kind='text', at=NOW, text='旧话')]
        sim_new = sim_old + [dict(who='us', kind='text', at=NOW, text='手机来的')]
        friend = [outgoing('上次的'), incoming('好友的新话'), outgoing('我自己发的', origin=None)]
        screens = {name: [sim_old, sim_old, sim_new], A: [friend]}
        current = {'title': name}

        def read_chat(hwnd, title, **kwargs):
            return {'ok': True, 'source': 'uia', 'reason': 'ok',
                    'msgs': [dict(m) for m in screens[title].pop(0)]}
        with TemporaryDirectory() as tmp:
            store = ReplyStore(Path(tmp) / 'reply.db', mode='send')
            adapter = Mock(unique=Mock(return_value=True), selected=Mock(return_value=True))
            rows = [{'title': A, 'dot_only': False, 'box': (0, 0, 1, 1)},
                    {'title': name, 'dot_only': False, 'box': (0, 0, 1, 1)}]
            with (patch.object(wechat_reply_flow, 'WeChatUIA', return_value=adapter),
                  patch.object(wechat_reply_flow.Listener, '_single_window', return_value=True),
                  patch.object(trigger, 'WHITELIST', [A, name]),
                  patch.object(trigger, 'open_title', side_effect=lambda shot: current['title']),
                  patch.object(wechat_reply_flow, 'find_unread_rows', return_value=rows),
                  patch.object(wechat_reply_flow, '_badge_count', return_value=1),
                  patch.object(trigger.messages, 'read_chat', side_effect=read_chat)):
                print(trigger.sim_start(7))
                listener = wechat_reply_flow.Listener(7, [A, name], store, preview=False)
                listener._collect(None)                      # 停在文件传输助手：建基线
                friend_view = listener._read(A)
                # 好友会话不经模拟改标：对方仍是 them，本人手发的仍是 us
                self.assertEqual([m['who'] for m in friend_view['msgs']], ['us', 'them', 'us'])
                self.assertIsNone(friend_view['msgs'][2].get('origin'))
                listener._collect(None)                      # 手机来信
                self.assertEqual([m['text'] for m in store.pending(name)['incoming']], ['手机来的'])
            with store.transaction() as db:
                unread = {row['contact']: row['unread'] for row in db.execute('SELECT contact,unread FROM session')}
            from agent.tools.reply_store import contact_key
            self.assertEqual(unread[contact_key(A)], 1)      # 好友角标照常登记
            self.assertIsNone(unread[contact_key(name)])     # 文件传输助手角标不作数

    def test_generation_and_delivery_share_real_persistent_batch(self):
        with TemporaryDirectory() as tmp:
            store = ReplyStore(Path(tmp) / 'reply.db', mode='send')
            store.discover(A, 1)
            view = {'ok': True, 'source': 'uia', 'quiet': True, 'reason': 'ok',
                    'msgs': [incoming('翻译你好')]}
            adapter = Fake()
            adapter.hwnd = 7
            with (patch.object(wechat_reply_flow, 'WeChatUIA', return_value=adapter),
                  patch.object(wechat_reply_flow.Listener, '_safe_target', return_value=True),
                  patch.object(wechat_reply_flow.Listener, '_single_window', return_value=True),
                  patch.object(wechat_reply_flow.Listener, '_read', return_value=view),
                  patch.object(adapter, 'unique', return_value=True, create=True),
                  patch.object(wechat_reply_flow, 'check_responsive', return_value=(True, '')),
                  patch.object(trigger, 'prepare', return_value=True),
                  patch.object(trigger, 'remember', return_value=view['msgs']),
                  patch.object(trigger, 'read_view', return_value=view),
                  patch.object(wechat_reply_flow.messages, 'wait_quiet', return_value=view),
                  patch.object(trigger, 'ask_qwen', side_effect=[
                      plan(([1], 'general', '翻译你好', '')), 'Hello', '{"ok":true}']),
                  patch.object(send, '_uia_count', side_effect=[0, 1]),
                  patch.object(send.time, 'sleep'),
                  patch.object(send, 'press') as press):
                listener = wechat_reply_flow.Listener(7, [A], store, preview=False)
                listener.started = NOW
                listener.process(A)
                self.assertEqual(press.call_count, 1)
            self.assertEqual(store.scheduled([A]), [])
            store.recover()
            self.assertEqual(store.review_items(A), [])


if __name__ == '__main__':
    unittest.main()

