"""回复：读到的聊天记录 → 决定发不发、发什么（D15，agent 核心，由用户写）。

在流程里的位置：trigger.run_one 打开会话之后、graph 之前，替换掉写死的 make_task。
graph 里的 rules 要求输入的字和 task['msg'] 一字不差，所以回复内容必须在进 graph 之前定好。

借鉴的 4 条（11/11 读 5 个项目）：
  1. 先分流再生成：图片、语音、文件、已停住的会话，不调模型          → route
  2. 多层防线：提示词管怎么说 + 写完再查（结合对方的问题查）+ 不过就重写 → SYSTEM_PROMPT / check_reply
  3. 转人工是终点：只发一次"正在忙"，之后这个会话停住，不再自动回        → write_reply 的 pause
  4. 连发合并：等对方停几秒再回（wait_quiet 在工具层，不在这里）

本文件不碰窗口、不调键鼠，全是纯函数：输入聊天记录，输出决定。所以能离线拿假对话测。

消息格式（工具层 read_chat 给出，OCR 兜底也给同样格式）：
  {'who': 'them' | 'us' | 'none',
   'kind': 'text' | 'sticker' | 'image' | 'voice' | 'file' | 'time',
   'text': str,                      # 文字消息的全文（多行保留 \\n）；表情包等是"动画表情"之类
   'at': datetime | None}            # 按最近的时间线推出来的时间，推不出是 None
"""
from datetime import datetime, timedelta

CONTEXT_N = 6                       # 旧消息最多带几条（11/11 定：五六句）
MAX_AGE = timedelta(hours=1)        # 超过这么久的旧消息不带，免得把旧话题当成现在的（建议值，可改）
MAX_TRIES = 3                       # 第一次 + 重写两次，都不过就转人工
BUSY = '正在忙，稍后回复您'
STICKER_REPLY = '怎么啦？'
HUMAN = '转人工'                     # 模型判断该转人工时只输出这三个字


def split_new(msgs: list[dict], now: datetime,
              n: int = CONTEXT_N, max_age: timedelta = MAX_AGE) -> tuple[list[dict], list[dict]]:
    """把聊天记录分成"旧的上下文"和"这次要回的新消息"。

    输入：msgs 按时间从早到晚；now 当前时间（传进来，方便离线测）。
    输出：(context, new)
      new     = 我方最后一条之后，对方发的所有消息（时间线不算）
      context = new 之前最多 n 条，去掉时间线，去掉比 now 早 max_age 以上的
    """
    # TODO 1：从后往前找我方（who == 'us'）最后一条的位置，它后面的就是 new 的范围
    # TODO 2：new 里只留 who == 'them' 的
    # TODO 3：context 取 new 之前的部分，丢掉 kind == 'time'，再丢掉过期的（at 是 None 时怎么办？想一下）
    # TODO 4：context 只留最后 n 条

    start = 0
    for i in range(len(msgs)-1,-1,-1):
      if msgs[i]['who'] == 'us':
        start = i+1
        break
    
    them = []
    new = msgs[start:]
    for msg in new:
      if msg['who'] == 'them':
        them.append(msg)
    new = them

    context = []
    for msg in msgs[:start]:
      if msg['kind'] == 'time':
        continue
      # 时间推不出来的留下：一屏里没有时间线是常事，全丢掉模型就一句上下文都拿不到（11/12 检查发现）
      if msg['at'] is not None and now - msg['at'] > max_age:
        continue
      context.append(msg)

    return (context[-n:] if n> 0 else [],new)
    
def route(new: list[dict], paused: bool) -> tuple[str, str]:
    """生成之前先分流：这次要不要调模型。

    输入：new 是 split_new 给的新消息；paused 表示这个会话已经转过人工。
    输出：(去向, 原因)，去向是下面四个之一
      'skip'    不回：没有新消息，或会话已停住（停住后不再发任何东西，包括"正在忙"）
      'human'   转人工：新消息里有图片、语音、文件
      'sticker' 只有表情包、没有文字：回 STICKER_REPLY
      'write'   有文字：调模型写（表情包混在文字里时，只回文字）
    """
    # TODO：按上面的顺序判断。想清楚 paused 为什么要排在最前面
   
    if paused:
        return ('skip', '会话已暂停')

    if not new:
        return ('skip', '没有新消息')

    has_text = False

    for msg in new:
        if msg['kind'] in ('voice', 'image', 'file'):
            return ('human', '新消息包含图片、语音或文件')

        if msg['kind'] == 'text':
            has_text = True

    if has_text:
        return ('write', '有文字消息')

    return ('sticker', '只有表情包，没有文字')


