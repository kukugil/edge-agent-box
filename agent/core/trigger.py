"""D9 触发器 + 会话队列：常驻盯微信，有人发消息就醒，一个个回。

流程：盯总未读数 → 变了就扫会话列表的角标 → 挑出要回的 → 每个跑一遍 D8 的主循环 → 再扫，没有了就回去盯。
设计说明见 docs/D9-开工说明.md，要点：
  - 角标负责“发现”，账本 pending 负责“记住”：当前开着的会话来消息会出角标，
    但在微信里随便点一下它就消失。agent 去回别人时第一下就会点掉它，所以扫到过的都要记进 pending，回成功才划掉。
  - 不开线程，回完一个就重扫。
  - 总数字一变就醒（变多变少都醒）：醒一次只扫一遍列表、不调模型，很便宜；扫完没有该回的就接着睡。

pick_sessions 由 TIBO 写（11/7，离线 9/9）；serve 按 TIBO 要求由 Claude 直接写（11/7）。

运行（在项目根目录）：
  python -m agent.core.trigger --offline     不碰微信，测试回复后检查和 pick_sessions
  python -m agent.core.trigger 600           真的盯 600 秒，有白名单好友发消息就回。会动键鼠，跑的时候别碰鼠标键盘
"""
import argparse
import uuid
import ctypes
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from agent.core import graph, messages, reply
from agent.tools import history, journal, recall
from PIL import Image

from agent.tools import capture_window, check_responsive, click, norm, ocr, set_window_size, activate, window_rect, Shot
from agent.core.check import CHAT_TOP, LIST_RIGHT, chat_title, title_matches, layout, is_ours, our_bubbles
from agent.tools.unread import bell_state, find_row, find_unread_rows
from agent.tools import chat, kb, load_config
from agent.tools.notify import notify

POLL = 0.5        # 门铃每隔几秒看一眼：通过线是 5 秒内发现，0.5 秒留足余量
MAX_RETRY = 2     # 同一个会话最多失败几次，超过就放弃，不然一个坏会话会让 agent 永远在它身上打转
COOLDOWN = 60     # 放弃多少秒后解除：11/9 验收里一个会话放弃后整轮都不回，角标还在也不管

CONTACTS = Path(__file__).resolve().parent.parent / 'test_contacts.local.json'
WHITELIST = json.loads(CONTACTS.read_text(encoding='utf-8'))['allowed_chats']


# ---------- 诊断（11/8 查“回复之后第一条偶尔不出角标”，查清后可以删） ----------

HEARTBEAT = 10    # 等门铃时每隔几秒打一行现状


def ts() -> str:
    """带毫秒的时间，对日志用。"""
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]


def chat_sig(shot, lay=None) -> bytes:
    """聊天区的“样子”：缩成小图比像素，只看变没变，不读内容。

    用途：聊天区变了、门铃却没变，就说明来了消息但微信没出角标，正是要抓的那种漏。
    """
    lay = layout(shot.image) if lay is None else lay
    im = shot.image.crop((LIST_RIGHT, CHAT_TOP, shot.image.width, lay['bottom']))
    return im.convert('L').resize((48, 64)).tobytes()


def is_front(hwnd: int) -> bool:
    """微信是不是前台窗口。猜测漏响和“微信在前台、会话开着”有关，所以每行都带上。"""
    return ctypes.windll.user32.GetForegroundWindow() == hwnd


class Tee:
    """print 同时写屏幕和日志文件：用户自己跑，Claude 读文件。"""
    def __init__(self, path: Path):
        self.f = open(path, 'a', encoding='utf-8', buffering=1)
        self.out = sys.stdout

    def write(self, s):
        self.out.write(s)
        self.f.write(s)

    def flush(self):
        self.out.flush()
        self.f.flush()


# ---------- 开着的会话：不靠角标，盯聊天区 ----------
# 11/8 实测：电脑上刚发出消息后极短时间内来的新消息，不出角标，只有任务栏闪一下；
# 在微信里点一下、按回车，已有的角标也会消失。agent 每回一条都正好碰上。
# 所以 agent 回过的那个会话（一直开着）不靠角标：聊天区一变，就看“我们的回复下面有没有东西”。

NEW, NONE, UNKNOWN, OTHER = 'new', 'none', 'unknown', 'other'


def ocr_crop(shot, box: tuple[int, int, int, int]) -> list[dict]:
    """只 OCR 截图里的一块，坐标换回整张截图。"""
    x1, y1 = box[:2]
    # 一行字的窄条（标题条）不放大，见 ocr 的说明；聊天区这种大块照旧
    thin = box[3] - box[1] <= 120
    return [{**it, 'box': [it['box'][0] + x1, it['box'][1] + y1, it['box'][2] + x1, it['box'][3] + y1]}
            for it in ocr(shot.image.crop(box), no_upscale=thin)]


def title_box(shot) -> tuple[int, int, int, int]:
    # 右边 160 不要：那里是语音、视频、“…”图标，裁出来单独 OCR 会认成“?”“#+#”拼进标题（11/9 实测）
    return (LIST_RIGHT, 0, shot.image.width - 160, CHAT_TOP + 10)


def open_title(shot) -> str:
    """右边开着的是哪个会话：只 OCR 标题条，一次。"""
    return chat_title(ocr_crop(shot, title_box(shot)))


def chat_items(shot, lay=None) -> list[dict]:
    """标题条 + 聊天区的 OCR，坐标换回整张截图。

    为什么裁开识别：11/8 实测整张窗口一起 OCR 时，聊天区的字一条都没认出来，裁出来单独识别就有。
    """
    lay = layout(shot.image) if lay is None else lay
    return ocr_crop(shot, title_box(shot)) + ocr_crop(shot, (LIST_RIGHT, CHAT_TOP, shot.image.width, lay['bottom']))


def new_after_reply(shot: Shot, items: list[dict], task: dict, lay=None) -> str:
    """我们刚发的回复下面，还有没有别的东西。不碰窗口，可以离线测。

    输入：shot 截图；items 是 chat_items(shot)；task 是回过的那个任务（to、msg）
    输出：NEW      回复下面有东西：又来了消息
          NONE     回复就是最下面一条
          UNKNOWN  标题对上了，但没认出我们的回复：继续盯，不重发
          OTHER    标题对不上：开着的已经不是这个会话，不用再盯
    为什么数像素不用 OCR：对方发单个字时 OCR 常常认不出来（11/8 一屏的“1”“2”全漏）。
    头像两列不算（对方头像会跟着新消息出现，但我们自己的头像也在那一列）。
    """
    lay = layout(shot.image) if lay is None else lay
    if not title_matches(items, task['to']):
        return OTHER
    # D15：先拼气泡再整条比，回复折行时也认得出（原来一行一个字块比，折行就一直 UNKNOWN）
    ours = our_bubbles(items, task['msg'], lay)
    if not ours:
        return UNKNOWN
    bottom = max(it['box'][3] for it in ours)
    below = shot.image.convert('L').crop(
        (LIST_RIGHT + 60, min(bottom + 14, lay['bottom']), LIST_RIGHT + int((lay['right'] - LIST_RIGHT) * 0.4), lay['bottom']))
    return NEW if sum(below.histogram()[:160]) > 20 else NONE


