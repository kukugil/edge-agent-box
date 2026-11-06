"""邮件版的规矩和核对：和 check.py 一个套路，纯函数，读到的页面文字进、对错出，先离线测。

邮件和微信的区别：网页能直接读控件树（agent/tools/web.py 的 read_page），文字和位置都是准的，
不用 OCR，也不用 UI-TARS。read_page 交回的格式和 ocr() 一样，多两个键：
  'kind'  控件种类，'TextControl'、'EditControl' 等
  'value' 输入框里填的内容（'text' 是输入框的标签，比如“主题”）

实测出来的坑（11/4，QQ 邮箱网页版）：
  - 收件人填好后变成一个小块，块上只显示昵称，读不到完整地址。这个测试号的昵称就是地址 @ 前面那串号码。
  - 发完直接跳回收件箱，没有“发送成功”页。要自己去「已发送」里找。
  - 不能看收件箱：连着发几封，投递会被延后。

你要做的：填 recipients_ok、count_subject、verify_replied 三个函数（删掉 raise NotImplementedError）。

运行（在项目根目录）：python -m agent.core.mail_check     成功时最后一行打印：邮件核对逻辑全对。
"""
import sys

from agent.tools import norm

NAV_RIGHT = 220        # 左边文件夹栏的右边界：x 小于它的是“收件箱”“已发送”这些文件夹名
TO_ROW = (195, 240)    # 写信页“收件人”那一行的上下沿（截图内 y）
LIST_TOP = 190         # 邮件列表从这里往下（上面是工具栏）
SKIP = ('收件人', '抄送', '密送', '分别发送')   # 收件人那一行里不是收件人的字


def recipients_ok(items: list[dict], allowed: str) -> tuple[bool, str]:
    """发送前的规矩：收件人那一行里只能有一个收件人，而且是 allowed。

    输入：items 是写信页（或回复页）的 read_page 结果；allowed 是白名单里的地址，例如 'abc@qq.com'。
    输出：(通过没有, 原因)。原因里不要写出地址。
    怎么判断：
      1. 收件人小块：kind 是 'TextControl'，y1 和 y2 都在 TO_ROW 里面，x1 > NAV_RIGHT，文字不在 SKIP 里。
      2. 小块正好一个；它的文字 == allowed，或者 == allowed 里 @ 前面那段（小块只显示昵称）。
      一个都没有、多于一个、文字对不上，都不通过。
    提示：@ 前面那段是 allowed.split('@')[0]。
    """
    # TODO
    recipients = []
    for it in items:
        if (it['kind'] == 'TextControl' and it['text'] not in SKIP
        and it['box'][0] > NAV_RIGHT and TO_ROW[0] <= it['box'][1] and it['box'][3] <= TO_ROW[1]):
            recipients.append(it)
    if not recipients:
        return (False,"没有收件人")
    if len(recipients) >1:
        return (False,"收件人多个")

    if recipients[0]['text'] in (allowed,allowed.split('@')[0]):
        return (True,"收件人正确")
    return (False, '收件人不是测试号')

def count_subject(items: list[dict], subject: str) -> int:
    """数「已发送」列表里有几封标题是 subject。

    输入：items 是「已发送」页面的 read_page 结果；subject 例如 '回复：测试邮件 1'。
    输出：条数。
    怎么判断：kind 是 'TextControl'，x1 > NAV_RIGHT，y1 > LIST_TOP，norm(文字) == norm(subject)。
    提示：和 check.py 的 count_msg 一个写法。
    """
    # TODO
    mails = []
    for it in items:
        if (it['kind'] == 'TextControl' and it['box'][0] > NAV_RIGHT 
        and it['box'][1] > LIST_TOP and norm(it['text']) == norm(subject)):
            mails.append(it)
    return len(mails)
           


