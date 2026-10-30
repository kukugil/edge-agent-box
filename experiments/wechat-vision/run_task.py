"""真实闭环任务：在微信里给“文件传输助手”发一条消息。

分工（最小的 agent 循环：看 → 想 → 做 → 再看）：
  27B（大脑）  看截图，决定下一步做什么动作
  UI-TARS（手眼）把“点哪个元素”变成坐标
  本机          执行鼠标键盘；OCR 做安全检查和最终验收

用法：python run_task.py [轮数，默认 3]
运行期间不要动鼠标键盘；按 F10 随时中止。
每一步的截图（红圈=点击位置）和日志存在 runs/ 下。
"""
import ctypes
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 物理像素，截图和点击坐标才一致
except OSError:
    pass

import uiautomation as auto
from PIL import Image, ImageDraw, ImageGrab

import eval as ev  # 复用 chat / ocr / method_d（UI-TARS 定位）

user32 = ctypes.windll.user32
HERE = Path(__file__).parent
MAX_STEPS = 12
VK_F10 = 0x79

PLAN_PROMPT = '''你在操作一台 Windows 电脑上的微信，图片是当前的微信窗口截图。
任务：{task}

已经执行的步骤：
{history}

根据当前截图决定下一步，只做一个动作。只输出一个 JSON，格式是下面之一：
{{"thought": "一句话：当前界面是什么、为什么这么做", "action": "click", "target": "要点击的元素，写清位置和外观，例如：左侧会话列表里的“文件传输助手”"}}
{{"thought": "...", "action": "type", "text": "要输入的文字（输入到当前光标所在的输入框）"}}
{{"thought": "...", "action": "key", "key": "enter 或 ctrl+f（ctrl+f 会把光标放到左上角的搜索框）"}}
{{"thought": "...", "action": "scroll", "target": "要滚动的区域，例如：左侧会话列表", "direction": "up 或 down"}}
{{"thought": "...", "action": "done"}}      （截图里能确认任务已经完成）
{{"thought": "...", "action": "fail", "reason": "..."}}
target 里如果元素上有文字，用中文引号把文字括起来，例如：左上角写着“搜索”的搜索框。

微信界面常识（这个应用的“操作手册”）：
- 在会话列表里点开一个会话后，光标会自动进入消息输入框，可以直接 type。
- 消息输入框是聊天区底部、一排小图标（表情、文件夹、剪刀、麦克风）上方的大片白色区域，不是那排图标本身。
- 会话列表里找不到目标时，用搜索：点左上角写着“搜索”的搜索框（或按 ctrl+f），输入名称，再点下拉结果里的目标。
- 搜索下拉框分两部分：上面“搜索网络结果”以及带放大镜图标、绿色文字的那几行是网络搜索建议，点了会打开网页，不要点；
  真正打开聊天的是下面“功能”“联系人”“群聊”等分组标题下、带头像或图标的那一项（例如“功能”下面带绿色图标的“文件传输助手”）。

如果上一步没有达到预期，先根据截图纠正；同一个办法试了两次都不行，必须换一种办法。{warning}'''


# ---------- 窗口、截图、鼠标键盘 ----------

def find_wechat():
    # 用 Win32 枚举（UIA 列不出隐藏窗口）。最小化的正常还原；隐藏到托盘的不强行显示——
    # 实测强行显示后微信不响应输入，只能让人点托盘图标
    found = []
    buf_t, buf_c = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(64)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        user32.GetWindowTextW(hwnd, buf_t, 64)
        user32.GetClassNameW(hwnd, buf_c, 64)
        if buf_t.value == '微信' and buf_c.value.startswith('Qt'):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for hwnd in found:
        if not user32.IsWindowVisible(hwnd):
            print('微信主窗口藏在托盘里，请点一下托盘里的微信图标再运行')
            continue
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
            time.sleep(0.8)
        w = auto.ControlFromHandle(hwnd)
        if w and w.BoundingRectangle.width() > 300:
            return w
    return None


