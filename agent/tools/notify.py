"""通知：转人工、卡住、结果不确定时，在 Windows 右下角弹一条提醒给操作的人（D15）。

为什么用托盘气泡（NotifyIcon）：系统自带，不用装包；弹出时不抢前台，不会把正在操作微信的键鼠带偏。
不往微信里发东西：卡住的时候再去操作微信，可能越弄越乱。
内容只写"白名单第几个 + 原因"，不写人名和聊天内容。

运行（在项目根目录）：
  python -m agent.tools.notify      弹一条测试通知
"""
import os
import subprocess
import sys

# 文字走环境变量传给 PowerShell，不拼进命令里：原因里有引号、换行也不会把命令弄坏
_PS = r'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(10000, $env:EDGE_NOTIFY_TITLE, $env:EDGE_NOTIFY_TEXT, 'Info')
Start-Sleep -Seconds 10
$n.Dispose()
'''


def notify(title: str, text: str) -> bool:
    """输入标题和正文，弹一条右下角通知，不等它消失就返回。输出：通知进程启没启动起来。"""
    env = {**os.environ, 'EDGE_NOTIFY_TITLE': title[:60], 'EDGE_NOTIFY_TEXT': text[:250]}
    try:
        subprocess.Popen(['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', _PS],
                         env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return True
    except OSError:
        return False


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print('启动了' if notify('端侧助手：需要你处理', '白名单#2 测试通知：这是一条演示，不用处理') else '没启动起来')