def verify_replied(items: list[dict], subject: str, count_before: int) -> tuple[bool, str]:
    """核对回复有没有真的发出去：「已发送」里标题是 subject 的邮件，比发之前多。

    输入：items 是发送后「已发送」页面的 read_page 结果；count_before 是发送前数到的条数。
    输出：(通过没有, 原因)。
    为什么要和发之前比：同一封邮件以前回复过一次，「已发送」里本来就有一封同样的标题。
    """
    # TODO
    count = 0
    for it in items:
        if (it['kind'] == 'TextControl' and it['box'][0] > NAV_RIGHT 
        and it['box'][1] > LIST_TOP and norm(it['text']) == norm(subject)):
            count +=1
    if count > count_subject(items, subject):
        return (True,"f'发前 {count_before} 封，现在 {n} 封'")   
    return (False,"f'发前 {count_before} 封，现在 {n} 封'")


# ---------- 下面已经写好 ----------

def _t(text, x1, y1, x2, y2, kind='TextControl', value=''):
    return {'text': text, 'box': [x1, y1, x2, y2], 'score': 1.0, 'kind': kind, 'value': value}


_ADDR = 'tester01@qq.com'                    # 假地址，测的是逻辑
_FORM = [_t('发送', 292, 162, 320, 180), _t('收件人', 244, 208, 286, 226), _t('抄送', 1490, 209, 1516, 225),
         _t('密送', 1540, 209, 1566, 225), _t('分别发送', 1599, 209, 1651, 225),
         _t('主题', 335, 240, 1651, 284, 'EditControl', '回复：测试邮件 1'),
         _t('tester01', 302, 480, 373, 496)]  # 最后这个是原邮件引用里的发件人，在收件人行下面，不能算

RECIPIENT_CASES = [
    ('收件人是测试号（显示昵称）', _FORM + [_t('tester01', 336, 208, 419, 226)], True),
    ('收件人显示完整地址', _FORM + [_t(_ADDR, 336, 208, 470, 226)], True),
    ('收件人是别人', _FORM + [_t('tester02', 336, 208, 419, 226)], False),
    ('多了一个收件人', _FORM + [_t('tester01', 336, 208, 419, 226), _t('tester02', 430, 208, 510, 226)], False),
    ('收件人是空的', _FORM, False),
    ('昵称只是开头一样', _FORM + [_t('tester011', 336, 208, 425, 226)], False),
]

_NAV = [_t('收件箱', 52, 203, 94, 221), _t('已发送', 52, 305, 94, 323)]
_HEAD = [_t('已发送', 268, 161, 316, 181)]
_ROW1 = _t('回复：测试邮件 1', 494, 230, 600, 248)
_ROW1B = _t('回复：测试邮件 1', 494, 290, 600, 308)

SENT_CASES = [
    ('回复发出去了', _NAV + _HEAD + [_ROW1], 0, True),
    ('「已发送」里没有', _NAV + _HEAD + [_t('测试邮件 1', 494, 230, 560, 248)], 0, False),
    ('以前回复过，这次没发出去', _NAV + _HEAD + [_ROW1], 1, False),
    ('以前回复过，这次又发出去了', _NAV + _HEAD + [_ROW1, _ROW1B], 1, True),
    ('标题只是包含', _NAV + _HEAD + [_t('回复：测试邮件 10', 494, 230, 610, 248)], 0, False),
]


def run_offline() -> None:
    bad = 0
    for name, items, want in RECIPIENT_CASES:
        ok, why = recipients_ok(items, _ADDR)
        mark = '对' if ok == want else '错'
        bad += ok != want
        print(f'[{mark}] 收件人｜{name}：应该{"通过" if want else "不通过"}，结果{"通过" if ok else "不通过"}（{why}）')
    for name, items, before, want in SENT_CASES:
        ok, why = verify_replied(items, '回复：测试邮件 1', before)
        mark = '对' if ok == want else '错'
        bad += ok != want
        print(f'[{mark}] 已发送｜{name}：应该{"通过" if want else "不通过"}，结果{"通过" if ok else "不通过"}（{why}）')
    if bad:
        sys.exit(f'有 {bad} 种情况判错了，看上面标“错”的行')
    print('邮件核对逻辑全对')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    run_offline()