# ---------- 文件传输助手模拟（D15，只在 --simulate 时开）----------
# 手机发到文件传输助手的消息在电脑上显示在右边，算"我方"，程序不会回。模拟模式下：
#   启动前屏上已有的消息不参与本轮；只对启动后能按顺序对齐的新增气泡判方向。
# 为什么要记"自己发了什么"：不记的话，程序发出去的回复也会被当成对方的新消息，自己回自己停不下来。
# 只对名字就是"文件传输助手"的白名单会话生效，真好友的会话不受影响。
SIM_NAME = '文件传输助手'
SIM = {'on': False, 'seen': [], 'labels': [], 'pending': [], 'epoch': None, 'bad': False}


def _sim_key(msg: dict) -> tuple:
    """输入屏幕消息，输出用于相邻两次读屏对齐的可见内容。"""
    return msg['who'], msg['kind'], msg['text']


def sim_start(hwnd: int) -> str:
    """开模拟：把屏上现有消息设为边界，本轮只处理之后的新增消息。"""
    if SIM_NAME not in WHITELIST:
        return '白名单里没有文件传输助手，模拟没开'
    r = messages.read_chat(hwnd, SIM_NAME)
    if not r['ok']:
        SIM['on'] = False
        return f'模拟没开：先在电脑上点开文件传输助手（{r["reason"]}）'
    SIM.update(on=True, seen=[_sim_key(m) for m in r['msgs']],
               labels=[{'who': 'none'} for _ in r['msgs']], pending=[],
               epoch=uuid.uuid4().hex, bad=False)
    return f'模拟已开：屏上 {len(r["msgs"])} 条旧消息只作边界'


def sim_sent(task: dict) -> None:
    """输入已确认发送的任务，等下一次读屏核对新增的我方气泡。"""
    if SIM['on'] and norm(task['to']) == norm(SIM_NAME) and task.get('msg'):
        SIM['pending'].append((task['msg'], task.get('tier', '')))


def sim_view(title: str, r: dict) -> dict:
    """输入当前屏幕，按前后重叠判新增气泡；对不齐就停止模拟回复。"""
    if not (SIM['on'] and r['ok'] and norm(title) == norm(SIM_NAME)):
        return r
    if SIM['bad']:
        return {**r, 'ok': False, 'reason': '模拟消息来源不明'}
    current = [_sim_key(msg) for msg in r['msgs']]
    previous = SIM['seen']
    if current[:len(previous)] == previous:
        overlap = len(previous)
        labels = list(SIM['labels'])
    else:
        overlap = next((n for n in range(min(len(previous), len(current)), 0, -1)
                        if previous[-n:] == current[:n]), 0)
        # 滚屏后相同内容可能对应多个位置，无法证明哪个气泡是新增的。
        if not overlap or any(previous[i:i + overlap] == current[:overlap]
                              for i in range(len(previous) - overlap)):
            SIM['bad'] = True
            return {**r, 'ok': False, 'reason': '模拟消息无法唯一对齐'}
        labels = list(SIM['labels'][-overlap:])
    pending = list(SIM['pending'])
    new = r['msgs'][overlap:]
    if pending and (not new or new[0]['kind'] != 'text' or new[0]['text'] != pending[0][0]):
        # 尚未看到刚发送的气泡时可以继续等；看到别的新增气泡则不能猜顺序。
        if new:
            SIM['bad'] = True
            return {**r, 'ok': False, 'reason': '模拟发送与新消息顺序不明'}
    if pending and new and sum(m['kind'] == 'text' and m['text'] == pending[0][0] for m in new) > 1:
        SIM['bad'] = True
        return {**r, 'ok': False, 'reason': '模拟同文消息来源不明'}
    for msg in new:
        if msg['who'] == 'us' and pending:
            expected, tier = pending.pop(0)
            if msg['kind'] != 'text' or msg['text'] != expected:
                SIM['bad'] = True
                return {**r, 'ok': False, 'reason': '模拟发送内容未匹配'}
            labels.append({'who': 'us', 'origin': 'agent', 'reply_kind': tier})
        elif msg['who'] == 'us':
            labels.append({'who': 'them', 'origin': 'other'})
        else:
            labels.append({'who': msg['who']})
    SIM.update(seen=current, labels=labels, pending=pending)
    return {**r, 'msgs': [{**msg, **label} for msg, label in zip(r['msgs'], labels)]}


def read_view(hwnd: int, title: str) -> dict:
    """read_chat，再按模拟模式改一下谁说的（模拟没开时原样交回）。"""
    return sim_view(title, messages.read_chat(hwnd, title, preview=title in WHITELIST))


def after_reply(hwnd: int, shot: Shot, task: dict) -> str:
    """开着的会话里，我们的回复下面有没有新消息：控件树读得到就按控件树判，读不到再用 new_after_reply（OCR + 像素）。

    输出同 new_after_reply：NEW / NONE / UNKNOWN / OTHER
    控件树更准的地方：对方发单个字、表情包、图片都是一条，不用靠像素猜；我们的回复折行也是一整条。
    """
    r = read_view(hwnd, task['to'])
    if not r['ok'] and r['reason'].startswith('标题'):
        return OTHER
    if r['ok'] and r['source'] == 'uia':
        mine = [i for i, m in enumerate(r['msgs']) if m['who'] == 'us' and m['kind'] == 'text'
                and norm(m['text']) == norm(task['msg'])]
        if not mine:
            return UNKNOWN
        return NEW if any(m['who'] == 'them' for m in r['msgs'][mine[-1] + 1:]) else NONE
    return new_after_reply(shot, chat_items(shot), task)


# ---------- 已写好 ----------

def bell(hwnd: int) -> tuple | None:
    """拍一张，交回门铃要比的样子（见 unread.bell_state）；拿不到画面时交回 None。"""
    shot = capture_window(hwnd)
    return bell_state(shot) if shot is not None else None


def wait_for_change(hwnd: int, last: tuple | None, timeout: float,
                    chat: bytes | None = None) -> tuple[tuple | None, str | None]:
    """盯门铃，直到样子和 last 不一样，或者过了 timeout 秒。

    输入：last 上一次看到的样子（bell 的结果）
         chat 刚回完时聊天区的样子（chat_sig）。传了就也盯聊天区：它一变就返回，让 serve 再查一次回复下面
    输出：(现在的样子, 为什么返回)：'bell' 门铃响了，'chat' 聊天区变了，None 超时没变（这时交回 last）。
    为什么只比图片、不认数字：白字红底和“99+”OCR 容易认错；变多变少都醒，醒了扫列表再说。
    响的时候打印是哪一样变了：11/8 漏响的原因没查清，下次再漏就靠这行查。
    """
    end = time.time() + timeout
    watching, beat = chat is not None, time.time()
    print(f'{ts()} 开始等门铃  前台={is_front(hwnd)}  角标行={len(last[1]) if last else "?"}  盯聊天区={watching}')
    while time.time() < end:
        shot = capture_window(hwnd)
        if shot is None:                         # 偶尔拿不到画面，跳过这一眼，不当成“变了”
            time.sleep(POLL)
            continue
        now = bell_state(shot)
        sig = chat_sig(shot)
        if now != last:
            why = []
            if last is None or now[0] != last[0]:
                why.append('总未读数')
            if last is None or now[1] != last[1]:
                why.append('会话角标')
            print(f'{ts()} 叮：{"、".join(why)}变了  前台={is_front(hwnd)}  聊天区变={chat is not None and sig != chat}')
            return now, 'bell'
        if chat is not None and sig != chat:     # 要抓的就是这一行
            print(f'{ts()} !! 聊天区变了，门铃没变  前台={is_front(hwnd)}  角标行={len(now[1])}  总数字={"有" if now[0] else "无"}')
            if watching:
                return last, 'chat'
        chat = sig
        if time.time() - beat >= HEARTBEAT:
            beat = time.time()
            print(f'{ts()} …等待中  前台={is_front(hwnd)}  角标行={len(now[1])}  总数字={"有" if now[0] else "无"}')
        time.sleep(POLL)
    return last, None


