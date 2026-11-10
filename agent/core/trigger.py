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
import ctypes
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from agent.core import graph
from PIL import Image

from agent.tools import capture_window, check_responsive, click, norm, ocr, set_window_size, activate, window_rect, Shot
from agent.core.check import CHAT_BOTTOM, CHAT_TOP, LIST_RIGHT, chat_title
from agent.tools.unread import bell_state, find_row, find_unread_rows

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


def chat_sig(shot) -> bytes:
    """聊天区的“样子”：缩成小图比像素，只看变没变，不读内容。

    用途：聊天区变了、门铃却没变，就说明来了消息但微信没出角标，正是要抓的那种漏。
    """
    im = shot.image.crop((LIST_RIGHT, CHAT_TOP, shot.image.width, CHAT_BOTTOM))
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
    return [{**it, 'box': [it['box'][0] + x1, it['box'][1] + y1, it['box'][2] + x1, it['box'][3] + y1]}
            for it in ocr(shot.image.crop(box))]


def title_box(shot) -> tuple[int, int, int, int]:
    # 右边 160 不要：那里是语音、视频、“…”图标，裁出来单独 OCR 会认成“?”“#+#”拼进标题（11/9 实测）
    return (LIST_RIGHT, 0, shot.image.width - 160, CHAT_TOP + 10)


def open_title(shot) -> str:
    """右边开着的是哪个会话：只 OCR 标题条，一次。"""
    return chat_title(ocr_crop(shot, title_box(shot)))


def chat_items(shot) -> list[dict]:
    """标题条 + 聊天区的 OCR，坐标换回整张截图。

    为什么裁开识别：11/8 实测整张窗口一起 OCR 时，聊天区的字一条都没认出来，裁出来单独识别就有。
    """
    return ocr_crop(shot, title_box(shot)) + ocr_crop(shot, (LIST_RIGHT, CHAT_TOP, shot.image.width, CHAT_BOTTOM))


def new_after_reply(shot: Shot, items: list[dict], task: dict) -> str:
    """我们刚发的回复下面，还有没有别的东西。不碰窗口，可以离线测。

    输入：shot 截图；items 是 chat_items(shot)；task 是回过的那个任务（to、msg）
    输出：NEW      回复下面有东西：又来了消息
          NONE     回复就是最下面一条
          UNKNOWN  标题对上了，但没认出我们的回复：继续盯，不重发
          OTHER    标题对不上：开着的已经不是这个会话，不用再盯
    为什么数像素不用 OCR：对方发单个字时 OCR 常常认不出来（11/8 一屏的“1”“2”全漏）。
    头像两列不算（对方头像会跟着新消息出现，但我们自己的头像也在那一列）。
    """
    if norm(chat_title(items)) != norm(task['to']):
        return OTHER
    chat = [it for it in items
            if it['box'][0] > LIST_RIGHT and it['box'][1] > CHAT_TOP and it['box'][3] < CHAT_BOTTOM]
    ours = [it for it in chat if norm(it['text']) == norm(task['msg'])]
    if not ours:
        return UNKNOWN
    bottom = max(it['box'][3] for it in ours)
    below = shot.image.convert('L').crop(
        (LIST_RIGHT + 60, min(bottom + 14, CHAT_BOTTOM), shot.image.width - 60, CHAT_BOTTOM))
    return NEW if sum(below.histogram()[:160]) > 20 else NONE


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


