"""读消息：从聊天区的 OCR 结果里拼出"谁说了什么"，交给回复生成用（D15）。

纯函数，OCR 结果进、气泡列表出，可以离线测。不碰窗口，不打印内容。

为什么按行拼气泡：RapidOCR 一行一个字块，长句和多行气泡会被拆成好几块。
同一个气泡里上下两行几乎贴着、左边对齐；两个气泡之间隔着气泡边距，空隙大得多。

运行（在项目根目录）：
  python -m agent.core.messages     离线测拼气泡
"""
import sys

from agent.core.check import CHAT_TOP, DEFAULT_LAYOUT, LIST_RIGHT, is_ours
from agent.tools import norm

THEM, US = 'them', 'us'
LINE_GAP = 12      # 同一气泡上下两行的最大空隙（px）。真机量出来再调，见 D15 记录
ALIGN = 10         # 同一气泡各行左边对齐的容差
MID_BAND = 40      # 中心离聊天区中线这么近、又不贴两边的字块，当作时间分隔线，不算任何一方
MIN_SCORE = 0.8    # 一行识别把握低于它，整个气泡标成读不清


def _side(item: dict, lay: dict) -> str | None:
    """输入字块和布局，输出 THEM / US，居中的时间线等交回 None。"""
    left, right = LIST_RIGHT, lay['right']
    mid = (left + right) / 2
    cx = (item['box'][0] + item['box'][2]) / 2
    # 时间线居中、离两边都远；对方的短气泡虽然窄，但贴着左边头像，不会居中
    if abs(cx - mid) < MID_BAND and item['box'][0] > left + 120 and item['box'][2] < right - 120:
        return None
    return US if is_ours(item, lay) else THEM


def bubbles(items: list[dict], lay=DEFAULT_LAYOUT) -> list[dict]:
    """输入整窗或裁块换回整窗坐标的 OCR 字块和布局，输出聊天区气泡（从上到下）。

    每个气泡：{'who': THEM/US, 'text': 各行直接拼接, 'box': 外框, 'clear': 是否读得清, 'cut': 是否被上沿切掉}
    坑：多行气泡拼接时不加空格也不加换行。微信自动折行处本来就没有字；真换行的多行消息会丢掉换行，
        给模型看不影响意思。
    """
    chat = [it for it in items if it['box'][0] > LIST_RIGHT and it['box'][1] > CHAT_TOP
            and it['box'][3] < lay['bottom'] and it['text'].strip()]
    out: list[dict] = []
    for it in sorted(chat, key=lambda it: (it['box'][1], it['box'][0])):
        x1, y1, x2, y2 = it['box']
        last = out[-1] if out else None
        # 坑（11/12 真机）：我方气泡靠右，但气泡里的字是左对齐的，各行右边参差不齐；
        # 原来按右边对齐判，把一条两行的我方消息拆成了两条。两边都按左边对齐判。
        # 续行先认、再判左右：短的续行可能落在中间被当成时间线，长消息的续行中心也可能过了中线
        if last and y1 - last['box'][3] <= LINE_GAP and abs(x1 - last['box'][0]) <= ALIGN:
            last['text'] += it['text']
            last['box'] = [min(last['box'][0], x1), last['box'][1], max(last['box'][2], x2), y2]
            last['clear'] = last['clear'] and it.get('score', 1) >= MIN_SCORE
            continue
        who = _side(it, lay)
        if who is None:
            continue
        out.append({'who': who, 'text': it['text'], 'box': [x1, y1, x2, y2],
                    'clear': it.get('score', 1) >= MIN_SCORE,
                    # 贴着聊天区上沿的气泡可能只露出下半截，读到的不是整句
                    'cut': y1 < CHAT_TOP + 15})
    return out


def latest_from_them(bs: list[dict]) -> list[dict]:
    """输入 bubbles 的结果，输出最后一条我方气泡下面的对方气泡；没有我方气泡时交回全部对方气泡。

    用途：这就是这次要回的那几句。
    """
    last_us = max((i for i, b in enumerate(bs) if b['who'] == US), default=-1)
    return [b for b in bs[last_us + 1:] if b['who'] == THEM]


# ---------- 读一次：控件树为主，OCR 兜底 ----------