def scan(hwnd: int) -> list[dict]:
    """扫一遍会话列表，交回带角标的行 [{'title', 'box'}]，从上到下。拿不到画面时交回空列表。"""
    shot = capture_window(hwnd)
    return find_unread_rows(shot) if shot is not None else []


def make_task(title: str, n: int, run: str) -> dict:
    """把一个会话拼成主循环的任务。第一版回复写死，不读消息内容。

    输入：run 这次启动的时分（如 "2219"），n 这次启动后的第几条
    为什么带 run：11/8 实测编号每次从 1 起，聊天区里留着上几轮的同一句话，
      模型看见就说“完成”，回车前的条数核对也被旧气泡带偏。带上时分，每句都不重复。
    不带空格和横杠：11/7 实测“测试回复 1”的气泡 OCR 只读出“测试回复”，空格后单独的数字被漏掉。
    """
    return {'to': title, 'msg': f'测试{run}回复{n}'}


OK, RETRY, UNSURE, HUMAN, SKIP = 'ok', 'retry', 'unsure', 'human', 'skip'
# 语气例句是真实聊天，放在 git 忽略的本地文件里；没有就用 reply 里的默认语气
TONE_FILE = Path(__file__).resolve().parent.parent / 'tone.local.json'
TONE = json.loads(TONE_FILE.read_text(encoding='utf-8')) if TONE_FILE.exists() else []
_CFG = load_config()


def history_folder(title: str, simulate: bool) -> Path:
    """返回本机联系人数据目录，文件传输助手模拟数据单独保存。"""
    root = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'edge-agent'
    return root / ('wechat-history-sim' if simulate and title == SIM_NAME else 'wechat-history')


def remember(title: str, view: dict, sent_task: dict | None = None) -> list[dict]:
    """输入目标和已核对的读取结果，保存原文；已确认发送的气泡标记来源。"""
    # OCR 识别文字仍可处理当前一轮，但可能错字，不能当作长期原文。
    if not view['ok'] or view.get('source') != 'uia' or title not in WHITELIST:
        return view['msgs']
    if sent_task and sent_task.get('msg'):
        matches = [i for i, msg in enumerate(view['msgs'])
                   if msg['who'] == 'us' and msg['kind'] == 'text'
                   and msg['text'] == sent_task['msg']]
        if len(matches) == 1:
            messages_with_origin = list(view['msgs'])
            index = matches[0]
            messages_with_origin[index] = {**messages_with_origin[index],
                                           'origin': 'agent',
                                           'reply_kind': sent_task.get('tier', '')}
            view = {**view, 'msgs': messages_with_origin}
    try:
        folder = history_folder(title, SIM['on'])
        if SIM['on'] and title == SIM_NAME:
            folder = folder / SIM['epoch']
        context = history.sync(folder, title, view['msgs'],
                               preview=view.get('preview'))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # 历史文件失效时仍可用现场消息，日志不带联系人、路径或原文。
        print(f'  对话历史读写失败（{type(exc).__name__}），本轮只用当前会话')
        return view['msgs']
    # 历史里的对方消息只有在存在我方回复作为分界时，才适合交给现有的新旧消息切分。
    if context and not any(msg['who'] == 'us' for msg in context):
        return view['msgs']
    return context


def ask_qwen(msgs: list[dict]) -> str:
    """输入 OpenAI 格式的 messages，输出 Qwen 的回复原文。超时设短：等太久不如这轮不回、下轮再试。"""
    # 自由回复会有长回答（默写、数学题），80 个 token 会被截断；300 够写一段
    return chat(_CFG['llm_url'], _CFG['llm_model'], msgs, max_tokens=300, timeout=30)


def rank_history(msgs: list[dict]) -> str:
    """使用同一内网模型执行独立检索调用，限制时长，失败时由关键词检索接手。"""
    return chat(_CFG['llm_url'], _CFG['llm_model'], msgs, max_tokens=64, timeout=5)


