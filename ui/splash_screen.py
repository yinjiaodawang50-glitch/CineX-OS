# ui/splash_screen.py
"""
CineX OS — 纯粹全息悬浮开屏动画 (彻底解决 Windows DWM 黑色方框 Bug)
"""

import os
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QApplication
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPixmap, QPainter, QColor


class SplashScreen(QWidget):
    def __init__(self, logo_path="assets/logo.png"):
        super().__init__()
        # 【关键修复 1】：用 Tool 替代 SplashScreen，强行开启 Windows 原生 Alpha 分层图层
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self.setFixedSize(520, 520)

        # 【关键修复 2】：初始透明度设为 0
        self.setWindowOpacity(0.0)

        # 屏幕正中央居中
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move((geo.width() - self.width()) // 2, (geo.height() - self.height()) // 2)

        # 构建 UI
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 降级路径检查
        if not os.path.exists(logo_path):
            logo_path = "assets/logo_pure_transparent.png"
        if not os.path.exists(logo_path):
            logo_path = "assets/logo.png"

        self.logo_lbl = QLabel()
        self.logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_lbl.setStyleSheet("background: transparent;")
        
        if os.path.exists(logo_path):
            pix = QPixmap(logo_path).scaled(
                380, 380,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.logo_lbl.setPixmap(pix)
        else:
            self.logo_lbl.setText("CineX OS")
            self.logo_lbl.setStyleSheet("font-size: 38px; font-weight: bold; color: #00C2D1; background: transparent;")
        
        lay.addWidget(self.logo_lbl)
        lay.addSpacing(16)

        # 悬浮蒂芙尼青光感文字
        self.status_lbl = QLabel("C i n e X   O S")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet("""
            color: #00C2D1;
            font-size: 16px;
            font-weight: 700;
            letter-spacing: 4px;
            background: transparent;
        """)
        lay.addWidget(self.status_lbl)

    # 【关键修复 3】：重写 paintEvent，强制清空背景为 100% 完全透明
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))

    # ── 原生窗口淡入 ──
    def fade_in(self, duration=800, callback=None):
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(duration)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if callback:
            self.anim.finished.connect(callback)
        self.anim.start()

    # ── 原生窗口淡出 ──
    def fade_out(self, duration=600, callback=None):
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(duration)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if callback:
            self.anim.finished.connect(callback)
        self.anim.start()