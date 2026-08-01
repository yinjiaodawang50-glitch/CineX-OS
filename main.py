#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CineX OS - 入口文件 (含平滑淡入淡出开屏动画)
用法: python3 main.py
"""

import sys
import os
import logging
import datetime

# ── 跨平台 BASE_DIR 绝对路径锚定（解决 Linux systemd / 自动启动 cwd 错乱） ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 将项目根目录加入 sys.path
sys.path.insert(0, BASE_DIR)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer, qInstallMessageHandler, QtMsgType
from PyQt6.QtGui import QFont, QFontDatabase, QIcon

from ui.main_window import MainWindow
from ui.splash_screen import SplashScreen

# ── 日志初始化 ──────────────────────────────────────────────────
LOG_DIR = os.path.join(BASE_DIR, "logs")
if not os.path.exists(LOG_DIR):
    try: os.makedirs(LOG_DIR)
    except: pass
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
    """全局字体统一设置 - 优先加载项目内置中文字体，防 Linux 无中文字体库出现豆腐块□□□"""
    FONTS_DIR = os.path.join(BASE_DIR, "fonts")

    possible_paths = [
        os.path.join(FONTS_DIR, "NotoSansSC-Regular.ttf"),
        os.path.join(FONTS_DIR, "NotoSansSC-Medium.ttf"),
        os.path.join(FONTS_DIR, "NotoSansSC-Bold.ttf"),
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
                    logging.info(f"已加载内置字体文件: {path} -> {families[0]}")
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

    # 1. 设置软件全局 Taskbar / 窗口图标
    logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
    if os.path.exists(logo_path):
        app.setWindowIcon(QIcon(logo_path))

    # 2. 启动开屏动画窗口
    splash = SplashScreen(logo_path)
    splash.show()

    # 3. 后台同步初始化主窗口 (此时用户看到的是精致的 Logo 淡入)
    win = MainWindow(is_kiosk=False)

    # 4. 动画生命周期控制：淡入 (800ms) ➔ 停留 (1200ms) ➔ 淡出 (600ms) ➔ 显示主界面
    def on_fade_out_finished():
        splash.close()
        win.show()
        win.raise_()
        win.activateWindow()

    def start_fade_out():
        splash.fade_out(duration=600, callback=on_fade_out_finished)

    # 触发淡入，淡入完成后停留 1.2 秒然后淡出
    splash.fade_in(duration=800, callback=lambda: QTimer.singleShot(1200, start_fade_out))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()