def decide(hwnd: int, title: str, since: float | None = None) -> dict:
    """会话已经点开：等对方说完 → 读聊天记录 → reply.write_reply 决定发不发、发什么。

    输入：since 门铃/扫描第一次看到这条新消息的时刻，等静从那时算
    输出：write_reply 的决定 {'send', 'text', 'reason', 'pause'}，另加 'read_ok'（读消息成没成）、'source'
    """
    r = messages.wait_quiet(lambda: read_view(hwnd, title), since=since)
    # 读几次、失败几次、最后一次失败原因打出来：11/12 一次读消息用了 9.9 秒，要能看出慢在哪
    print(f"  等静：读 {r.get('reads')} 次，失败 {r.get('fails')} 次，静下来了={r.get('quiet')}，最后一次：{r['reason']}")
    if not r['ok']:
        return {'send': False, 'text': '', 'reason': '读消息失败：' + r['reason'], 'pause': False,
                'read_ok': False, 'source': r['source']}
    context = remember(title, r)
    try:
        now = datetime.now()
    except (OSError, OverflowError) as exc:
        return {'send': False, 'text': '', 'reason': f'本机时钟不可读：{type(exc).__name__}',
                'pause': True, 'tier': 'error', 'notify': False,
                'read_ok': False, 'source': r['source']}
    covered, new = reply.split_new(context, now)
    if title in WHITELIST:
        marker = history.handoff_path(history_folder(title, SIM['on']), title)
        status = {'send': False, 'text': '', 'reason': '会话等待人工处理', 'pause': True,
                  'read_ok': True, 'source': r['source'], 'seen': msg_key(r['msgs'])}
        try:
            if marker.exists():
                return status
            observed = context
            context = history.without_handled(marker, observed, reply.loan_followup)
            covered, new = reply.split_new(context, now)
            reason = reply.handoff_reason(new)
            if reason:
                try:
                    start = next(i for i, message in enumerate(observed) if message is new[0])
                    request = next((i for i in range(start, len(observed))
                                    if observed[i]['who'] == 'them'
                                    and reply.handoff_reason([observed[i]]) == reason), start)
                    batch = {'start': start, 'request': request, 'reason': reason,
                             'keys': history.message_keys(observed)}
                    if r['source'] == 'ocr':
                        batch.update(source='ocr', request_text=observed[request]['text'])
                    history.start_handoff(marker, batch, preview=r.get('preview'))
                except FileExistsError:
                    return status
                number = WHITELIST.index(title) + 1
                shown = notify(f'端侧助手：{reason}', f'白名单#{number} 已暂停自动回复，请处理后手动恢复。')
                print(f'  白名单#{number} 已转人工，通知启动={shown}')
                return {**status, 'send': True, 'text': reply.LOAN_HANDOFF}
        except (OSError, ValueError) as exc:
            print(f'  人工接管状态读写失败（{type(exc).__name__}）')
            return {**status, 'reason': '人工接管状态读写失败', 'read_ok': False}
    # reply_mode：'free' 临时自由回复（11/13 用户要求，先不分流）；'tiers' 三档分流（方案 A）
    if load_config().get('reply_mode', 'tiers') == 'free':
        try:
            intent = reply.classify_new(new, ask_qwen, covered) if new else None
        except Exception as exc:
            return {'send': False, 'text': '', 'reason': f'模型调用失败：{type(exc).__name__}',
                    'pause': False, 'tier': 'error', 'notify': False,
                    'read_ok': False, 'source': r['source']}
        recalled = []
        if intent == 'history':
            new_ids = {id(msg) for msg in new}
            safe = [msg for msg in context if id(msg) in new_ids or msg['who'] == 'them' or
                    (msg['who'] == 'us' and msg.get('origin') == 'human_confirmed')]
            recalled = recall.find(safe, new, rank_history, reply.CONTEXT_N)
        d = reply.write_reply_free(context, now, TONE, ask_qwen,
                                   recalled=recalled, intent=intent)
    else:
        recalled = recall.find(context, new, rank_history, reply.CONTEXT_N, covered=covered)
        d = reply.write_reply(context, now, False, TONE, ask_qwen, kb.search, recalled=recalled)
    if d.get('tier') == 'handoff' and title in WHITELIST:
        try:
            start = next(i for i, message in enumerate(observed) if message is new[0])
            batch = {'start': start, 'request': start, 'reason': d['reason'],
                     'keys': history.message_keys(observed)}
            if r['source'] == 'ocr':
                batch.update(source='ocr', request_text=observed[start]['text'])
            history.start_handoff(marker, batch, preview=r.get('preview'))
        except FileExistsError:
            return status
        except (OSError, ValueError, StopIteration) as exc:
            print(f'  人工接管状态创建失败（{type(exc).__name__}）')
            return {**status, 'reason': '人工接管状态创建失败', 'read_ok': False}
        number = WHITELIST.index(title) + 1
        shown = notify(f'端侧助手：{d["reason"]}',
                       f'白名单#{number} 已暂停自动回复，请处理后手动恢复。')
        print(f'  白名单#{number} 已转人工，通知启动={shown}')
        return {**status, 'send': True, 'text': reply.LOAN_HANDOFF,
                'reason': d['reason'], 'tier': 'handoff'}
    return {**d, 'read_ok': True, 'source': r['source'], 'seen': msg_key(r['msgs'])}


def msg_key(msgs: list[dict]) -> tuple:
    """输入消息列表，输出能比较的样子（谁、类型、文字），用来判断聊天记录变没变。"""
    return tuple((m['who'], m['kind'], m['text']) for m in msgs)
# 这几种停下是"情况不对"，不是"运气不好"：输入框里有用户的草稿、焦点被抢走、会话被切走。
# 重试照样会被拒绝，白占队列（D11 实测连试 2 次都被拒），所以交给人
HUMAN_STOPS = ('被底层拒绝', '发送许可作废')


def prepare(hwnd: int) -> bool:
    """动微信之前确认窗口：拉到前面、大小 960×800、能响应。不行就交回 False，这次不动。

    为什么一定要查“拉到前面”成没成：11/9 没查，微信被别的窗口挡着，点击和打字都落到了挡在前面的窗口上。
    """
    ok, why = activate(hwnd)
    if not ok or not is_front(hwnd):
        print(f'  微信拉不到前台，这次不动：{why}')
        return False
    l, t, r, b = window_rect(hwnd)
    if (r - l, b - t) != graph.WIN_SIZE:   # 大小本来就对就不设：set_window_size 每次固定等 0.5 秒
        ok, why = set_window_size(hwnd, *graph.WIN_SIZE)
        if not ok:
            print(f'  窗口大小不对，这次不动：{why}')
            return False
    ok, why = check_responsive(hwnd)
    if not ok:
        print(f'  微信不响应，这次不动：{why}')
    return ok


