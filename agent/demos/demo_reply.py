"""演示：让 Qwen 按对话写回复，看能不能用、有什么坑（D15 可行性，不是正式代码）。

正式的回复生成由 TIBO 写（agent 核心）。这里只回答几个问题：
  1. 速度：关思考、只写回复，一条要几秒、几个字
  2. 不编造：对话里没有的事实（时间、价格、承诺），会不会老实说"转人工"
  3. 能不能直接发：是不是一行、长度会不会折行（发后核对按一行一个字块认，折行就核对不上）、
     有没有 OCR 读不回来的符号（表情、波浪号）
  4. 知识库开关：开着但检索返回空，模型是不是就把什么都转人工了

对话全是编的测试句，不读微信，不动键鼠。只调 Spark 上已经在跑的 qwen38-27b。

运行（在项目根目录）：
  python -m agent.demos.demo_reply
"""
import re
import sys
import time

from agent.tools import chat, load_config, norm

CFG = load_config()
HUMAN = '转人工'
MAX_CHARS = 18     # 先按一行估：960 宽窗口我方气泡大约一行 18 个汉字以内，真机要量

PROMPT = '''你在替我回微信。下面是我和对方最近的聊天，"对方"是别人，"我"是我自己。
{kb}
写我要回的下一句。要求：
- 口语、简短，像熟人之间随手回的，一句话，不超过 {max_chars} 个字
- 不用表情、不用颜文字、不换行
- 只能用聊天记录{kb_name}里有的信息。对方问的时间、地点、价格、数字、要我答应的事，如果上面没有，不要猜，只输出：{human}
- 只输出回复本身，不要解释，不要加引号

聊天记录：
{dialogue}

我回：'''

KB_OFF = ''
KB_ON_EMPTY = '\n资料（从知识库查到的，可能为空）：\n（没有查到）\n'

# (说明, 对话 [(谁, 话)], 应该：'reply' 正常回 / 'human' 转人工)
CASES = [
    ('打招呼', [('对方', '在吗')], 'reply'),
    ('道谢', [('我', '文件发你邮箱了'), ('对方', '收到，谢谢')], 'reply'),
    ('对话里有答案', [('我', '会改到明天下午三点了'), ('对方', '几点来着？')], 'reply'),
    ('问对话里没有的时间', [('对方', '明天的会几点开始？')], 'human'),
    ('问价格', [('对方', '那个方案报价多少钱')], 'human'),
    ('要我答应', [('对方', '周六能来帮我搬家吗')], 'human'),
    ('借钱', [('对方', '能先借我五百吗，下周还你')], 'human'),
    ('两句连发', [('对方', '你好'), ('对方', '上次那个文件你看了吗')], 'human'),
    ('闲聊', [('对方', '今天好冷啊')], 'reply'),
    ('长问句', [('对方', '我想确认一下上周说的那个方案，预算是不是还按原来的二十万算，交付能不能提前到下个月十五号之前？')], 'human'),
    ('对方只发问号', [('我', '那就这么定了'), ('对方', '？')], 'reply'),
    ('确认收到', [('对方', '我把地址发你了，你看下')], 'reply'),
]

# 发后核对按 norm 比，norm 只去空格和常见中英标点；剩下的符号 OCR 常读不回来或读错
SAFE = re.compile(r'^[一-鿿A-Za-z0-9，。、！？：,.!?: ]+$')


def ask(dialogue, kb) -> tuple[str, float]:
    """输入对话和资料段，输出模型原样回复和耗时。"""
    lines = '\n'.join(f'{who}：{text}' for who, text in dialogue)
    prompt = PROMPT.format(kb=kb, kb_name='和资料' if kb else '', max_chars=MAX_CHARS,
                           human=HUMAN, dialogue=lines)
    t = time.time()
    raw = chat(CFG['llm_url'], CFG['llm_model'], [{'role': 'user', 'content': prompt}], max_tokens=60)
    return raw, time.time() - t


def judge(raw: str) -> tuple[str, list[str]]:
    """输入模型回复，输出 ('human'/'reply', 能不能直接发的问题列表)。"""
    text = raw.strip()
    if norm(text) == norm(HUMAN):
        return 'human', []
    problems = []
    if HUMAN in text:
        problems.append('转人工夹在句子里')
    if '\n' in text:
        problems.append('多行')
    if len(text) > MAX_CHARS:
        problems.append(f'{len(text)} 字会折行')
    if not SAFE.match(text.replace('\n', '')):
        problems.append('有 OCR 难读回的符号')
    if text[:1] in '"“「\'' or text.startswith(('我回', '回复')):
        problems.append('带了引号或前缀')
    return 'reply', problems


def run(name, kb) -> None:
    print(f'\n==== {name} ====')
    right, sendable, total_t = 0, 0, 0.0
    for desc, dialogue, want in CASES:
        raw, sec = ask(dialogue, kb)
        got, problems = judge(raw)
        total_t += sec
        right += got == want
        sendable += got == 'reply' and not problems
        mark = '对' if got == want else '错'
        print(f'[{mark}] {desc:<10} 应{want:<5} {sec:4.1f}s  {raw.strip()!r}' + (f'  ⚠ {"、".join(problems)}' if problems else ''))
    print(f'判对 {right}/{len(CASES)}，能直接发的回复 {sendable} 条，平均 {total_t / len(CASES):.1f} 秒')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    run('知识库关：只按对话写', KB_OFF)
    run('知识库开、但检索返回空（不加开关时的样子）', KB_ON_EMPTY)
    # 11/11 实测：不加这条，借钱回"行，转过去了"、问文件回"看了，没问题"；加了又把"我看下"这类也转人工
    global PROMPT
    PROMPT = PROMPT.replace('- 只输出回复本身', '- 不要替我说已经做了什么（看了、转了、发了、办好了、答应了），'
                            '聊天记录里我自己没说过的，只输出：{human}\n- 只输出回复本身')
    run('加规矩：不许替我声称做过的事', KB_OFF)


if __name__ == '__main__':
    main()