def pid_of(hwnd):
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def activate(w):
    hwnd = w.NativeWindowHandle
    if user32.IsWindowVisible(hwnd) and pid_of(user32.GetForegroundWindow()) == pid_of(hwnd):
        return  # 微信（含它的下拉框等弹出窗口）已在前台，不再按 Alt，免得干扰弹出层
    user32.ShowWindow(hwnd, 9)
    user32.keybd_event(0x12, 0, 0, 0)  # 按一下 Alt，Windows 才允许别的程序把窗口切到前台
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)


def screenshot(w, path):
    r = w.BoundingRectangle
    im = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
    im.save(path)
    return im, (r.left, r.top)


def click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(2, 0, 0, 0, 0)
    user32.mouse_event(4, 0, 0, 0, 0)


def type_text(text, select_all=True):
    auto.SetClipboardText(text)  # 走剪贴板粘贴，避开中文输入法
    if select_all:
        auto.SendKeys('{Ctrl}a', waitTime=0.1)  # 先全选，粘贴时覆盖掉输入框里残留的草稿
    auto.SendKeys('{Ctrl}v', waitTime=0.1)


def scroll(x, y, ticks):
    user32.SetCursorPos(int(x), int(y))
    user32.mouse_event(0x0800, 0, 0, -120 * ticks, 0)  # 负数=向下滚


def aborted():
    return user32.GetAsyncKeyState(VK_F10) & 1


# ---------- 安全检查和验收（OCR，不信模型自己说的） ----------

PAD_TOP = 56  # 实验 2（pad_test.py）：顶部补 56px 白边后 UI-TARS 从 33/38 升到 37/38，贴着顶边的元素不再偏下


def ground(shot, target, im):
    """UI-TARS 定位：先在截图顶部补白边，再把坐标换算回原图。"""
    padded = Image.new('RGB', (im.width, im.height + PAD_TOP), 'white')
    padded.paste(im.convert('RGB'), (0, PAD_TOP))
    path = shot.with_name(shot.stem + '-pad.png')
    padded.save(path)
    pt, raw = ev.method_d(path, None, {'instruction': '点击' + target}, padded.size)
    return (pt[0], pt[1] - PAD_TOP) if pt else None, raw


def chat_title(items):
    # 右侧聊天区顶部的标题：x 在会话列表右边，y 在窗口标题栏（最小化/关闭按钮）之下、聊天内容之上
    return ''.join(it['text'] for it in items if it['box'][0] > 255 and it['box'][1] > 30 and it['box'][3] < 85)


def ocr_snap(items, target, pt):
    """用 OCR 校正 UI-TARS 的点：目标描述里引号中的文字，在 OCR 结果里找到后，取离 UI-TARS 点最近的那个文字框中心。"""
    quoted = []
    for m in re.finditer(r'[“"‘\']([^”"’\']+)[”"’\']', target):
        after = target[m.end():m.end() + 6]
        # “‘发送’按钮左侧的输入框”里，“发送”是参照物不是目标，跳过
        if len(m.group(1)) >= 2 and not any(x in after for x in ('左', '右', '上方', '下方', '旁', '附近', '之间')):
            quoted.append(m.group(1))
    cands = [it for it in items for q in quoted if ev.norm(q) in ev.norm(it['text'])]
    if not cands:
        return pt, ''
    best = min(cands, key=lambda it: (ev.center(it['box'])[0] - pt[0]) ** 2 + (ev.center(it['box'])[1] - pt[1]) ** 2)
    c = ev.center(best['box'])
    if (c[0] - pt[0]) ** 2 + (c[1] - pt[1]) ** 2 > 60 ** 2:
        return pt, ''  # 离得远，说明 OCR 匹配到的可能是别处的同名文字，不校正（已知偏差约 28px）
    return c, f'OCR 校正到“{best["text"]}”'


