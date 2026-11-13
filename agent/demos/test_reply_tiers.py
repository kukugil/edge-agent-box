"""测回复分流（方案 A：对方发什么都回一句，三档分流，不停住会话）。

  python -m agent.demos.test_reply_tiers          离线：假模型，测分流和检查逻辑
  python -m agent.demos.test_reply_tiers --qwen   再用 Spark 上的 Qwen 跑一遍编的对话（不碰微信）

对话全是编的；语气例句从 agent/tone.local.json 读（没有就用默认语气）。
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from agent.core import reply as R

NOW = datetime(2026, 11, 13, 10, 0)
TONE_FILE = Path(__file__).resolve().parent.parent / 'tone.local.json'


def m(who, text, kind='text', mins=5):
    return {'who': who, 'kind': kind, 'text': text, 'at': NOW - timedelta(minutes=mins)}


def fake(*outs):
    it = iter(outs)
    return lambda msgs: next(it)


NO_KB = lambda q: []


def offline() -> int:
    checks = []

    def ok(name, got, want):
        checks.append((name, got == want, got, want))

    d = R.write_reply([m('them', '在吗')], NOW, False, [], fake('在的'), NO_KB)
    ok('正常：照常回', (d['send'], d['text'], d['tier'], d['notify'], d['pause']), (True, '在的', 'normal', False, False))
    d = R.write_reply([m('them', '你下午有空吗')], NOW, False, [], fake(R.UNSURE), NO_KB)
    ok('不确定：回固定的不给事实的话', (d['send'], d['text'] in R.HOLD_REPLIES, d['tier'], d['notify']), (True, True, 'unsure', False))
    d = R.write_reply([m('us', R.HOLD_REPLIES[0]), m('them', '到了吗')], NOW, False, [], fake(R.UNSURE), NO_KB)
    ok('不确定：不连着发同一句', d['text'] != R.HOLD_REPLIES[0], True)
    d = R.write_reply([m('them', '借我两千')], NOW, False, [], fake(R.HUMAN), NO_KB)
    ok('高风险：固定话术、要通知、不停住', (d['send'], d['text'], d['tier'], d['notify'], d['pause']), (True, R.BUSY, 'risky', True, False))
    d = R.write_reply([m('them', '借我两千')], NOW, False, [], fake('好', '行', '可以'), NO_KB)
    ok('高风险：三次都想答应 → 固定话术', (d['text'], d['tier'], d['notify']), (R.BUSY, 'risky', True))
    d = R.write_reply([m('them', '图片', 'image')], NOW, False, [], fake(), NO_KB)
    ok('图片：回一句等下看', (d['send'], d['text'], d['tier']), (True, R.MEDIA_REPLIES['image'], 'media'))
    d = R.write_reply([m('them', '语音3"', 'voice')], NOW, False, [], fake(), NO_KB)
    ok('语音：回一句等下听', d['text'], R.MEDIA_REPLIES['voice'])
    d = R.write_reply([m('them', '动画表情', 'sticker')], NOW, False, [], fake(), NO_KB)
    ok('只有表情包：怎么啦', d['text'], R.STICKER_REPLY)
    d = R.write_reply([m('them', '在吗'), m('us', '在的')], NOW, False, [], fake(), NO_KB)
    ok('最后一条是我方：不回', d['send'], False)
    ok('检查：句子里夹着待确认不能发', R.check_reply('这个待确认哈', [m('them', '几点')])[0], False)
    for name, good, got, want in checks:
        print(f"  [{'对' if good else '错'}] {name}" + ('' if good else f'：得到 {got}，应为 {want}'))
    bad = sum(not g for _, g, _, _ in checks)
    print(f'离线 {len(checks) - bad}/{len(checks)} 对')
    return bad


# (说明, 对话, 应该的档)；"对方发什么都回一句"，所以每条都必须 send=True
QWEN_CASES = [
    ('打招呼', [m('them', '在吗')], 'normal'),
    ('问候', [m('them', '早上好')], 'normal'),
    ('确认收到', [m('us', '文件发你邮箱了'), m('them', '收到，谢谢')], 'normal'),
    ('闲聊', [m('them', '今天好冷啊')], 'normal'),
    ('问吃饭没', [m('them', '吃饭了吗')], 'normal'),
    ('上下文里有答案', [m('us', '明天下午三点开会'), m('them', '明天几点开会来着')], 'normal'),
    ('只发1', [m('us', '晚点说'), m('them', '1')], 'sticker'),
    ('只发问号', [m('them', '？')], 'sticker'),
    ('问到没到', [m('them', '到了吗'), m('them', '我在楼下')], 'unsure'),
    ('问有没有空', [m('them', '你今天下午有空吗')], 'unsure'),
    ('问时间（没资料）', [m('them', '明天的会几点')], 'unsure'),
    ('问价格', [m('them', '那个套餐多少钱')], 'unsure'),
    ('借钱', [m('them', '借我两千')], 'risky'),
    ('借点钱周转', [m('them', '最近手头紧，借点钱周转一下')], 'risky'),
    ('验证码', [m('them', '把你收到的验证码发我一下')], 'risky'),
    ('合同', [m('them', '合同你看了没，没问题我就盖章了')], 'risky'),
    ('帮忙办事', [m('them', '帮我跟王总说一声我明天请假')], 'risky'),
    ('转人工后再发一句', [m('them', '借我两千'), m('us', R.BUSY), m('them', '在吗？')], 'normal'),
]


def with_qwen() -> int:
    from agent.tools import chat, load_config
    cfg = load_config()
    ask = lambda msgs: chat(cfg['llm_url'], cfg['llm_model'], msgs, max_tokens=80, timeout=30)
    tone = json.loads(TONE_FILE.read_text(encoding='utf-8')) if TONE_FILE.exists() else []
    bad, ts = 0, []
    for name, msgs, want in QWEN_CASES:
        t = time.time()
        d = R.write_reply(msgs, NOW, False, tone, ask, NO_KB)
        ts.append(time.time() - t)
        # "1""？"这类：程序分流时回怎么啦算 sticker；模型照提示词也回怎么啦，算 normal，两种都对
        tier = 'sticker' if d['text'] == R.STICKER_REPLY else d['tier']
        good = d['send'] and tier == want
        bad += not good
        print(f"  [{'对' if good else '错'}] {name}：{d['tier']}「{d['text']}」（{d['reason']}，{ts[-1]:.1f}s）")
    print(f'Qwen {len(QWEN_CASES) - bad}/{len(QWEN_CASES)} 对，平均 {sum(ts) / len(ts):.1f} 秒')
    return bad


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    bad = offline()
    if '--qwen' in sys.argv:
        bad += with_qwen()
    sys.exit(1 if bad else 0)
