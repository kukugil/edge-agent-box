"""工具层：agent 的“手和眼”。每个函数只做一件事，输入输出写在各自的说明里。

  看  screen.screenshot   按屏幕区域截图，返回 Shot（图 + 它在屏幕上的位置）
      window.capture_window 让窗口自己交一张画面，被挡住也拿得到
      screen.find_red_dots  找未读角标（红色小圆点）
      ocr.ocr             图里有哪些文字、各在哪
      ground.ground       一句话描述 → UI-TARS 给出点击坐标（内部补白边、换算坐标）
      ground.snap_to_text 用 OCR 把 UI-TARS 的点拉到文字中心（只在 60px 内）
  做  inputs.click / press / paste_text / scroll
  窗口 window.find_windows / activate / check_responsive

坐标约定：截图、OCR、定位返回的都是“截图内坐标”（左上角是 0,0）；
鼠标函数要的是“屏幕坐标”，用 shot.to_screen(x, y) 换算。
"""
import ctypes

# 让本进程按物理像素工作，截图和鼠标坐标才对得上（高 DPI 屏幕上不设会错位）。
# 这是整个进程的设置，只能设一次，所以放在包导入时。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except OSError:
    pass

from .screen import Shot, screenshot, pad_top, screen_changed, find_red_dots
from .ocr import ocr, find_text, center, norm
from .models import load_config, chat
from .ground import ground, snap_to_text
from .inputs import click, press, paste_text, scroll
from .window import WindowInfo, find_windows, window_rect, activate, check_responsive, capture_window