OK, RETRY, UNSURE = 'ok', 'retry', 'unsure'


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
    if norm(open_title(shot)) != norm(title):   # 已经开着就不碰列表：单人连着回时省 2 秒多
        point = find_row(shot.image, title)
        if point is None:
            print('  列表里没找到这个会话')
            return None
        click(*shot.to_screen(*point))
        time.sleep(0.5)                  # 等右边换成这个会话
        if norm(open_title(capture_window(hwnd))) != norm(title):
            print('  点开后标题对不上')
            return None
    # 再点一下输入框：点列表行后焦点停在列表上，模型的 type 是直接粘贴、不先点输入框，字会落空（11/9 实测）
    click(*shot.to_screen((LIST_RIGHT + shot.image.width) // 2, CHAT_BOTTOM + 50))
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


def run_one(hwnd: int, task: dict) -> tuple[str, Shot | None]:
    """程序点开会话，再跑一遍 D8 的主循环回它。

    输出：(结果, 点开之前那张图)。图拿去查被切走的会话；没点成时是 None。结果三种之一（看程序核对，不看模型说的“完成”）：
      OK      核对通过
      RETRY   没按过回车就停了：消息肯定没发出去，可以重试
      UNSURE  按过回车但核对没通过：消息可能已经发出去了，重试会发两条，所以不重试，交给人看
    每次都重新确认窗口：两次回复之间可能有人动过微信，大小变了核对会不准，不响应就更不能点。
    """
    t0 = time.time()
    before = open_chat(hwnd, task['to']) if prepare(hwnd) else None
    t1 = time.time()
    if before is None:
        return RETRY, None
    end = graph.build_graph().invoke(graph.new_state(task, hwnd), {'recursion_limit': 80})
    print(f'  耗时：准备+开会话 {t1 - t0:.1f} 秒，主循环 {time.time() - t1:.1f} 秒')
    if end['done']:
        return OK, before
    # 「做」按完回车会记一条 result='已按回车'（graph.act），看它就知道消息有没有可能已经发出
    if any(s['result'] == '已按回车' for s in end['steps']):
        return UNSURE, before
    return RETRY, before


# ---------- 你来写 ----------

def find(title,whitelist):
            for w in whitelist:
                if norm(title) == norm(w):
                    return w
            return  None

def pick_sessions(rows: list[dict], whitelist: list[str], pending: dict[str, int],
                  max_retry: int = MAX_RETRY) -> tuple[list[str], int]:
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
    whitelist2 = []
    unwhitelist= 0
    for row in rows:
        name = find(row['title'],whitelist)
        if name is None:
            unwhitelist += 1
            continue
        if pending.get(name,0) >= max_retry:
            continue
        if name in whitelist2:
            continue
        whitelist2.append(name)
    for name,count in pending.items():
        if name not in whitelist2 and count < max_retry:
            whitelist2.append(name)
    return (whitelist2,unwhitelist)



def serve(hwnd: int, seconds: float) -> dict:
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
    n, replied, unsure, wakes = 1, 0, 0, 0
    run = time.strftime('%H%M')          # 这次启动的时分，拼进回复里，见 make_task
    pending = {}                         # 账本 {标题: 失败次数}：扫到就记，回成功才划掉
    gave_up_at = {}                      # {标题: 放弃的时刻}：过了 COOLDOWN 秒就从账本里划掉，再有角标会重新挑它
    gave_up = 0
    watch = None                         # 最近回过的任务：它的会话一直开着，要盯聊天区
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
            names, skipped = pick_sessions(scan(hwnd), WHITELIST, pending)
            if skipped:
                print(f'  跳过 {skipped} 个非白名单会话')
            if names:
                for name in names:       # 先记账再动手：一动手就可能点掉别人的角标
                    if name not in pending:
                        pending[name] = 0
                title = names[0]
                task = make_task(title, n, run)
                who = WHITELIST.index(title) + 1  # 日志不写名字，写白名单第几个
                print(f'{time.strftime("%H:%M:%S")} 去回：白名单#{who} {task["msg"]}（还有 {len(names) - 1} 个排队）')
                result, before = run_one(hwnd, task)
                print(f'{ts()} 回复结束：{result}  前台={is_front(hwnd)}')
                note_left(before, title)
                if before is not None:   # 已经切到这个会话，原来盯的那个交给账本或 revisit 了
                    watch = None
                if result == OK:
                    replied += 1
                    n += 1
                    del pending[title]
                    watch = task         # 回了谁，开着的就是谁
                elif result == UNSURE:   # 可能已经发了：不重试，免得发两条；编号也往后走，不重复用
                    unsure += 1
                    n += 1
                    del pending[title]
                    print('  按过回车但核对没通过：可能已经发出，不重试，请人工看一眼')
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
                    seen = new_after_reply(shot, chat_items(shot), task)
                    print(f'{ts()} 回头查 白名单#{WHITELIST.index(title) + 1}：{seen}')
                    watch = task
                    if seen == NEW:
                        pending[title] = 0
                        watch = None
                continue

            # 开着的会话：不靠角标，看我们的回复下面有没有东西
            chat = None
            shot = capture_window(hwnd) if watch else None
            if shot:
                seen = new_after_reply(shot, chat_items(shot), watch)
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
    assert ok_r == len(REPLY_CASES) and passed == len(CASES), '离线测试失败'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    if sys.argv[1:] == ['--offline']:
        return run_offline()
    if len(sys.argv) != 2:
        sys.exit('用法：python -m agent.core.trigger --offline  或  python -m agent.core.trigger 600')
    log_dir = Path(tempfile.gettempdir()) / 'edge-agent'
    log_dir.mkdir(exist_ok=True)
    log = log_dir / f'trigger-{datetime.now():%m%d-%H%M}.log'
    sys.stdout = Tee(log)                      # 日志不放项目里：微信相关的输出不留在仓库
    print(f'日志：{log}')
    hwnd = graph.find_wechat()
    print(f'开始盯微信 {sys.argv[1]} 秒，Ctrl+C 停。白名单 {len(WHITELIST)} 个会话，别的会话来消息不动。')
    stats = serve(hwnd, float(sys.argv[1]))
    print(f"\n结束：回了 {stats['replied']} 个，待人工确认 {stats['unsure']} 个，放弃 {stats['gave_up']} 次，门铃醒了 {stats['wakes']} 次")


if __name__ == '__main__':
    main()