def open_chat(hwnd: int, title: str) -> Shot | None:
    """程序自己点开会话，再核对右边标题。

    输入：微信窗口句柄、要点开的会话标题（白名单里的写法）
    输出：点开了而且标题对上 → 点之前截的那张图（拿去查被切走的会话，见 left_behind）
          没找到这一行、或点完标题不对 → None
    为什么不让模型点：11/9 两人同时发时，模型从看图到点下去要好几秒，列表被新消息重新排序，
      点到了别的会话（被规矩拦下，连败两次就放弃）。程序从读到点只隔一次标题 OCR，约 0.6 秒。
    """
    shot = capture_window(hwnd)
    if shot is None:
        return None
    if not title_matches(ocr_crop(shot, title_box(shot)), title):   # 已经开着就不碰列表：单人连着回时省 2 秒多
        point = find_row(shot.image, title)
        if point is None:
            print('  列表里没找到这个会话')
            return None
        click(*shot.to_screen(*point))
        time.sleep(0.5)                  # 等右边换成这个会话
    # 再点一下输入框：点列表行后焦点停在列表上，模型的 type 是直接粘贴、不先点输入框，字会落空（11/9 实测）
    current = capture_window(hwnd)
    if current is None or not title_matches(ocr_crop(current, title_box(current)), title):
        print('  点开后标题对不上')
        return None
    lay = layout(current.image)
    # bottom 在分隔线上方 10 像素，故输入点要加 60 才是线下 50。
    click(*current.to_screen((LIST_RIGHT + lay['right']) // 2, lay['bottom'] + 60))
    return shot


def left_behind(before: Shot | None, watch: dict | None, title: str) -> str | None:
    """切走时，被切走的那个会话（watch）有没有新消息。不碰窗口。

    输入：before 点之前那张图（open_chat 交回的）；watch 切走前开着、盯着的任务；title 要去的会话
    输出：没切走（没点成、没在盯、或者去的就是它）→ None；否则 new_after_reply 的结果
    为什么要查：开着的会话来消息常常不出角标，一点别的会话，出了的角标也会消失（11/9 TIBO 发现）。
      点之前那一刻它聊天区里有的，这里都能看到；只有“截图到点下去”那约 0.6 秒里来的会漏。
    """
    if before is None or watch is None or watch['to'] == title:
        return None
    return new_after_reply(before, chat_items(before), watch)


# 发送方式：'flow' 固定流程（D15 方案二，默认）；'graph' 旧的看图主循环（模型每步看截图决定，留作对照和老测试）
SEND_MODE = 'flow'
FORCE_VISION = False     # True：固定流程也不用控件树，只用看图适配器（测兜底那条路用）


def run_one(hwnd: int, task: dict, since: float | None = None) -> tuple[str, Shot | None]:
    """点开会话 → （没定回复时）读消息、写回复 → 发出去 → 程序核对。

    输出：(结果, 点开之前那张图)。图拿去查被切走的会话；没点成时是 None。结果（看程序核对，不看模型说的“完成”）：
      OK      核对通过
      RETRY   肯定没发出去，可以重试
      UNSURE  按过回车但核对没通过：可能已经发出去了，重试会发两条，所以不重试，交给人看
      HUMAN   没发，但情况不对（输入框有别人的字等），重试也一样，交给人看
      SKIP    读完消息决定不回（没有新消息等），什么都没发
    D15：task['msg'] 是 None 时，点开会话后先读消息、让 reply 写回复；决定写进 task['msg']、task['pause']。
      读消息失败、模型连不上算 RETRY：什么都没发。
    每次都重新确认窗口：两次回复之间可能有人动过微信，大小变了核对会不准，不响应就更不能点。
    """
    t0 = time.time()
    if SEND_MODE == 'graph':
        before = open_chat(hwnd, task['to']) if prepare(hwnd) else None
        adapter = None
    else:
        from agent.core.send import pick_adapter
        before = capture_window(hwnd) if prepare(hwnd) else None
        adapter = pick_adapter(hwnd, FORCE_VISION) if before is not None else None
        if adapter is not None and not adapter.open(task['to']):
            print(f'  [{adapter.kind}] 没打开目标会话')
            before = None
    t1 = time.time()
    if before is None:
        if task.get('msg'):
            journal.record(task, 'fail')
        return RETRY, None
    if task.get('msg') is None:
        d = decide(hwnd, task['to'], since)
        print(f"  读消息（{d['source'] or '无'}）+ 决定 {time.time() - t1:.1f} 秒："
              f"{'发「' + d['text'] + '」' if d['send'] else '不发'}，{d['reason']}")
        # 读消息失败、模型连不上：什么都没发，下一轮再试。不能算 SKIP：SKIP 会记下这屏消息，
        # 聊天记录不变就不再回，模型恢复了也不会回（11/12 真机 Qwen 掉线时碰到）
        if not d['read_ok'] or d['reason'].startswith('模型调用失败'):
            return RETRY, before
        if not d['send']:
            task['seen'] = d.get('seen')
            return SKIP, before
        task['msg'], task['pause'], task['reason'] = d['text'], d['pause'], d['reason']
        task['reply_snapshot'] = d['seen']
        task['notify'], task['tier'] = d.get('notify', False), d.get('tier', '')
        t1 = time.time()
    if not reply_snapshot_matches(hwnd, task):
        # 常驻队列保留 task；清掉失效候选，让下一次从新消息开始。
        discard_reply(task)
        task['why'] = '生成回复后消息变化或无法核对'
        return RETRY, before
    if adapter is None:
        return _run_graph(hwnd, task, before, t0, t1)
    from agent.core.send import deliver
    try:
        result, why = deliver(adapter, task)
    except Exception as e:
        # 说不清回车按没按过：按"可能发了"处理，隔离、不重试，交给人看
        print(f'  发送流程出错（{type(e).__name__}），按可能已发处理，交给人看')
        journal.record(task, 'unknown')
        return UNSURE, before
    task['why'] = why
    print(f'  发送：{result}，{why}；耗时 准备+开会话+读消息写回复 {t1 - t0:.1f} 秒，发送 {time.time() - t1:.1f} 秒')
    return result, before


def discard_reply(task: dict) -> None:
    """使队列中的任务重新读消息并生成回复。"""
    task.pop('reply_snapshot', None)
    task.update(msg=None, pause=False, notify=False, tier='')


def reply_snapshot_matches(hwnd: int, task: dict) -> bool | None:
    """比较实时聊天与生成依据；预先指定正文的任务没有生成快照。"""
    if 'reply_snapshot' not in task:
        return True
    view = read_view(hwnd, task['to'])
    return msg_key(view['msgs']) == task['reply_snapshot'] if view['ok'] else None


def _run_graph(hwnd: int, task: dict, before, t0: float, t1: float) -> tuple[str, Shot | None]:
    """旧的看图主循环（SEND_MODE='graph'）：模型每步看截图决定下一步。"""
    try:
        end = graph.build_graph().invoke(graph.new_state(task, hwnd), {'recursion_limit': 80})
    except Exception as e:
        # 11/12 真机：定位服务断开，异常一路冒上来把常驻整个带崩。
        # 说不清回车按没按过，按"可能发了"处理：隔离、不重试，交给人看
        print(f'  主循环出错（{type(e).__name__}），按可能已发处理，交给人看')
        journal.record(task, 'unknown')
        return UNSURE, before
    print(f'  耗时：准备+开会话+读消息写回复 {t1 - t0:.1f} 秒，主循环 {time.time() - t1:.1f} 秒')
    task['why'] = end['steps'][-1]['result'] if end['steps'] else ''   # 停在哪一步，通知里告诉人
    # 坑（11/12 真机）：模型说"完成"时核对会给出 unknown，可回车根本没按过，也照记 unknown，
    # 下次启动账本把这个会话当"可能已发"一直隔离，再也不回。没按过回车就一定没发，记 fail
    pressed = any(s['result'] == '已按回车' for s in end['steps'])
    outcome = 'ok' if end['done'] else (end.get('outcome') or 'unknown') if pressed else 'fail'
    journal.record(task, outcome)
    if end['done']:
        return OK, before
    if end.get('outcome') == 'fail':
        return RETRY, before
    # 「做」按完回车会记一条 result='已按回车'（graph.act），看它就知道消息有没有可能已经发出
    if any(s['result'] == '已按回车' for s in end['steps']):
        return UNSURE, before
    if end['steps'] and end['steps'][-1]['result'].startswith(HUMAN_STOPS):
        return HUMAN, before
    return RETRY, before


# ---------- 你来写 ----------

def find(title,whitelist):
            for w in whitelist:
                if norm(title) == norm(w):
                    return w
            return  None

def pick_sessions(rows: list[dict], whitelist: list[str], pending: dict[str, int],
                  max_retry: int = MAX_RETRY, isolated=None) -> tuple[list[str], int]:
    """挑出这一轮要回的会话。不碰窗口，只看传进来的数据，所以能离线测。

    输入：
      rows       这次扫到的未读行 [{'title': 标题, 'box': 框}]，从上到下
      whitelist  允许回的会话标题
      pending    账本 {标题: 已经失败了几次}：扫到过、还没回成功的会话。刚扫到还没回的是 0
      max_retry  失败到这个次数就放弃
    输出：(要回的标题列表, 跳过了几个非白名单会话)
    规则：
      1. 标题 norm() 后和白名单里某一个 norm() 后相等，才算白名单内；不在的跳过并计数
      2. pending 里次数 >= max_retry 的跳过（放弃了），不计入非白名单的数
      3. 同一个标题只出现一次
      4. 顺序：先按 rows 从上到下；再把 pending 里还能回、但这次 rows 里没有的标题加在最后
         （为什么要加：角标一点就没，被 agent 自己点掉的、回失败的，都只剩账本里这一份记录）
    交回的标题用白名单里的写法，不用 OCR 读出来的写法：主循环的规矩拿它和白名单逐字比。
    """
    isolated = set() if isolated is None else isolated
    whitelist2 = []
    unwhitelist= 0
    for row in rows:
        name = find(row['title'],whitelist)
        if name is None:
            unwhitelist += 1
            continue
        if whitelist.index(name) + 1 in isolated or pending.get(name,0) >= max_retry:
            continue
        if name in whitelist2:
            continue
        whitelist2.append(name)
    for name,count in pending.items():
        if name in whitelist and whitelist.index(name) + 1 not in isolated and name not in whitelist2 and count < max_retry:
            whitelist2.append(name)
    return (whitelist2,unwhitelist)



def drop_unchanged(names: list[str], skip_seen: dict, read) -> list[str]:
    """输入待回的会话、{标题: 上次判不回时的聊天记录}、读聊天记录的函数，输出去掉"判过不回且没变"的会话。

    为什么：11/18 真机判不回后，扫描每 4 秒又挑中同一个会话（会话开着时角标没被点掉），
    每次都等静、调模型。skip_seen 原先只在"盯开着的聊天区"那条路上查，扫描这条路漏了。
    读失败（比如这个会话没开着）就照常去回，宁可多读一次也不漏回。
    """
    keep = []
    for name in names:
        if name in skip_seen:
            r = read(name)
            if r['ok'] and msg_key(r['msgs']) == skip_seen[name]:
                continue
        keep.append(name)
    return keep


def serve(hwnd: int, seconds: float, isolated=None, journal_path=None) -> dict:
    """常驻 seconds 秒：有白名单好友发消息就回，回完重扫，没有了就回去盯。

    输出：{'replied': 回成功几个, 'unsure': 可能发了但没核对上几个, 'gave_up': 放弃了几次, 'wakes': 门铃醒了几次}
         放弃的只交回个数，不打印标题
    提示：
      1. 准备：n = 1（回复编号）、pending = {}、记下开始时间
      2. 启动时先扫一遍：开跑前就已经有的未读也要回，不能等门铃响
      3. 外层循环，直到过了 seconds 秒（每一圈开头先 last = bell(hwnd)，再扫：顺序不能反，见代码里的注释）：
           a. 扫 → pick_sessions → 先把挑出来的每一个都记进 pending（已经在的不动，新的记 0）
              → 只回排第一个的那个（run_one）：
                成功：n 加 1，从 pending 里删掉它
                失败：pending 里它的次数加 1
              回完一个就回到 a 重扫（回的时候可能又来了新消息，扫一遍只要 0.6 秒）；挑不出要回的了才往下走
           b. 没人要回了：wait_for_change(hwnd, last, 剩下的秒数)，变了就 wakes 加 1，回到 a
      4. 想一想：a 里“回到 a 再扫”如果写成无条件循环，一个一直失败的会话会怎样？max_retry 在哪里起作用？
         再想：为什么“挑出来的都记进账本”要在 run_one 之前做，而不是回失败了才记？
      5. Ctrl+C 要能停：用 try/except KeyboardInterrupt 包住外层循环，停下时照样交回统计
    """
    # 11/7 TIBO 说“这次直接写”，由 Claude 写完整
    isolated = set() if isolated is None else set(isolated)
    retry_tasks = {}
    n, replied, unsure, wakes = 1, 0, 0, 0
    run = time.strftime('%H%M')          # 这次启动的时分，拼进回复里，见 make_task
    pending = {}                         # 账本 {标题: 失败次数}：扫到就记，回成功才划掉
    gave_up_at = {}                      # {标题: 放弃的时刻}：过了 COOLDOWN 秒就从账本里划掉，再有角标会重新挑它
    gave_up = 0
    watch = None                         # 最近回过的任务：它的会话一直开着，要盯聊天区
    skip_seen = {}                       # {标题: 判"不回"时读到的聊天记录}：没变就不再去点它（11/12 真机每 7 秒点一次）
    revisit = {}                         # {标题: 回过的任务}：切走时没查清的会话，都回完了再挨个点开查（TIBO 11/9 的主意）
    start = time.time()

    def note_left(before, title):        # 切走了 watch：查它，有新消息记账本，查不清记进 revisit
        seen = left_behind(before, watch, title)
        if seen:
            print(f'{ts()} 切走前查 白名单#{WHITELIST.index(watch["to"]) + 1}：{seen}')
        if seen == NEW:
            pending[watch['to']] = 0
        elif seen == UNKNOWN:
            revisit[watch['to']] = watch

    def left() -> float:                 # 还剩几秒
        return seconds - (time.time() - start)

    try:
        while left() > 0:
            # TIBO 的写法：先记样子，再扫。顺序反过来（11/7 的版本）时，扫完、记样子之前的那一瞬
            # 来的消息会被算进 last，门铃拿“已经包含新消息”的样子去比，就一直不响（11/8 验收漏了 4 条）
            last = bell(hwnd)
            for t in [t for t, at in gave_up_at.items() if time.time() - at > COOLDOWN]:
                del pending[t], gave_up_at[t]
            # 第 2–7 步：看一眼 → 挑人 → 有人就回第一个，回完马上再看一眼
            names, skipped = pick_sessions(scan(hwnd), WHITELIST, pending, isolated=isolated)
            if skip_seen:
                kept = drop_unchanged(names, skip_seen, lambda t: read_view(hwnd, t))
                for t in set(names) - set(kept):
                    pending.pop(t, None)     # 判过不回、没变：不进账本，下面去等门铃，不空转
                names = kept
            found_at = time.time()       # 角标已经在了，说明消息在这之前就到了：等静从这里算
            if skipped:
                print(f'  跳过 {skipped} 个非白名单会话')
            if names:
                for name in names:       # 先记账再动手：一动手就可能点掉别人的角标
                    if name not in pending:
                        pending[name] = 0
                title = names[0]
                task = retry_tasks.get(title) or {'to': title}
                # 走到这里的旧任务都没发出去（发了或可能发了的已经划掉），回复要按最新的聊天重新写
                task['msg'], task['pause'], task['notify'] = None, False, False
                who = WHITELIST.index(title) + 1  # 日志不写名字，写白名单第几个
                if journal_path is not None:
                    task.update(journal=str(journal_path), who=who)
                    task.setdefault('id', uuid.uuid4().hex)
                retry_tasks[title] = task
                print(f'{time.strftime("%H:%M:%S")} 去回：白名单#{who}（还有 {len(names) - 1} 个排队）')
                result, before = run_one(hwnd, task, since=found_at)
                print(f'{ts()} 回复结束：{result}  前台={is_front(hwnd)}')
                note_left(before, title)
                if before is not None:   # 已经切到这个会话，原来盯的那个交给账本或 revisit 了
                    watch = None
                if result == OK:
                    sim_sent(task)
                if task.get('notify') and result in (OK, UNSURE):
                    # 方案 A（11/13）：高风险话题回固定话术、通知本人，但不停住会话，对方再发照样回
                    print(f'  白名单#{who} 高风险话题，已回固定话术并通知你')
                    notify('端侧助手：高风险话题，需要你本人处理',
                           f'白名单#{who}：{task.get("reason") or "高风险话题"}。已回对方一句稍后回复，没有答应任何事')
                if result == SKIP:
                    del pending[title]
                    retry_tasks.pop(title, None)
                    skip_seen[title] = task.get('seen')
                elif result == OK:
                    # 发后重新读实际消息顺序，避免把生成的草稿或尚未确认的回复写成历史。
                    remember(title, read_view(hwnd, title), sent_task=task)
                    replied += 1
                    n += 1
                    del pending[title]
                    retry_tasks.pop(title, None)
                    watch = task         # 回了谁，开着的就是谁
                elif result == UNSURE:   # 可能已经发了：不重试，免得发两条；编号也往后走，不重复用
                    unsure += 1
                    n += 1
                    del pending[title]
                    isolated.add(who)
                    revisit.pop(title, None)
                    retry_tasks.pop(title, None)
                    print(f'白名单#{who} 已隔离，等人工确认')
                    if not task.get('notify'):       # 高风险那条上面已经通知过
                        notify('端侧助手：请确认是否发出', f'白名单#{who}：按过回车但没核对上，可能已经发出。'
                               f'已停止自动回这个会话，请看一眼聊天记录')
                elif result == HUMAN:    # 没发；情况不对，重试也一样。冷却期内不再挑它，免得一直被拒
                    pending[title] = MAX_RETRY
                    gave_up_at[title] = time.time()
                    gave_up += 1
                    print(f'  被拒绝或发送许可作废，没发出去：不重试，请人工看一眼输入框（{COOLDOWN} 秒内不再回这个会话）')
                    notify('端侧助手：没发出去，需要你看一眼', f'白名单#{who}：{task.get("why", "")[:120]}。'
                           f'{COOLDOWN} 秒内不再自动回这个会话')
                else:
                    pending[title] += 1  # 到 MAX_RETRY 后 pick_sessions 不再挑它，这里就不会一直转
                    later = '之后重试'
                    if pending[title] >= MAX_RETRY:
                        later = f'放弃 {COOLDOWN} 秒'
                        gave_up_at[title] = time.time()
                        gave_up += 1
                    print(f'  没发出去，第 {pending[title]} 次，{later}')
                continue                 # 回到第 2 步

            # 都回完了：切走时没查清的会话，挨个点开查一遍
            if revisit:
                title, task = revisit.popitem()
                before = open_chat(hwnd, title) if prepare(hwnd) else None
                if before is not None:
                    note_left(before, title)
                    shot = capture_window(hwnd)
                    seen = after_reply(hwnd, shot, task)
                    print(f'{ts()} 回头查 白名单#{WHITELIST.index(title) + 1}：{seen}')
                    watch = task
                    if seen == NEW:
                        pending[title] = 0
                        watch = None
                continue

            # 开着的会话：不靠角标，看我们的回复下面有没有东西
            chat = None
            open_wl = None
            if watch is None:
                # 11/12 真机：好友会话本来就开着，来消息不出角标，前两分钟发的都没发现。
                # 开着的是白名单会话（没隔离）就也盯聊天区，一变就去读一遍；没有新消息 reply 会判不回
                cur = capture_window(hwnd)
                t = open_title(cur) if cur is not None else ''
                hit = next((w for w in WHITELIST if t and norm(t) == norm(w)), None)
                if hit and WHITELIST.index(hit) + 1 not in isolated:
                    open_wl, chat = hit, chat_sig(cur)
            shot = capture_window(hwnd) if watch else None
            if shot:
                seen = after_reply(hwnd, shot, watch)
                print(f'{ts()} 查回复下面：{seen}')
                if seen == NEW:              # 放回账本，回到扫描那一步再回一次
                    pending[watch['to']] = 0
                    watch = None             # 回成功会重新记上；不清掉的话，回失败时会一直查到 NEW、一直重置次数
                    continue
                if seen == OTHER:
                    watch = None
                else:
                    chat = chat_sig(shot)    # 基准和这次检查是同一张图：查完之后再来的，一定会让聊天区变

            # 第 8–9 步：没人要回了，拿扫描之前记的样子等门铃。扫描之后才来的消息一定和它不一样
            last, why = wait_for_change(hwnd, last, left(), chat)
            if why:
                wakes += 1
            if why == 'chat' and open_wl:
                # 先不点，直接读一遍（会话本来就开着）：和上次判"不回"时一样，就当没变
                r = read_view(hwnd, open_wl)
                if not (r['ok'] and skip_seen.get(open_wl) == msg_key(r['msgs'])):
                    pending.setdefault(open_wl, 0)
    except KeyboardInterrupt:            # Ctrl+C：不报错，照样交回统计
        print('\n按了 Ctrl+C，停下')

    return {'replied': replied, 'unsure': unsure, 'gave_up': gave_up, 'wakes': wakes}


# ---------- 离线测试和入口，已写好 ----------

def _row(title: str, y: int) -> dict:
    return {'title': title, 'box': (80, y, 250, y + 60)}

_W = ['测试好友A', '测试好友B']
CASES = [
    # (说明, rows, pending, 应该交回的列表, 应该跳过几个)
    ('没有未读', [], {}, [], 0),
    ('一个白名单', [_row('测试好友A', 100)], {}, ['测试好友A'], 0),
    ('两个都在，按从上到下', [_row('测试好友B', 100), _row('测试好友A', 170)], {}, ['测试好友B', '测试好友A'], 0),
    ('非白名单跳过并计数', [_row('工作群', 100), _row('测试好友A', 170), _row('某人', 240)], {}, ['测试好友A'], 2),
    ('OCR 多了空格也认', [_row('测试 好友A', 100)], {}, ['测试好友A'], 0),
    ('失败过一次，这次不在列表里，也要重试', [], {'测试好友A': 1}, ['测试好友A'], 0),
    ('失败到上限，放弃', [_row('测试好友A', 100)], {'测试好友A': 2}, [], 0),
    ('同一个不重复', [_row('测试好友A', 100)], {'测试好友A': 1}, ['测试好友A'], 0),
    ('扫到过还没回，角标被点掉了，也要回', [_row('测试好友B', 100)], {'测试好友A': 0}, ['测试好友B', '测试好友A'], 0),
]


def _it(text, x1, y1, x2, y2):
    return {'text': text, 'box': [x1, y1, x2, y2], 'score': 0.99}

_T = {'to': '测试好友A', 'msg': '测试回复3'}
_HEAD = [_it('测试好友A', 270, 45, 360, 67)]
REPLY_CASES = [
    # (说明, OCR 块, 深色块位置, 应该)；深色块模拟 OCR 认不出的单个字
    ('回复在最下面', _HEAD + [_it('测试回复3', 820, 610, 900, 635)], None, NONE),
    ('回复下面有单个字的墨迹', _HEAD + [_it('测试回复3', 820, 400, 900, 420)], (350, 460, 355, 466), NEW),
    ('标题对不上：开着的不是它', [_it('别人', 270, 45, 330, 67), _it('测试回复3', 820, 400, 900, 420)], (350, 460, 355, 466), OTHER),
    ('没认出我们的回复：判断不了', _HEAD, (350, 460, 355, 466), UNKNOWN),
    ('只看最下面那条我们的回复', _HEAD + [_it('测试回复3', 820, 300, 900, 320), _it('测试回复3', 820, 460, 900, 480)], (350, 360, 355, 366), NONE),
    ('输入框里的墨迹不算', _HEAD + [_it('测试回复3', 820, 460, 900, 480)], (350, 690, 355, 696), NONE),
    ('头像那一列的墨迹不算', _HEAD + [_it('测试回复3', 820, 400, 900, 420)], (270, 460, 275, 466), NONE),
    ('恰好 20 个深色像素不算', _HEAD + [_it('测试回复3', 820, 400, 900, 420)], (350, 460, 354, 465), NONE),
    ('21 个深色像素算新消息', _HEAD + [_it('测试回复3', 820, 400, 900, 420)], (350, 460, 353, 467), NEW),
]


def run_offline() -> None:
    ok_r = 0
    for i, (desc, items, ink, want) in enumerate(REPLY_CASES, 1):
        image = Image.new('RGB', (960, 800), 'white')
        if ink:
            image.paste((0, 0, 0), ink)
        got = new_after_reply(Shot(image, 0, 0), items, _T)
        ok_r += got == want
        print(f'R{i}. {"对" if got == want else "错"}  {desc}' + ('' if got == want else f'  应该 {want}，实际 {got}'))
    print(f'new_after_reply {ok_r}/{len(REPLY_CASES)} 对\n')
    passed = 0
    for i, (desc, rows, pending, want, want_skip) in enumerate(CASES, 1):
        got = pick_sessions(rows, _W, dict(pending))
        ok = got == (want, want_skip)
        passed += ok
        print(f'{i}. {"对" if ok else "错"}  {desc}' + ('' if ok else f'  应该 {(want, want_skip)}，实际 {got}'))
    print(f'\n{passed}/{len(CASES)} 对' + ('，pick_sessions 通过' if passed == len(CASES) else ''))

    # drop_unchanged：判过不回、聊天记录没变的会话不再去回（11/18 真机每 4 秒重进一次、每次调模型）
    same = {'ok': True, 'msgs': [{'who': 'them', 'kind': 'text', 'text': '在吗'}]}
    changed = {'ok': True, 'msgs': [*same['msgs'], {'who': 'them', 'kind': 'text', 'text': '你好'}]}
    seen = {'测试好友A': msg_key(same['msgs'])}
    reads = {'同样': lambda t: same, '变了': lambda t: changed, '读失败': lambda t: {'ok': False, 'msgs': []}}
    drop_cases = [('同样', ['测试好友B']), ('变了', ['测试好友A', '测试好友B']), ('读失败', ['测试好友A', '测试好友B'])]
    for desc, want in drop_cases:
        got = drop_unchanged(['测试好友A', '测试好友B'], seen, reads[desc])
        print(f'D. {"对" if got == want else "错"}  聊天记录{desc}' + ('' if got == want else f'  应该 {want}，实际 {got}'))
        assert got == want, desc
    assert ok_r == len(REPLY_CASES) and passed == len(CASES), '离线测试失败'


def startup_isolated(path, clear_journal=False) -> set[int]:
    """输入账本路径和清账选项，输出启动隔离序号；供命令行与离线测试共用。"""
    if clear_journal:
        journal.clear(path)
    return journal.unresolved(path)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    if sys.argv[1:] == ['--offline']:
        return run_offline()
    parser = argparse.ArgumentParser()
    parser.add_argument('seconds', nargs='?', type=float)
    parser.add_argument('--clear-journal', action='store_true')
    parser.add_argument('--simulate', action='store_true', help='文件传输助手模拟：手机发来的当成对方的')
    parser.add_argument('--vision', action='store_true', help='发送只用看图适配器（测兜底那条路）')
    parser.add_argument('--graph', action='store_true', help='用旧的看图主循环发送（对照用）')
    parser.add_argument('--resume-handoff', type=int, metavar='N', help='人工处理后恢复白名单第 N 个会话')
    parser.add_argument('--reply-flow', choices=('preview', 'send'), help='启用可恢复回复的预览或测试发送')
    parser.add_argument('--reply-contact', action='append', default=[], help='明确指定白名单测试联系人，可重复')
    parser.add_argument('--reply-db', type=Path, help='本机隔离回复账本路径')
    args = parser.parse_args()
    if args.reply_flow:
        if (args.seconds is None or args.seconds <= 0 or args.clear_journal or args.resume_handoff is not None
                or args.vision or args.graph or not args.reply_contact
                # 文件传输助手必须以模拟方式接入（不模拟时它的气泡全是我方），模拟也只对它生效；
                # 可以和真实好友一起跑：好友会话仍按头像和角标判断（11/18 用户只找到一位测试好友）
                or args.simulate != (SIM_NAME in args.reply_contact)
                or any(name not in WHITELIST for name in args.reply_contact)
                or any(sum(norm(other) == norm(name) for other in WHITELIST) != 1
                       for name in args.reply_contact)
                or len(set(args.reply_contact)) != len(args.reply_contact)):
            parser.error('新流程须指定运行时长和唯一、明确的白名单测试联系人，只使用真实微信控件树')
        from agent.core import wechat_reply_flow
        wins = graph._wechat_windows()
        if len(wins) != 1:
            parser.error('请只打开一个已登录的测试微信主窗口')
        if args.simulate:
            print(sim_start(wins[0].hwnd))
            if not SIM['on']:
                return
        return wechat_reply_flow.run(wins[0].hwnd, args.seconds, args.reply_contact,
                                     mode=args.reply_flow, path=args.reply_db)
    if args.reply_contact or args.reply_db:
        parser.error('回复测试联系人和账本须和 --reply-flow 一起指定')
    if args.resume_handoff is not None:
        if args.seconds is not None or args.clear_journal or not 1 <= args.resume_handoff <= len(WHITELIST):
            parser.error('请指定有效白名单序号，单独恢复人工接管')
        title = WHITELIST[args.resume_handoff - 1]
        history.resume_handoff(history.handoff_path(history_folder(title, args.simulate), title), reply.loan_request)
        print(f'白名单#{args.resume_handoff} 的人工接管已解除')
        return
    journal_path = Path(tempfile.gettempdir()) / 'edge-agent' / 'journal.jsonl'
    if args.clear_journal:
        startup_isolated(journal_path, clear_journal=True)
        print('发送账本已清空')
        if args.seconds is None:
            return
    if args.seconds is None:
        parser.error('需要常驻秒数或 --clear-journal')
    isolated = startup_isolated(journal_path)
    print(f'上次有 {len(isolated)} 个会话结果不确定，已隔离')
    log_dir = Path(tempfile.gettempdir()) / 'edge-agent'
    log_dir.mkdir(exist_ok=True)
    log = log_dir / f'trigger-{datetime.now():%m%d-%H%M}.log'
    sys.stdout = Tee(log)                      # 日志不放项目里：微信相关的输出不留在仓库
    print(f'日志：{log}')
    hwnd = graph.find_wechat()
    global SEND_MODE, FORCE_VISION
    SEND_MODE = 'graph' if args.graph else 'flow'
    FORCE_VISION = args.vision
    print(f"发送方式：{'旧主循环' if args.graph else '固定流程'}，适配器：{'只看图' if args.vision else '控件树优先'}")
    if args.simulate:
        print(sim_start(hwnd))
        if not SIM['on']:
            return
    print(f'开始盯微信 {args.seconds} 秒，Ctrl+C 停。白名单 {len(WHITELIST)} 个会话，别的会话来消息不动。')
    stats = serve(hwnd, args.seconds, isolated, journal_path)
    print(f"\n结束：回了 {stats['replied']} 个，待人工确认 {stats['unsure']} 个，放弃 {stats['gave_up']} 次，门铃醒了 {stats['wakes']} 次")


if __name__ == '__main__':
    main()
