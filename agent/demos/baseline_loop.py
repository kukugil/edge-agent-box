"""手写版主循环（工具层版）：在微信里给“文件传输助手”发一条消息，OCR 独立验收。

这是 experiments/wechat-vision/run_task.py 的搬家版：逻辑一行不改，只把它自带的截图、OCR、定位、键鼠
换成 agent.tools 里的函数。用途有两个：
  1. 工具层的回归测试：换成工具后，成功率应该和原来一样（原来 3/3）。
  2. D5–D6 正式主循环的对照组：LangGraph 版的成功率不能比它低。
     写正式版之前别照抄这里，先自己画出“看 → 想 → 做 → 核对”再动手。

和原脚本的区别：不存截图（真实账号的聊天内容不落盘），日志写到系统临时目录，只打印动作和坐标。

用法（在项目根目录）：python -m agent.demos.baseline_loop [轮数，默认 3]
运行期间不要动鼠标键盘；按 F10 随时中止。
"""
import ctypes
import json
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from agent.tools import (activate, center, chat, check_responsive, click, find_windows, ground, load_config, norm,
                         ocr, paste_text, press, screen_changed, screenshot, scroll, snap_to_text, window_rect)
from agent.tools.models import image_url

user32 = ctypes.windll.user32
CFG = load_config()
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


# ---------- 微信专用的界面知识（D11 会挪进应用配置文件） ----------
# 下面的像素范围按实验时的微信窗口大小量出来的：会话列表宽约 255px，标题栏高约 30px

def chat_title(items):
    # 右侧聊天区顶部的标题：x 在会话列表右边，y 在窗口标题栏之下、聊天内容之上
    return ''.join(it['text'] for it in items if it['box'][0] > 255 and it['box'][1] > 30 and it['box'][3] < 85)


def chat_title_is_target(items):
    return '文件传输助手' in chat_title(items)


SECTION_TITLES = ('功能', '联系人', '群聊', '聊天记录', '公众号', '最近使用过的小程序', '小程序')


def in_web_suggestions(items, pt):
    """点击点是否落在搜索下拉框的“网络搜索建议”区：从“搜索网络结果”这一行到第一个分组标题（功能/联系人…）之间。"""
    head = [it for it in items if '搜索网络结果' in it['text'] and it['box'][0] < 400]
    if not head:
        return False
    top = head[0]['box'][1]
    sections = [it['box'][1] for it in items
                if it['box'][0] < 400 and it['box'][1] > top and norm(it['text']) in SECTION_TITLES]
    if sections:
        bottom = min(sections)
    else:
        # “功能”等分组排在上面时，建议区下面没有标题；用建议行本身（OCR 把放大镜读成 Q）定下边界
        rows = [it['box'][3] for it in items if it['box'][0] < 400 and it['box'][1] > top and it['text'][:1] in 'Qq']
        bottom = max(rows) + 5 if rows else top + 220
    return pt[0] < 400 and top - 5 <= pt[1] < bottom


def message_sent(items, msg):
    # 聊天区里要有一条和目标消息完全一致的文字（只忽略空格标点），“包含”不算——残留草稿就是这么漏过去的
    in_chat = [it['text'] for it in items if it['box'][0] > 255 and 80 < it['box'][1] < 710]
    return chat_title_is_target(items) and any(norm(t) == norm(msg) for t in in_chat)


# ---------- 杂项 ----------

def find_wechat():
    """找微信主窗口，返回 hwnd 或 None。藏在托盘的不强行显示（强行显示后不响应输入）。"""
    for w in find_windows(title='微信', class_prefix='Qt'):
        if not w.visible:
            print('微信主窗口藏在托盘里，请点一下托盘里的微信图标再运行')
            continue
        if w.minimized:
            return w.hwnd  # 最小化时量不出大小；activate() 会把它还原
        l, t, r, b = window_rect(w.hwnd)
        if r - l > 300:  # 微信还有几个同名的小窗口（托盘菜单等），按宽度挑主窗口
            return w.hwnd
    return None


