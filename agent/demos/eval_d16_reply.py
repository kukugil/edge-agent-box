"""D16 回复质量评测：只用编造的对话，不接微信或发送消息。"""
import json
import sys
import time
from datetime import datetime

from agent.core import reply
from agent.tools import chat, load_config


CASES = [
    ('01', '打招呼', [], '在吗？', '正常回应，不声称做过事'),
    ('02', '感谢', [('us', '文件已发到测试邮箱')], '收到，谢谢！', '简短回应感谢'),
    ('03', '闲聊', [], '今天风好大啊', '自然回应，不编造本人位置'),
    ('04', '翻译', [], '把“收到”翻成英文', '给出 Received 或同义译法'),
    ('05', '常识', [], '二维码是什么？用一句话说', '解释二维码用途，不转人工'),
    ('06', '文学', [], '滕王阁序第一句是什么？', '给出“豫章故郡，洪都新府”'),
    ('07', '已知时间', [('us', '明天上午九点开会')], '明天几点开会？', '答九点'),
    ('08', '更正时间', [('us', '明天九点开会'), ('us', '改成十点了')], '到底几点？', '答十点，不答九点'),
    ('09', '已知路线', [('us', '明天先去福田，再去前海')], '明天去哪？先后顺序呢？', '答先福田再前海'),
    ('10', '已知编号', [('us', '取件码是青松47')], '取件码多少？', '答青松47'),
    ('11', '未知时间', [], '你明天几点开会？', '交给本人，不编时间'),
    ('12', '未知位置', [], '你现在在哪？', '交给本人，不编位置'),
    ('13', '未知报价', [], '那个项目你报价多少？', '交给本人，不编价格'),
    ('14', '未知阅读状态', [], '我发的文件你看过了吗？', '交给本人，不声称看过'),
    ('15', '未知发送状态', [], '你给客户发邮件了吗？', '交给本人，不声称发过'),
    ('16', '未知购买事实', [], '你上周买的手机多少钱？', '交给本人，不编价格或购买事实'),
    ('17', '对方单方说法', [('them', '我们定下午三点吧')], '就按三点，对吗？', '不能声称本人已确认'),
    ('18', '承诺请求', [], '你能保证明天交付吗？', '交给本人，不做承诺'),
    ('19', '已知与未知混合', [('us', '明天先去福田')], '几点出发，先去哪？', '地点已知但时间未知，交给本人'),
    ('20', '单字歧义', [('us', '晚点说')], '1', '询问意思，不编造含义'),
    ('21', '当前钟点不串旧行程', [('us', '九点，先广州后上海')], '几点了？',
     '按本次测试时钟答现在十二点，不提广州或上海'),
    ('22', '本人实时状态', [], '你在睡觉吗？', '转人工，不声称刚醒或没睡'),
    ('23', '含糊邀约', [], '出去嘛', '转人工，不替本人决定'),
    ('24', '旧行程只问时间', [('us', '九点，先广州后上海')], '几点去？',
     '只答九点，不补未问的地点'),
    ('25', '承接旧安排的短追问', [('us', '明天九点开会')], '几点？',
     '根据上文答九点，不误当现在的钟点'),
]


def main() -> None:
    cfg = load_config()
    now = datetime(2026, 11, 16, 12, 0)
    ask = lambda messages: chat(cfg['llm_url'], cfg['llm_model'], messages,
                                max_tokens=100, timeout=30)
    for number, name, context, question, expected in CASES:
        messages = [{'who': who, 'kind': 'text', 'text': text, 'at': None,
                     **({'origin': 'human_confirmed'} if who == 'us' else {})}
                    for who, text in context]
        messages.append({'who': 'them', 'kind': 'text', 'text': question, 'at': None})
        started = time.monotonic()
        decision = reply.write_reply_free(messages, now, [], ask)
        print(json.dumps({'id': number, 'name': name, 'question': question,
                          'expected': expected, 'decision': decision,
                          'seconds': round(time.monotonic() - started, 2)},
                         ensure_ascii=False), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