FRAME_GAP = 0.3     # 两帧之间隔多久：太短看不出"刚出来还没画完"，太长每次读都慢


def _result(ok: bool, source: str, msgs: list[dict], reason: str) -> dict:
    return {'ok': ok, 'source': source, 'msgs': msgs, 'reason': reason}


def _title_ok(shot, title: str) -> bool:
    # 晚导入：trigger 以后会导入本文件，放顶上会循环导入
    from agent.core.trigger import open_title
    t = open_title(shot)
    return bool(t) and norm(t) == norm(title)


def _uia_frame(hwnd, lst, shot, now) -> list[dict] | None:
    """用一张截图判左右，读一帧控件树。有条目左右判不出时返回 None。"""
    import numpy as np
    from agent.tools import chatlog, window_rect
    items, frame = chatlog.list_items(lst, window_rect(hwnd)[:2])
    # 行顶被切掉的看不到头像、Name 也可能只是半截，丢掉；完全在列表外的（虚拟列表留的）也丢掉
    items = [it for it in items if it['box'][1] >= frame[1] and it['box'][1] < frame[3]]
    gray = np.asarray(shot.image.convert('L')).astype(float)
    sides = [chatlog.NONE if chatlog.kind_of(it['cls'], it['text']) == 'time'
             else chatlog.avatar_side(gray, it['box']) for it in items]
    if any(s not in (chatlog.THEM, chatlog.US) for it, s in zip(items, sides)
           if chatlog.kind_of(it['cls'], it['text']) != 'time'):
        return None
    return chatlog.to_msgs(items, sides, now)


def _ocr_msgs(shot) -> tuple[list[dict] | None, str]:
    """OCR 兜底：拼气泡，换成同样的消息格式。读不清的整次作废。

    坑：OCR 看不见表情包、图片、语音（没有字），也读不到时间线的时刻（at 全是 None）。
    所以只发表情包时这里读不出新消息，不会回"怎么啦"；这是兜底的已知缺口。
    """
    from agent.core.check import layout
    from agent.core.trigger import chat_items
    lay = layout(shot.image)
    bs = [b for b in bubbles(chat_items(shot, lay), lay) if not b['cut']]
    if not all(b['clear'] for b in bs):
        return None, 'OCR 有气泡读不清'
    return [{'who': b['who'], 'kind': 'text', 'text': b['text'], 'at': None} for b in bs], 'ok'


def _chat_preview(shot, lst, msgs, origin):
    """从已经核对会话的截图截取最多六条可见消息所在区域。"""
    left, top = origin
    rect = lst.BoundingRectangle
    x1, y1 = rect.left - left, rect.top - top
    x2, y2 = rect.right - left, rect.bottom - top
    if not (0 < x1 < x2 <= shot.image.width and 0 < y1 < y2 <= shot.image.height):
        return None
    visible = [m['box'] for m in msgs if m['who'] in ('us', 'them')
               and y1 <= m['box'][1] < y2]
    if visible:
        y1 = max(y1, visible[-6:][0][1] - 8)
        y2 = min(y2, max(box[3] for box in visible[-6:]) + 12)
    return shot.image.crop((x1, y1, x2, y2))