# 提示词分三块（Chatwoot 的分法）：护栏 = 哪些不答；准则 = 怎么说；场景 = 碰到什么做什么。
# 语气用真实例句教，不用"随和、简短"这种形容词（wechat-persona-agent 的经验）。
# 坑（demo_reply 实测）：规矩写太松会替你答应事情，写太紧连"收到"都转人工，改完要拿固定测试集量。
SYSTEM_PROMPT = """\
  你是我的聊天回复助手，根据提供的聊天记录拟写回复。
护栏（哪些事不替我回，只输出 {human}）
- 对方要求借钱、转账或提供验证码时，只输出 {human}。
- 对方要求我作出承诺，但资料不足以判断时，只输出 {human}。
- 对方需要我提供任何东西，或者确认某个事实的时候，输出{human}。
- 不编造我的日程、位置、价格或其他事实。

回复准则（长度、口语、不用表情、只输出回复本身）
- 使用简短、自然的中文口语，通常一到两句。
- 不使用表情符号。
- 只输出要发送的回复正文，不加引号、前缀或解释。
- 需要转人工时，只输出 {human}，不附加其他文字。

场景（对方在问时间地点价格怎么办、对方只打招呼怎么办……）
- 对方只打招呼时，简短回应。
- 对方只发数字、问号、单个字这类看不出意思的消息（如"1""？""嗯？"），只输出：{sticker}
- 对方询问时间、地点或价格时，只依据提供的资料回答；资料不足时，只输出 {human}。
- 对方连续发来多条文字时，结合这些消息一起回复。

{tone}
"""
# 语气例句是真实聊天，不写在这个会进 git 的文件里：放 agent/tone.local.json（*.local.json 已被忽略），
# 由调用方读出来通过 tone 传进来
TONE_HEAD = '我平时说话的样子（只学语气和长短，不要照搬里面的内容）：\n'
TONE_DEFAULT = '语气：像熟人之间随手回的，简短自然。'


def build_messages(context: list[dict], new: list[dict], tone: list[str], kb_hits: list[dict]) -> list[dict]:
    """拼给模型的消息。

    输入：context / new 来自 split_new；tone 是语气例句；kb_hits 是知识库 search 的结果（开关关着就是空列表）。
    输出：OpenAI 格式的 messages：[{'role': 'system', ...}, {'role': 'user', ...}]
    """
    # TODO 1：system = SYSTEM_PROMPT 填上 human 和 tone（tone 为空时给一句默认语气）
    # TODO 2：user 里写清楚三段：之前的聊天（标上"我"/"对方"）、对方这次说的、资料（kb_hits 有才写）
    #         为什么要分开写"之前的"和"这次的"：不分开，模型会去回早就聊完的事
    # 坑（11/12 检查发现）：默认语气那句不能接在例句后面，会被当成一句例句
    if tone:
      tone_text = TONE_HEAD + '\n'.join(tone)
    else:
      tone_text = TONE_DEFAULT
    system_text = SYSTEM_PROMPT.format(
      human = HUMAN,
      sticker = STICKER_REPLY,
      tone = tone_text
    )

    context_lines = []
    for msg in context:
      if msg['who'] == 'us':
        speaker = '我'
      else:
        speaker = '对方'
      context_lines.append(f"{speaker}:{msg['text']}")
    context_text = '\n'.join(context_lines) or '（无）'

    new_lines = []
    for msg in new:
      if msg['kind'] == 'text':        # 表情包的"动画表情"四个字不给模型，免得它去回这四个字
        new_lines.append(msg['text'])
    new_text = '\n'.join(new_lines)
    
    kb_lines = []

    for hit in kb_hits:
        kb_lines.append(hit['content'])
    kb_text = '\n'.join(kb_lines)

    user_text = (
      f"【之前的聊天】\n{context_text}\n\n"
      f"【对方消息】\n{new_text}"
    )

    if kb_text:
      user_text += f"\n\n【资料】\n{kb_text}"
    return[
      {'role':'system','content':system_text},
      {'role':'user','content':user_text}
    ] 