SECTION_TITLES = ('功能', '联系人', '群聊', '聊天记录', '公众号', '最近使用过的小程序', '小程序')


def in_web_suggestions(items, pt):
    """点击点是否落在搜索下拉框的“网络搜索建议”区：从“搜索网络结果”这一行到第一个分组标题（功能/联系人…）之间。"""
    head = [it for it in items if '搜索网络结果' in it['text'] and it['box'][0] < 400]
    if not head:
        return False
    top = head[0]['box'][1]
    sections = [it['box'][1] for it in items
                if it['box'][0] < 400 and it['box'][1] > top and ev.norm(it['text']) in SECTION_TITLES]
    if sections:
        bottom = min(sections)
    else:
        # “功能”等分组排在上面时，建议区下面没有标题；用建议行本身（OCR 把放大镜读成 Q）定下边界
        rows = [it['box'][3] for it in items if it['box'][0] < 400 and it['box'][1] > top and it['text'][:1] in 'Qq']
        bottom = max(rows) + 5 if rows else top + 220
    return pt[0] < 400 and top - 5 <= pt[1] < bottom


def screen_changed(a, b):
    from PIL import ImageChops
    if a is None or b is None or a.size != b.size:
        return True
    return ImageChops.difference(a.convert('L'), b.convert('L')).point(lambda v: 255 if v > 30 else 0).getbbox() is not None


def chat_title_is_target(items):
    return '文件传输助手' in chat_title(items)


def message_sent(items, msg):
    # 聊天区里要有一条和目标消息完全一致的文字（只忽略空格标点），“包含”不算
    in_chat = [it['text'] for it in items if it['box'][0] > 255 and 80 < it['box'][1] < 710]
    return chat_title_is_target(items) and any(ev.norm(t) == ev.norm(msg) for t in in_chat)