def aborted():
    return user32.GetAsyncKeyState(VK_F10) & 1


def parse_json(s):
    m = re.search(r'\{.*\}', s or '', re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def grab(hwnd):
    shot = screenshot(window_rect(hwnd))
    return shot, ocr(shot.image)


# ---------- 一轮任务 ----------

def run_once(hwnd, k):
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
        ok, why = activate(hwnd)
        if not ok:
            print(f'激活微信失败：{why}')
            return 'env_failed', step - 1, log
        shot, items = grab(hwnd)
        im = shot.image

        # 卡住检测：上一步做完界面没变，要明确告诉大脑；同一动作连做两次，要求换办法
        if history and not screen_changed(prev_im, im):
            history[-1] += '（但截图显示界面没有任何变化）'
        warning = ''
        if len(actions) >= 2 and actions[-1] == actions[-2]:
            warning = f'\n注意：你已经连续两次执行“{actions[-1]}”，没有达到目的。这次必须换一种完全不同的办法。'
        prev_im = im.copy()

        t0 = time.time()
        raw = chat(CFG['vlm_url'], CFG['vlm_model'], [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': image_url(im)}},
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
                pt, g_raw = ground(im, target, CFG['grounder_url'], CFG['grounder_model'], CFG['grounder_max_pixels'])
                rec['ground'] = {'raw': g_raw, 'ms': int((time.time() - t0) * 1000), 'point': pt}
                if pt is None:
                    result = f'定位失败：{g_raw}'
                else:
                    pt, snap = snap_to_text(items, target, pt)
                    rec['ground'].update({'snapped': pt, 'snap': snap})
                    if pt[1] < 32:
                        result = '被安全规则拦下：点击位置在窗口标题栏（最小化/关闭按钮一带）'
                    elif in_web_suggestions(items, pt):
                        result = ('被安全规则拦下：点的是搜索下拉框里的网络搜索建议（绿色文字那几行），会打开网页；'
                                  '要打开聊天，请点“功能”等分组标题下面带图标的那一项')
                    else:
                        click(*shot.to_screen(*pt))
                        last_click_target, title_before_click = target, chat_title(items)
                        result = f'已点击 ({pt[0]:.0f},{pt[1]:.0f}){"，" + snap if snap else ""}'
        elif act == 'type':
            text = a.get('text', '')
            # 往搜索框输入时，要求点击前后聊天标题没变；变了说明点偏打开了别的聊天，光标可能在别人的输入框里
            in_search = '搜索' in last_click_target and chat_title(items) == title_before_click
            if chat_title_is_target(items):
                paste_text(text)
                result = '已输入'
            elif in_search:
                paste_text(text, clear=False)  # 不全选：万一光标其实在别人的输入框里，也不会覆盖草稿
                time.sleep(0.8)
                _, check_items = grab(hwnd)
                box_text = ''.join(it['text'] for it in check_items if it['box'][0] < 255 and it['box'][3] < 80)
                if norm(text)[:4] in norm(box_text):
                    time.sleep(2.0)  # 下拉框是分批加载的：先出“功能”结果，网络建议稍后才插到上面，等它稳定再截下一张
                    result = '已输入到搜索框'
                else:
                    press('ctrl+z')  # 文字没进搜索框，撤销这次粘贴
                    result = '输入后搜索框里没有出现这段文字，说明光标不在搜索框，已撤销输入'
            else:
                result = '被安全规则拦下：光标不在搜索框，当前聊天也不是文件传输助手，不允许输入'
        elif act == 'key':
            key = (a.get('key') or '').lower().replace(' ', '')
            if key == 'enter' and not chat_title_is_target(items):
                result = '被安全规则拦下：当前聊天不是文件传输助手，不允许按回车'
            elif key == 'enter':
                press('enter')
                result = '已按 enter'
            elif key == 'esc':
                result = '被安全规则拦下：微信主界面按 Esc 会把窗口藏到托盘，不允许'
            elif key == 'ctrl+f':
                press('ctrl+f')
                last_click_target, title_before_click = '搜索（ctrl+f）', chat_title(items)
                result = '已按 ctrl+f'
            else:
                result = f'不支持的按键：{key}'
        elif act == 'scroll':
            pt, g_raw = ground(im, a.get('target', ''), CFG['grounder_url'], CFG['grounder_model'],
                               CFG['grounder_max_pixels'])
            if pt is None:
                result = f'定位失败：{g_raw}'
            else:
                down = a.get('direction') == 'down'
                scroll(*shot.to_screen(*pt), 5 if down else -5)
                result = f'已在 ({pt[0]:.0f},{pt[1]:.0f}) 向{"下" if down else "上"}滚动'
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

    # 独立验收：重新截图，用 OCR 核对标题和消息，不看模型自己说了什么
    activate(hwnd)
    _, final_items = grab(hwnd)
    ok = message_sent(final_items, msg)
    claimed = log['steps'][-1]['result'] if log['steps'] else ''
    print(f'模型自评：{claimed}；OCR 验收：{"成功" if ok else "失败"}')
    log.update({'claimed': claimed, 'verified': ok, 'steps_used': len(log['steps'])})
    return ('success' if ok else 'failed'), len(log['steps']), log


def reset(hwnd):
    """每轮开始前把界面摆到固定起点：切到“公众号”，再把会话列表往下滚，让“文件传输助手”不在可见范围内。
    这一步是测试环境的准备，不是被测对象，所以用 OCR 定位；顺带确认微信真的能响应输入。返回是否成功。"""
    ok, why = activate(hwnd)
    if ok:
        # 悬停测试选会话列表里相邻两行，鼠标放上去会变灰
        ok, why = check_responsive(hwnd, [(160, 200), (160, 280)])
    if not ok:
        print(f'重置失败：{why}')
        return False
    shot, _ = grab(hwnd)
    scroll(*shot.to_screen(160, 450), -40)  # 先滚回列表顶部
    time.sleep(0.8)
    shot, items = grab(hwnd)
    row = [it for it in items if it['text'] == '公众号' and it['box'][0] < 255 and it['box'][1] > 80]
    if not row:
        print('重置失败：会话列表里没找到“公众号”')
        return False
    click(*shot.to_screen(*center(row[0]['box'])))
    time.sleep(1.2)
    _, items = grab(hwnd)
    if '公众号' not in chat_title(items):
        print('重置失败：点了“公众号”但标题没变，微信窗口可能没有响应输入')
        return False
    scroll(*shot.to_screen(160, 450), 15)
    time.sleep(0.8)
    _, items = grab(hwnd)
    visible = any(it['text'].startswith('文件传') and it['box'][0] < 255 for it in items)
    print(f'重置完成：已切到公众号，文件传输助手在列表里{"仍可见" if visible else "已不可见"}')
    return True


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    hwnd = find_wechat()
    if not hwnd:
        sys.exit('没找到微信主窗口')
    # 日志里有模型对界面的描述，可能带会话名，所以放系统临时目录，不进项目
    root = Path(tempfile.gettempdir()) / 'agent-baseline' / datetime.now().strftime('%m%d-%H%M%S')
    root.mkdir(parents=True)
    summary = []
    for k in range(1, n + 1):
        if not reset(hwnd):
            summary.append((k, 'reset_failed', 0, 0))
            break  # 环境不对就停，不在没响应的窗口上浪费一轮
        t0 = time.time()
        status, steps, log = run_once(hwnd, k)
        log['seconds'] = round(time.time() - t0, 1)
        (root / f'round{k}.json').write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        summary.append((k, status, steps, log['seconds']))
        if status in ('aborted', 'env_failed'):
            break
    print('\n===== 汇总 =====')
    for k, status, steps, sec in summary:
        print(f'第 {k} 轮：{status}，{steps} 步，{sec} 秒')
    print(f'成功 {sum(s == "success" for _, s, _, _ in summary)}/{len(summary)}；日志在 {root}')


if __name__ == '__main__':
    main()