def read_chat(hwnd: int, title: str, now=None, *, preview=False) -> dict:
    """读一次当前会话的聊天记录。

    输入：hwnd 微信窗口；title 应该开着的会话名（白名单里的）；now 当前时间，不传就用现在。
    输出：{'ok': bool, 'source': 'uia'|'ocr'|'', 'msgs': [消息...], 'reason': str}
      ok=False 时 msgs 是空的，调用方不要拿去回复，等一会儿再读。
    规矩：标题对不上就不读消息（11/11 探针读到过白名单外的会话）；最小化截不到图核对不了标题，也不读。
    """
    import time
    from datetime import datetime
    from agent.tools import capture_window, chatlog
    now = now or datetime.now()
    shot = capture_window(hwnd)
    if shot is None:
        return _result(False, '', [], '截不到图（最小化？），核对不了标题')
    if not _title_ok(shot, title):
        return _result(False, '', [], '标题对不上或读不清')
    try:
        lst = chatlog.message_list(hwnd)
    except Exception:
        lst = None
    if lst is None:
        msgs, why = _ocr_msgs(shot)
        return _result(msgs is not None, 'ocr', msgs or [], why)
    first = _uia_frame(hwnd, lst, shot, now)
    time.sleep(FRAME_GAP)
    shot2 = capture_window(hwnd)
    if shot2 is None:
        return _result(False, 'uia', [], '第二帧截不到图')
    # 标题条像素一模一样就不用再 OCR（省 0.5 秒左右）；变了才重新核对
    from agent.core.trigger import title_box
    box = title_box(shot2)
    if shot.image.crop(box).tobytes() != shot2.image.crop(box).tobytes() and not _title_ok(shot2, title):
        return _result(False, 'uia', [], '两帧之间切走了会话')
    second = _uia_frame(hwnd, lst, shot2, now)
    if first is None or second is None:
        return _result(False, 'uia', [], '有消息分不出左右（刚出来或在滚动）')
    if not chatlog.same_frames(first, second):
        return _result(False, 'uia', [], '两帧不一致（刚来消息或在滚动）')
    result = _result(True, 'uia', second, 'ok')
    if preview:
        image = _chat_preview(shot2, lst, second, (shot2.left, shot2.top))
        # 裁剪过程中切换会话或移动窗口，都不保留截图。
        final = capture_window(hwnd)
        if (image is not None and final is not None and (final.left, final.top) == (shot2.left, shot2.top)
                and final.image.size == shot2.image.size
                and final.image.crop(box).tobytes() == shot2.image.crop(box).tobytes()):
            result['preview'] = image
    return result


def wait_quiet(read, quiet: float = 3.0, timeout: float = 12.0, since: float | None = None,
               step: float = 0.5, clock=None, sleep=None) -> dict:
    """连发合并：反复读，直到对方停了 quiet 秒没再发，交出最后一次读到的。

    输入：read 无参函数，返回 read_chat 的结果；quiet 要静多久；timeout 最多等多久；
         since 第一次发现新消息的时刻（门铃时间，time.time()）：从那时算静，省掉重复等待；
         clock / sleep 离线测时换成假的。
    输出：read_chat 的结果，另加 'quiet': 是否真的等到静了（超时时 False，照样交出最新一次）。
    为什么只看消息列表变没变、不看是谁发的：我们这边不会在等的时候发东西；有人手动回了，也该重新判断。
    """
    import time
    clock, sleep = clock or time.time, sleep or time.sleep
    start = clock()
    last_change = since if since is not None else start
    last_key, good = None, None
    reads = fails = 0
    while True:
        r = read()
        reads += 1
        fails += not r['ok']
        if r['ok']:
            key = [(m['who'], m['kind'], m['text']) for m in r['msgs']]
            if key != last_key:
                if last_key is not None:
                    last_change = clock()
                last_key = key
            good = r
            if clock() - last_change >= quiet:
                return {**r, 'quiet': True, 'reads': reads, 'fails': fails}
        if clock() - start >= timeout:
            out = good or r
            return {**out, 'quiet': False, 'reads': reads, 'fails': fails}
        sleep(step)


# ---------- 离线测试 ----------

def _it(text, x1, y1, x2, y2, score=0.99):
    return {'text': text, 'box': [x1, y1, x2, y2], 'score': score}


