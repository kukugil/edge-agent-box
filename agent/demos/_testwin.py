"""给 demo_window.py 用的“可控测试窗口”：一列会在鼠标悬停时变色的行，像微信的会话列表。

从标准输入接收命令，用来人为制造各种窗口状态：
  hide    隐藏窗口（模拟微信藏到托盘）
  show    显示窗口
  min     最小化
  hang    主线程卡住 4 秒（模拟程序卡死）
  freeze  取消悬停效果，但程序照常运行（模拟“看着正常、其实不响应输入”）
  quit    退出
"""
import queue
import sys
import threading
import time
import tkinter as tk

root = tk.Tk()
root.title('agent-test-window')
root.geometry('360x400+200+200')
rows = []
for i in range(6):
    lb = tk.Label(root, text=f'第 {i + 1} 行', bg='white', anchor='w', padx=12, font=('Microsoft YaHei', 12))
    lb.pack(fill='x', ipady=14)
    lb.bind('<Enter>', lambda e, w=lb: w.config(bg='#dddddd'))
    lb.bind('<Leave>', lambda e, w=lb: w.config(bg='white'))
    rows.append(lb)

cmds = queue.Queue()
threading.Thread(target=lambda: [cmds.put(line.strip()) for line in sys.stdin], daemon=True).start()


def poll():
    while not cmds.empty():
        c = cmds.get()
        if c == 'hide':
            root.withdraw()
        elif c == 'show':
            root.deiconify()
        elif c == 'min':
            root.iconify()
        elif c == 'hang':
            time.sleep(4)  # 不到 5 秒：超过 5 秒 Windows 会换上“未响应”的幽灵窗口，干扰后续测试
        elif c == 'freeze':
            for lb in rows:
                lb.unbind('<Enter>')
                lb.unbind('<Leave>')
                lb.config(bg='white')
        elif c == 'quit':
            root.destroy()
            return
    root.after(100, poll)


root.after(100, poll)
root.mainloop()