def check_reply(text: str, new: list[dict]) -> tuple[bool, str]:
    """写完再查：这句能不能发。

    输入：text 模型原样输出；new 对方这次的消息（要结合它来查）。
    输出：(能发, 原因)。能发时原因写 'ok'。
    """
    # TODO 1：格式关：空的、带引号或"回复："前缀的、句子里夹着"转人工"的、有读不出来的符号的
    # TODO 2：结合对方的问题查：对方在说借钱、转账、验证码、合同、帮忙办事时，
    #         回复里哪怕只有"行""好""可以""没问题"，也算替我答应了 → 不能发
    #         （wechat-persona-agent 的做法：同一个"行"，回在借钱后面就是答应借钱）
    # 想一下：这里只挡"答应"，还是对方一提钱就整句都不发？两种各会误伤什么
    cleaned = text.strip()

    if not cleaned:
      return(False,'回复为空')
    if cleaned.startswith(('回复：', '回复:')):
        return (False, '含回复前缀')

    quote_pairs = (
        ('"', '"'),
        ("'", "'"),
        ('“', '”'),
        ('‘', '’')
    )

    for left, right in quote_pairs:
        if cleaned.startswith(left) and cleaned.endswith(right):
            return (False, '整条回复被引号包住')

    # 换行粘进微信输入框会变成多行草稿，发送和读回都对不上；让模型重写成一段
    if '\n' in cleaned:
        return (False, '回复里有换行')

    if '转人工' in cleaned or '\uFFFD' in cleaned:
      return(False,'含转人工标记和异常符号')
    
    new_lines = []
    for msg in new:
        if msg['kind'] == 'text':
            new_lines.append(msg['text'])

    question = '\n'.join(new_lines)

    # 坑（11/12 检查发现）：只认"借钱"两个字连着写，"借点钱""借我500""周转一下"都漏了。
    # 所以认单个"借"字和几种常见说法，宁可多拦：拦错只是多重写几次或转人工，不会误发
    sensitive_words = (
        '借', '周转', '垫', '转账', '转钱', '打钱', '汇款', '还钱',
        '验证码', '密码', '合同', '签字', '盖章', '帮我', '帮忙', '帮个忙'
    )
    agree_words = ('行', '好', '可以', '没问题', '嗯', '发你', '给你', '转你', '马上', 'ok', 'OK')
    # 这些词里的"好""行"不是答应，先去掉再查，不然"不好意思，在忙"也会被拦
    not_agree = ('不好意思', '不太好', '不好', '不行', '不可以', '银行', '你好', '您好')

    sensitive = False
    for word in sensitive_words:
        if word in question:
            sensitive = True
            break

    if sensitive:
        rest = cleaned
        for word in not_agree:
            rest = rest.replace(word, '')
        for word in agree_words:
            if word in rest:
                return (False, '敏感请求的回复中包含答应用语')

    return (True, 'ok')


def write_reply(msgs: list[dict], now: datetime, paused: bool, tone: list[str],
                ask, search) -> dict:
    """回复节点：串起上面几步，给出最终决定。

    输入：
      msgs / now / paused  同上
      tone    语气例句
      ask     调模型的函数：ask(messages) -> str。真机传 Qwen，离线测传假的
      search  知识库：search(问题) -> [{content, source, score}]，开关关着时返回 []
    输出：{'send': bool, 'text': str, 'reason': str, 'pause': bool}
      send=True 时 text 就是要交给 graph 的 task['msg']；pause=True 表示从此停住这个会话
    """
    # TODO 1：split_new → route；'skip' 直接不发
    # TODO 2：'human'：发 BUSY，pause=True
    # TODO 3：'sticker'：发 STICKER_REPLY
    # TODO 4：'write'：最多 MAX_TRIES 次 ask → check_reply，过了就发；
    #         模型自己输出 HUMAN，或次数用完都没过 → 按 'human' 处理
    # 每个分支都写清 reason，日志里要能看出为什么发、为什么不发
    context,new = split_new(msgs,now)
    destination,reason = route(new,paused)

    if destination == 'skip':
      return {
        'send':False,
        'text':'',
        'reason':reason,
        'pause':paused
      }

    if destination == 'human':
      return {
        'send':True,
        'text':BUSY,
        'reason':reason,
        'pause':True
      }

    if destination == 'sticker':
      return {
        'send':True,
        'text':STICKER_REPLY,
        'reason':reason,
        'pause':paused
      }
    
    if destination == 'write':
      question = '\n'.join(
        msg['text'] for msg in new
        if msg['kind'] == 'text'
    )
      kb_hits = search(question)
      messages = build_messages(context, new, tone, kb_hits)

      reason = '回复检查未通过'

      for attempt in range(MAX_TRIES):
        # 模型连不上不是对方的问题：不发"正在忙"、也不停住，下一轮还能再试
        try:
            text = (ask(messages) or '').strip()
        except Exception as e:
            return {
                'send': False,
                'text': '',
                'reason': f'模型调用失败：{type(e).__name__}',
                'pause': paused
            }

        if text == HUMAN:
            reason = '模型要求转人工'
            break

        allowed, reason = check_reply(text, new)

        if allowed:
            return {
                'send': True,
                'text': text,
                'reason': 'ok',
                'pause': False
            }

        messages.append({
            'role': 'assistant',
            'content': text
        })
        messages.append({
            'role': 'user',
            'content': (
                f'这条回复不能发送，原因：{reason}。'
                f'请按原规则重写；无法安全回复时，只输出 {HUMAN}。'
            )
        })

      return {
        'send': True,
        'text': BUSY,
        'reason': reason,
        'pause': True
       } 