CASES = [
    # (说明, 字块, 应该拼出的 [(who, text)])
    ('一左一右', [_it('你好', 320, 200, 360, 220), _it('在的', 860, 260, 900, 280)],
     [(THEM, '你好'), (US, '在的')]),
    ('三行长气泡拼成一句', [_it('第一行文字', 320, 200, 600, 220), _it('第二行文字', 320, 226, 600, 246),
                     _it('第三行', 320, 252, 400, 272)], [(THEM, '第一行文字第二行文字第三行')]),
    ('两个挨着的对方气泡不拼', [_it('第一句', 320, 200, 400, 220), _it('第二句', 320, 250, 400, 270)],
     [(THEM, '第一句'), (THEM, '第二句')]),
    ('时间线不算', [_it('你好', 320, 200, 360, 220), _it('14:05', 585, 240, 630, 256), _it('嗯', 320, 280, 340, 300)],
     [(THEM, '你好'), (THEM, '嗯')]),
    ('对方单字气泡不当时间线', [_it('好', 320, 200, 336, 220)], [(THEM, '好')]),
    ('我方多行：字左对齐、右边参差', [_it('我方第一行很长', 600, 200, 900, 220), _it('第二行', 600, 226, 680, 246)],
     [(US, '我方第一行很长第二行')]),
    ('我方长消息的短续行在中间，不当时间线', [_it('我方很长很长的第一行', 450, 200, 900, 220), _it('尾巴', 450, 226, 490, 246)],
     [(US, '我方很长很长的第一行尾巴')]),
    ('我方两条挨着的不拼', [_it('第一条消息', 760, 200, 900, 220), _it('短', 870, 250, 900, 270)],
     [(US, '第一条消息'), (US, '短')]),
    ('输入框里的字不算', [_it('你好', 320, 200, 360, 220), _it('草稿', 320, 700, 360, 720)], [(THEM, '你好')]),
    ('列表里的字不算', [_it('某会话', 70, 200, 160, 220), _it('你好', 320, 200, 360, 220)], [(THEM, '你好')]),
]


def run_offline() -> None:
    ok = 0
    for i, (desc, items, want) in enumerate(CASES, 1):
        got = [(b['who'], b['text']) for b in bubbles(items)]
        ok += got == want
        print(f'{i}. {"对" if got == want else "错"}  {desc}' + ('' if got == want else f'  应该 {want}，实际 {got}'))
    bs = bubbles([_it('旧问题', 320, 150, 400, 170), _it('旧回复', 860, 190, 900, 210),
                  _it('新问题1', 320, 240, 400, 260), _it('新问题2', 320, 290, 400, 310)])
    got = [b['text'] for b in latest_from_them(bs)]
    ok += got == ['新问题1', '新问题2']
    print(f'{len(CASES) + 1}. {"对" if got == ["新问题1", "新问题2"] else "错"}  只取我方最后一条下面的')
    low = bubbles([_it('看不清', 320, 200, 400, 220, score=0.5)])
    ok += low[0]['clear'] is False
    print(f'{len(CASES) + 2}. {"对" if low[0]["clear"] is False else "错"}  低把握标成读不清')
    total = len(CASES) + 2

    # wait_quiet：假时钟。读到的消息在第 0、1 秒各变一次，之后不变
    def fake(seq):
        t = {'now': 0.0}
        def clock():
            return t['now']
        def sleep(s):
            t['now'] += s
        def read():
            for at, msgs, good in reversed(seq):
                if t['now'] >= at:
                    return {'ok': good, 'source': 'uia', 'reason': '', 'msgs': [
                        {'who': THEM, 'kind': 'text', 'text': x, 'at': None} for x in msgs]}
        return read, clock, sleep, t
    for desc, seq, want_quiet, want_texts, want_t in [
        ('连发两句，等最后一句后静 3 秒', [(0, ['a'], True), (1, ['a', 'b'], True)], True, ['a', 'b'], 4.0),
        ('一直在发，12 秒超时交最新的', [(i, [str(j) for j in range(i + 1)], True) for i in range(20)],
         False, [str(j) for j in range(13)], 12.0),
        ('读失败的那次不算', [(0, ['a'], True), (1, [], False), (2, ['a'], True)], True, ['a'], 3.0),
    ]:
        read, clock, sleep, t = fake(seq)
        r = wait_quiet(read, clock=clock, sleep=sleep)
        got = (r['quiet'], [m['text'] for m in r['msgs']], t['now'])
        good = got == (want_quiet, want_texts, want_t)
        ok += good
        total += 1
        print(f'{total}. {"对" if good else "错"}  {desc}' + ('' if good else f'  实际 {got}'))
    read, clock, sleep, t = fake([(0, ['a'], True)])
    r = wait_quiet(read, since=-2.5, clock=clock, sleep=sleep)
    good = (r['quiet'], t['now']) == (True, 0.5)
    ok += good
    total += 1
    print(f'{total}. {"对" if good else "错"}  门铃 2.5 秒前就看到了，只再等 0.5 秒' + ('' if good else f'  实际 {t["now"]}'))
    print(f'\n{ok}/{total} 对')
    assert ok == total, '离线测试失败'


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    run_offline()