def parse_json(s):
    m = re.search(r'\{.*\}', s or '', re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


# ---------- 一轮任务 ----------

def run_once(w, k, out):
    code = datetime.now().strftime('K%H%M%S')
    msg = f'agent闭环测试 {code}'
    task = f'打开“文件传输助手”的聊天，发送消息：{msg}，并确认消息已经发出去。'
    history, actions, log = [], [], {'task': task, 'steps': []}
    last_click_target, title_before_click = '', ''
    prev_im = None
    print(f'\n===== 第 {k} 轮：{task}')

    for step in range(1, MAX_STEPS + 1):
        if aborted():
            return 'aborted', step - 1, log
        activate(w)
        shot = out / f'{step:02d}.png'
        im, (left, top) = screenshot(w, shot)
        items = ev.ocr(shot)

        # 卡住检测：上一步做完界面没变，要明确告诉大脑；同一动作连做两次，要求换办法
        if history and not screen_changed(prev_im, im):
            history[-1] += '（但截图显示界面没有任何变化）'
        warning = ''
        if len(actions) >= 2 and actions[-1] == actions[-2]:
            warning = f'\n注意：你已经连续两次执行“{actions[-1]}”，没有达到目的。这次必须换一种完全不同的办法。'
        prev_im = im.copy()

        t0 = time.time()
        raw = ev.chat(ev.CFG['vlm_url'], ev.CFG['vlm_model'], [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': ev.image_url(shot)}},
            {'type': 'text', 'text': PLAN_PROMPT.format(task=task, history='\n'.join(history) or '（无）', warning=warning)}]}],
            max_tokens=300)
        plan_ms = int((time.time() - t0) * 1000)
        a = parse_json(raw)
        act = a.get('action')
        rec = {'step': step, 'plan': a or raw, 'plan_ms': plan_ms}
        print(f'[{step}] {a.get("thought", "")}  → {act} {a.get("target") or a.get("text") or a.get("key") or ""}  ({plan_ms}ms)')

        result = ''
        if act == 'click':
            target = a.get('target', '')
            if '发送' in target and not chat_title_is_target(items):
                result = '被安全规则拦下：当前聊天不是文件传输助手，不允许点发送'
            elif any(x in target for x in ('关闭', '最小化', '最大化', '退出')):
                result = '被安全规则拦下：任务不需要关闭或最小化窗口'
            else:
                t0 = time.time()
                pt, g_raw = ground(shot, target, im)
                rec['ground'] = {'raw': g_raw, 'ms': int((time.time() - t0) * 1000), 'point': pt}
                if pt is None:
                    result = f'定位失败：{g_raw}'
                else:
                    raw_pt = pt
                    pt, snap = ocr_snap(items, target, pt)
                    rec['ground'].update({'snapped': pt, 'snap': snap})
                    if pt[1] < 32:
                        result = '被安全规则拦下：点击位置在窗口标题栏（最小化/关闭按钮一带）'
                    elif in_web_suggestions(items, pt):
                        result = ('被安全规则拦下：点的是搜索下拉框里的网络搜索建议（绿色文字那几行），会打开网页；'
                                  '要打开聊天，请点“功能”等分组标题下面带图标的那一项')
                    else:
                        click(left + pt[0], top + pt[1])
                        d = ImageDraw.Draw(im)
                        if snap:  # 灰圈=UI-TARS 原始点，红圈=实际点击点
                            d.ellipse([raw_pt[0] - 8, raw_pt[1] - 8, raw_pt[0] + 8, raw_pt[1] + 8], outline=(150, 150, 150), width=2)
                        d.ellipse([pt[0] - 10, pt[1] - 10, pt[0] + 10, pt[1] + 10], outline=(255, 0, 0), width=3)
                        im.save(shot)
                        last_click_target, title_before_click = target, chat_title(items)
                        result = f'已点击 ({pt[0]:.0f},{pt[1]:.0f}){"，" + snap if snap else ""}'
        elif act == 'type':
            text = a.get('text', '')
            # 往搜索框输入时，要求点击前后聊天标题没变；变了说明点偏打开了别的聊天，光标可能在别人的输入框里
            in_search = '搜索' in last_click_target and chat_title(items) == title_before_click
            if chat_title_is_target(items):
                type_text(text)
                result = '已输入'
            elif in_search:
                type_text(text, select_all=False)  # 不全选：万一光标其实在别人的输入框里，也不会覆盖草稿
                time.sleep(0.8)
                check = out / f'{step:02d}-check.png'
                screenshot(w, check)
                box_text = ''.join(it['text'] for it in ev.ocr(check) if it['box'][0] < 255 and it['box'][3] < 80)
                if ev.norm(text)[:4] in ev.norm(box_text):
                    time.sleep(2.0)  # 下拉框是分批加载的：先出“功能”结果，网络建议稍后才插到上面，等它稳定再截下一张
                    result = '已输入到搜索框'
                else:
                    auto.SendKeys('{Ctrl}z', waitTime=0.1)  # 文字没进搜索框，撤销这次粘贴
                    result = '输入后搜索框里没有出现这段文字，说明光标不在搜索框，已撤销输入'
            else:
                result = '被安全规则拦下：光标不在搜索框，当前聊天也不是文件传输助手，不允许输入'
        elif act == 'key':
            key = (a.get('key') or '').lower().replace(' ', '')
            if key == 'enter' and not chat_title_is_target(items):
                result = '被安全规则拦下：当前聊天不是文件传输助手，不允许按回车'
            elif key == 'enter':
                auto.SendKeys('{Enter}', waitTime=0.1)
                result = '已按 enter'
            elif key == 'esc':
                result = '被安全规则拦下：微信主界面按 Esc 会把窗口藏到托盘，不允许'
            elif key == 'ctrl+f':
                auto.SendKeys('{Ctrl}f', waitTime=0.1)
                last_click_target, title_before_click = '搜索（ctrl+f）', chat_title(items)
                result = '已按 ctrl+f'
            else:
                result = f'不支持的按键：{key}'
        elif act == 'scroll':
            pt, g_raw = ground(shot, a.get('target', ''), im)
            if pt is None:
                result = f'定位失败：{g_raw}'
            else:
                scroll(left + pt[0], top + pt[1], 5 if a.get('direction') == 'down' else -5)
                result = f'已在 ({pt[0]:.0f},{pt[1]:.0f}) 向{"下" if a.get("direction") == "down" else "上"}滚动'
        elif act in ('done', 'fail'):
            rec['result'] = act
            log['steps'].append(rec)
            break
        else:
            result = f'无法解析模型输出：{raw[:80]}'

        rec['result'] = result
        log['steps'].append(rec)
        desc = f'{act} {a.get("target") or a.get("text") or a.get("key") or ""}'
        actions.append(desc)
        history.append(f'{step}. {desc} → {result}')
        print(f'     {result}')
        time.sleep(1.0)

    # 独立验收：重新截图，用 OCR 核对标题和消息
    activate(w)
    final = out / 'final.png'
    screenshot(w, final)
    ok = message_sent(ev.ocr(final), msg)
    claimed = log['steps'][-1]['result'] if log['steps'] else ''
    print(f'模型自评：{claimed}；OCR 验收：{"成功" if ok else "失败"}')
    log.update({'claimed': claimed, 'verified': ok, 'steps_used': len(log['steps'])})
    return ('success' if ok else 'failed'), len(log['steps']), log


