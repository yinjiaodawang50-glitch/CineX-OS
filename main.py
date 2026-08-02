#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CineX OS - 入口文件 (Linux 跨平台绝对路径与开屏动画兼容版)
"""

import sys
import os
import logging
import datetime

# 获取项目根目录绝对路径，添加到 Python 模块搜索路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer, qInstallMessageHandler, QtMsgType
from PyQt6.QtGui import QFont, QFontDatabase, QIcon

from ui.main_window import MainWindow
from ui.splash_screen import SplashScreen

# ── 日志初始化 ──
LOG_DIR = os.path.join(BASE_DIR, "logs")
if not os.path.exists(LOG_DIR):
    try:
        os.makedirs(LOG_DIR)
    except Exception:
        pass

LOG_FILE = os.path.join(LOG_DIR, f"cinex_{datetime.datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)


def qt_message_handler(msg_type, context, msg):
    if any(k in msg for k in ("QSslSocket", "QIODevice", "SSL", "Network")):
        return
    if msg_type == QtMsgType.QtFatalMsg:
        logging.fatal(f"Qt Fatal: {msg}")
    else:
        logging.debug(f"Qt: {msg}")


qInstallMessageHandler(qt_message_handler)


def setup_font():
    """全局字体加载（优先载入项目 fonts/ 目录下的内置字体，防 Linux 乱码）"""
    fonts_dir = os.path.join(BASE_DIR, "fonts")
    possible_paths = [
        os.path.join(fonts_dir, "NotoSansSC-Regular.ttf"),
        os.path.join(fonts_dir, "NotoSansSC-Medium.ttf"),
        os.path.join(fonts_dir, "NotoSansSC-Bold.ttf"),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            font_id = QFontDatabase.addApplicationFont(path)
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    font = QFont(families[0], 10)
                    font.setStyleHint(QFont.StyleHint.SansSerif)
                    QApplication.setFont(font)
                    logging.info(f"已成功加载本地内置字体: {path} -> {families[0]}")
                    return

    preferred_family = "Noto Sans SC"
    if QFont(preferred_family).exactMatch():
        font = QFont(preferred_family, 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        QApplication.setFont(font)
        logging.info(f"全局字体设置为系统字体: {preferred_family}")
        return

    logging.warning("未找到 Noto Sans SC 字体，使用系统默认 sans-serif 字体（中文可能显示为豆腐块□□□）")
    font = QFont("sans-serif", 10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    QApplication.setFont(font)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 全局字体设置
    setup_font()

    # 设置软件图标
    logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
    if os.path.exists(logo_path):
        app.setWindowIcon(QIcon(logo_path))

    # 1. 启动开屏动画窗口并置顶显示
    splash = SplashScreen(logo_path)
    splash.show()
    splash.raise_()
    splash.activateWindow()

    # 2. 初始化主窗口
    is_kiosk = "--windowed" not in sys.argv
    win = MainWindow(is_kiosk=is_kiosk)

    # 3. 动画生命周期控制：展示 2.0 秒后顺畅切入主界面
    def show_main_window():
        splash.close()
        win.show()
        win.raise_()
        win.activateWindow()

    QTimer.singleShot(2000, show_main_window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
