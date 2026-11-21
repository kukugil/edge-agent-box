"""端侧盒子托盘入口。"""
import argparse
from importlib.util import find_spec
import os
import sys
from pathlib import Path

if find_spec('PySide6') is None:
    local_lib = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'edge-agent' / 'desktop-deps'
    if (local_lib / 'PySide6').is_dir():
        sys.path.insert(0, str(local_lib))

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from agent.core import trigger
from agent.ui.main_window import MainWindow


def main():
    """解析启动参数，取得单实例锁并启动托盘主窗口。"""
    parser = argparse.ArgumentParser(description='端侧盒子 Windows 托盘应用')
    parser.add_argument('--tray', action='store_true', help='启动时只显示托盘图标')
    parser.add_argument('--file-transfer-only', action='store_true', help='只显示文件传输助手的模拟事项')
    parser.add_argument('--reply-db', type=Path, help='显示另一个测试回复账本中的事项')
    parser.add_argument('--listen', action='store_true', help='打开托盘后立刻开始自动回复')
    args = parser.parse_args()
    if args.listen and args.reply_db:
        parser.error('--listen 写的是默认账本，不能和 --reply-db 一起用，否则托盘看的和监听写的不是同一本')
    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        parser.exit(1, 'Windows 托盘不可用。\n')
    local = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'edge-agent'
    local.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(local / 'desktop.lock'))
    if not lock.tryLock(0):
        parser.exit(1, '端侧盒子窗口已在运行，请从托盘打开。\n')
    contacts = [(name, False) for name in trigger.WHITELIST] if not args.file_transfer_only else []
    if trigger.SIM_NAME in trigger.WHITELIST:
        contacts.append((trigger.SIM_NAME, True))
    window = MainWindow(contacts, app, args.reply_db)
    if not args.tray:
        window.open_window()
    if args.listen:
        window.toggle_listen()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