def reset(w, out):
    """每轮开始前把界面摆到固定起点：切到“公众号”，再把会话列表往下滚，让“文件传输助手”不在可见范围内。
    这一步是测试环境的准备，不是被测对象，所以用 OCR 定位，并且顺带验证微信窗口确实在响应输入。返回是否成功。"""
    activate(w)
    shot = out / '00-reset.png'
    im, (left, top) = screenshot(w, shot)
    scroll(left + 160, top + 450, -40)  # 先滚回列表顶部
    time.sleep(0.8)
    im, (left, top) = screenshot(w, shot)
    row = [it for it in ev.ocr(shot) if it['text'] == '公众号' and it['box'][0] < 255 and it['box'][1] > 80]
    if not row:
        print('重置失败：会话列表里没找到“公众号”')
        return False
    x, y = ev.center(row[0]['box'])
    click(left + x, top + y)
    time.sleep(1.2)
    screenshot(w, shot)
    if '公众号' not in chat_title(ev.ocr(shot)):
        print('重置失败：点了“公众号”但标题没变，微信窗口可能没有响应输入')
        return False
    scroll(left + 160, top + 450, 15)
    time.sleep(0.8)
    screenshot(w, shot)
    visible = any(it['text'].startswith('文件传') and it['box'][0] < 255 for it in ev.ocr(shot))
    print(f'重置完成：已切到公众号，文件传输助手在列表里{"仍可见" if visible else "已不可见"}')
    return True


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    w = find_wechat()
    if not w:
        sys.exit('没找到微信主窗口')
    root = HERE / 'runs' / datetime.now().strftime('%m%d-%H%M%S')
    summary = []
    for k in range(1, n + 1):
        out = root / f'round{k}'
        out.mkdir(parents=True)
        if not reset(w, out):
            summary.append((k, 'reset_failed', 0, 0))
            break  # 环境不对就停，不在没响应的窗口上浪费一轮
        t0 = time.time()
        status, steps, log = run_once(w, k, out)
        log['seconds'] = round(time.time() - t0, 1)
        (out / 'log.json').write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        summary.append((k, status, steps, log['seconds']))
        if status == 'aborted':
            break
    print('\n===== 汇总 =====')
    for k, status, steps, sec in summary:
        print(f'第 {k} 轮：{status}，{steps} 步，{sec} 秒')
    print(f'成功 {sum(s == "success" for _, s, _, _ in summary)}/{len(summary)}；日志在 {root}')


if __name__ == '__main__':
    main()
