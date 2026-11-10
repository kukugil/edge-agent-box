"""练习 5：在假截图里找带未读角标的会话行。

不碰微信。程序先画一张假截图：白底，上面有导航栏的总数字、会话列表里两个角标、头像里一块红色、聊天区里一张红图片。
你要从这些红点里只挑出会话角标，再算出每一行标题条在哪里。
讲解见 docs/学习笔记/03-会话队列.md 第三节。

你要做的：把下面 2 个函数里的 TODO 填上（删掉 raise NotImplementedError）。其他地方已经写好，不用改。

运行（在项目根目录）：python -m agent.practice.ex05_unread_rows
成功时最后一行打印：全部通过。
"""
import sys

from PIL import Image, ImageDraw

from agent.tools import find_red_dots

BADGE_X = (90, 125)    # 会话行角标中心的 x 范围（截图内坐标）
TITLE_X = (116, 200)   # 标题条的左右：左边要避开角标
TITLE_DY = (-4, 24)    # 标题条相对角标上沿的上下


def keep_list_badges(dots: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """从一堆红点里只留会话列表的角标。

    输入：find_red_dots 交回的框 (x1, y1, x2, y2)，从上到下
    输出：中心 x 落在 BADGE_X 范围里（两头都算）的那些框，顺序不变
    提示：中心 x = (x1 + x2) / 2。想一想：为什么用中心，不直接用 x1？
    """
    # TODO
    raise NotImplementedError


def title_box(badge: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """给一个角标，算出它那一行标题条的框 (x1, y1, x2, y2)。

    输出：左右用 TITLE_X；上沿 = 角标上沿 + TITLE_DY[0]，下沿 = 角标上沿 + TITLE_DY[1]
    想一想：下沿为什么不能再往下多给一点？（提示：再往下是什么？）
    """
    # TODO
    raise NotImplementedError


# ---------- 下面已经写好 ----------

RED = (250, 81, 81)    # 微信角标的颜色


def fake_shot() -> Image.Image:
    """画一张 960×800 的假微信截图，只有红色的东西。"""
    im = Image.new('RGB', (960, 800), 'white')
    d = ImageDraw.Draw(im)
    d.ellipse((35, 98, 50, 113), fill=RED)      # 导航栏总数字：不是会话角标
    d.ellipse((101, 87, 116, 102), fill=RED)    # 第 1 行角标（带数字的大红点）
    d.rectangle((64, 160, 84, 180), fill=RED)   # 第 2 行头像里一块红色：不是角标
    d.ellipse((105, 221, 113, 229), fill=RED)   # 第 3 行角标（免打扰的小红点）
    d.rectangle((500, 300, 560, 360), fill=RED) # 聊天区里一张红图片：不是角标
    return im


WANT_BADGES = [(101, 87, 117, 103), (105, 221, 114, 230)]
WANT_TITLES = [(116, 83, 200, 111), (116, 217, 200, 245)]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    dots = find_red_dots(fake_shot())
    print(f'假截图里一共 {len(dots)} 个红点')
    badges = keep_list_badges(dots)
    ok1 = badges == WANT_BADGES
    print(f'1. 会话角标：{"对" if ok1 else "错"}  ' + ('' if ok1 else f'应该 {WANT_BADGES}，实际 {badges}'))
    titles = [title_box(b) for b in WANT_BADGES]
    ok2 = titles == WANT_TITLES
    print(f'2. 标题条：{"对" if ok2 else "错"}  ' + ('' if ok2 else f'应该 {WANT_TITLES}，实际 {titles}'))
    print('全部通过' if ok1 and ok2 else '还没通过')


if __name__ == '__main__':
    main()
