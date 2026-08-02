# ui/player_view.py
"""
CineX OS — 10-Foot 工业级内嵌播放器 (基于 python-mpv / libmpv 标准架构)
- 单进程 C-API 级渲染：彻底根除 Linux/X11 窗口层级遮挡、4秒黑屏与外部进程焦点抢占问题
- 纯粹单窗口模型：OSDOverlay 作为标准 Qt 子控件天然叠在视频上方
- 250ms 高高效内存状态轮询：直接读取 C 接口属性，无 Socket IPC 开销
- 10-Foot 电视遥控器直觉唤醒与 2D 焦点导航
"""

import os
import sys
import time
import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSlider, QApplication, QMessageBox
)
from PyQt6.QtCore import (
    Qt, QTimer, QSize, QEvent
)
from PyQt6.QtGui import QColor, QFont, QPainter

from core.config import ConfigManager
from core.theme import Theme

logger = logging.getLogger("EmbeddedPlayer")

# 尝试导入 python-mpv 绑定
try:
    import mpv
    HAS_PYTHON_MPV = True
except ImportError:
    HAS_PYTHON_MPV = False
    logger.warning("未检测到 python-mpv 模块，请执行 pip install python-mpv")


class OSDOverlay(QWidget):
    """标准的 Qt 子控件 OSD 蒙层，天然叠加于视频上方"""
    def __init__(self, parent_player):
        super().__init__(parent_player)
        self.player = parent_player
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("OSDOverlay")
        self.setStyleSheet("background: transparent;")


class EmbeddedPlayerWindow(QWidget):
    def __init__(self, parent_window, movie_data, route_idx=0, ep_idx=0, api_name=""):
        super().__init__()
        self.main = parent_window
        self.movie_data = movie_data
        self.vod_id = str(movie_data.get("vod_id", ""))
        self.title_text = movie_data.get("vod_name", "未知影片")
        self.api = movie_data.get("api", api_name)
        self.route_idx = route_idx
        self.ep_idx = ep_idx

        self.is_dark = True
        self.routes = movie_data.get("parsed_routes", [])
        self.current_ep_name = "正片"
        self.video_url = ""

        self.mpv_instance: Optional[mpv.MPV] = None
        self._is_paused = False
        self._is_user_seeking = False
        self._current_speed = 1.0
        self._speeds = [1.0, 1.25, 1.5, 2.0]
        self._duration_sec = 0.0
        self._pos_sec = 0.0
        self._mpv_started = False
        self._has_video_started = False

        # 解析剧集 URL
        self._extract_ep_info()

        # 窗口属性：标准无边框全屏，不搞任何多窗口黑科技
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("EmbeddedPlayer")

        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())

        self._build_ui()
        self._apply_theme()

        # 事件过滤器
        self.installEventFilter(self)
        QApplication.instance().installEventFilter(self)

        # 4 秒无操作自动隐藏 OSD
        self._osd_timer = QTimer(self)
        self._osd_timer.setSingleShot(True)
        self._osd_timer.setInterval(4000)
        self._osd_timer.timeout.connect(self._hide_osd)

        # 中央弹窗自动消失
        self._center_popup_timer = QTimer(self)
        self._center_popup_timer.setSingleShot(True)
        self._center_popup_timer.setInterval(1200)
        self._center_popup_timer.timeout.connect(self._hide_center_popup)

        # 250ms 内存状态轮询定时器 (比 Socket 通信快 100 倍)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(250)
        self._status_timer.timeout.connect(self._poll_mpv_status)

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        if not self._mpv_started:
            self._mpv_started = True
            QTimer.singleShot(50, self._start_mpv)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w, h = self.width(), self.height()
        # 将视频容器和 OSD 蒙层精准拉伸铺满
        if hasattr(self, "video_container"):
            self.video_container.setGeometry(0, 0, w, h)
        if hasattr(self, "osd_overlay"):
            self.osd_overlay.setGeometry(0, 0, w, h)
            self.osd_overlay.raise_()

    def _extract_ep_info(self):
        if self.routes and self.route_idx < len(self.routes):
            eps = self.routes[self.route_idx].get("episodes", [])
            if eps and self.ep_idx < len(eps):
                self.current_ep_name = eps[self.ep_idx].get("name", "正片")
                self.video_url = eps[self.ep_idx].get("url", "").strip()

    # ── UI 构建 ──────────────────────────────────────────────────
    def _build_ui(self):
        # 1. 底层：MPV 原生渲染容器
        self.video_container = QWidget(self)
        self.video_container.setObjectName("VideoContainer")
        self.video_container.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_container.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.video_container.setStyleSheet("background-color: #000000;")

        # 2. 上层：标准的 Qt OSD 子控件 (同一窗口下天然置顶)
        self.osd_overlay = OSDOverlay(self)

        osd_lay = QVBoxLayout(self.osd_overlay)
        osd_lay.setContentsMargins(40, 30, 40, 30)

        # 顶部 OSD 栏
        top_bar = QHBoxLayout()
        self.lbl_title = QLabel(f"《{self.title_text}》  {self.current_ep_name}")
        self.lbl_title.setObjectName("OSDTitle")

        top_bar.addWidget(self.lbl_title)
        top_bar.addStretch()

        self.btn_close = QPushButton(" 退出播放")
        self.btn_close.setObjectName("OSDCloseBtn")
        self.btn_close.setIcon(Theme.create_icon("x", "#EF4444", 16))
        self.btn_close.setIconSize(QSize(16, 16))
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_close.clicked.connect(self._close_player)
        top_bar.addWidget(self.btn_close)

        osd_lay.addLayout(top_bar)

        # 中央卡片
        osd_lay.addStretch()
        center_box = QHBoxLayout()
        center_box.addStretch()

        # 加载卡片
        self.loading_card = QFrame()
        self.loading_card.setObjectName("OSDLoadingCard")
        loading_lay = QHBoxLayout(self.loading_card)
        loading_lay.setContentsMargins(20, 10, 20, 10)
        loading_lay.setSpacing(10)

        self.lbl_loading_icon = QLabel()
        self.lbl_loading_icon.setPixmap(Theme.create_icon("zap", "#00C2D1", 20).pixmap(20, 20))
        self.lbl_loading_text = QLabel("正在解析并连接视频源，请稍候...")
        self.lbl_loading_text.setObjectName("OSDLoadingText")

        loading_lay.addWidget(self.lbl_loading_icon)
        loading_lay.addWidget(self.lbl_loading_text)

        # 中央提示卡片
        self.popup_card = QFrame()
        self.popup_card.setObjectName("CenterPopupCard")
        popup_lay = QHBoxLayout(self.popup_card)
        popup_lay.setContentsMargins(28, 14, 28, 14)
        popup_lay.setSpacing(12)

        self.lbl_popup_icon = QLabel()
        self.lbl_popup_text = QLabel()
        self.lbl_popup_text.setObjectName("CenterPopupText")

        popup_lay.addWidget(self.lbl_popup_icon)
        popup_lay.addWidget(self.lbl_popup_text)
        self.popup_card.hide()

        center_vbox = QVBoxLayout()
        center_vbox.addWidget(self.loading_card, alignment=Qt.AlignmentFlag.AlignCenter)
        center_vbox.addWidget(self.popup_card, alignment=Qt.AlignmentFlag.AlignCenter)

        center_box.addLayout(center_vbox)
        center_box.addStretch()
        osd_lay.addLayout(center_box)
        osd_lay.addStretch()

        # 底部控制栏
        bottom_bar = QVBoxLayout()
        bottom_bar.setSpacing(10)

        time_row = QHBoxLayout()
        self.lbl_time_pos = QLabel("00:00:00 / 00:00:00")
        self.lbl_time_pos.setObjectName("OSDTimeLabel")

        self.lbl_hint = QLabel("按「方向键」导航/调整进度  |  按「OK」确认")
        self.lbl_hint.setObjectName("OSDHintLabel")

        time_row.addWidget(self.lbl_time_pos)
        time_row.addStretch()
        time_row.addWidget(self.lbl_hint)
        bottom_bar.addLayout(time_row)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("OSDSeekSlider")
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setValue(0)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.seek_slider.sliderMoved.connect(self._on_slider_moved)
        self.seek_slider.sliderReleased.connect(self._on_slider_released)
        bottom_bar.addWidget(self.seek_slider)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(16)

        self.btn_play_pause = QPushButton(" 暂停")
        self.btn_play_pause.setObjectName("OSDCtrlBtn")
        self.btn_play_pause.setIcon(Theme.create_icon("pause", "#E8EEF2", 18))
        self.btn_play_pause.setIconSize(QSize(18, 18))
        self.btn_play_pause.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_play_pause.clicked.connect(self._toggle_pause)

        self.btn_speed = QPushButton(f" {self._current_speed}x 倍速")
        self.btn_speed.setObjectName("OSDCtrlBtn")
        self.btn_speed.setIcon(Theme.create_icon("zap", "#00C2D1", 18))
        self.btn_speed.setIconSize(QSize(18, 18))
        self.btn_speed.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_speed.clicked.connect(self._cycle_speed)

        self.btn_next = QPushButton(" 下一集")
        self.btn_next.setObjectName("OSDCtrlBtn")
        self.btn_next.setIcon(Theme.create_icon("skip_forward", "#E8EEF2", 18))
        self.btn_next.setIconSize(QSize(18, 18))
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_next.clicked.connect(self._play_next_episode)

        ctrl_row.addWidget(self.btn_play_pause)
        ctrl_row.addWidget(self.btn_speed)
        ctrl_row.addWidget(self.btn_next)
        ctrl_row.addStretch()

        bottom_bar.addLayout(ctrl_row)
        osd_lay.addLayout(bottom_bar)

    # ── libmpv C-API 启动 ─────────────────────────────────────────
    def _start_mpv(self):
        if not HAS_PYTHON_MPV:
            QMessageBox.critical(
                self, "缺少依赖",
                "缺少 python-mpv 模块，请在终端执行：\n\n  pip install python-mpv\n"
            )
            self._close_player()
            return

        if not self.video_url:
            logger.error("无有效播放地址，无法启动 MPV")
            self._close_player()
            return

        ud = ConfigManager.load_user_data()
        s = ud.get("settings", {})
        hw = s.get("hardware_accel", "自动")
        skip_start = s.get("skip_start", 0)

        mpv_log_map = {
            "DEBUG": "debug",
            "INFO": "info",
            "WARNING": "warn",
            "ERROR": "error",
            "CRITICAL": "fatal"
        }

        sys_log_level = s.get("log_level", "INFO").upper()
        mpv_loglevel = mpv_log_map.get(sys_log_level, "warn")

        wid = int(self.video_container.winId())

        # 设置解码参数
        hwdec_opt = "auto-safe"
        if hw == "强制硬解":
            hwdec_opt = "auto"
        elif hw == "软解":
            hwdec_opt = "no"

        vo_opt = "gpu,x11" if sys.platform != "win32" else "gpu,direct3d11,gdi"

        try:
            logger.info(f"正在初始化 libmpv (wid={wid}, vo={vo_opt}, hwdec={hwdec_opt})...")
            
            # 使用 python-mpv C 动态库实例
            self.mpv_instance = mpv.MPV(
                wid=str(wid),
                vo=vo_opt,
                hwdec=hwdec_opt,
                input_default_bindings=False,
                input_vo_keyboard=False,
                input_cursor=False,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                loglevel=mpv_loglevel
            )

            # 跳过片头或续播处理
            prog = ud.get("progress", {}).get(self.vod_id, {})
            pos_sec = prog.get("position_sec", 0)
            
            start_pos = 0
            if skip_start > 0:
                start_pos = skip_start
            elif pos_sec > 10:
                start_pos = int(pos_sec)

            if start_pos > 0:
                self.mpv_instance.start = str(start_pos)

            # 播放视频
            self.mpv_instance.play(self.video_url)

            # 启动内存轮询
            self._status_timer.start()

            self.loading_card.show()
            self._show_osd()
            self.seek_slider.setFocus()

        except Exception as e:
            logger.error("初始化 libmpv 失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "播放错误", f"无法初始化 MPV 渲染引擎：\n{e}")
            self._close_player()

    # ── 250ms 高效内存状态轮询 (0 Socket 延迟) ──
    def _poll_mpv_status(self):
        if not self.mpv_instance:
            return

        try:
            pos = self.mpv_instance.time_pos or 0.0
            dur = self.mpv_instance.duration or 0.0
            is_buf = getattr(self.mpv_instance, 'paused_for_cache', False)

            self._pos_sec = pos
            self._duration_sec = dur

            # 首帧捕捉处理
            if (dur > 0 or pos > 0) and not self._has_video_started:
                self._has_video_started = True
                self.loading_card.hide()
                self._osd_timer.start()

            if is_buf:
                self.lbl_loading_text.setText("正在缓冲视频数据，请稍候...")
                if not self.loading_card.isVisible():
                    self.loading_card.show()
            elif self._has_video_started and self.loading_card.isVisible():
                self.loading_card.hide()

            # 刷新 UI 进度条
            if self._duration_sec > 0:
                self.seek_slider.setRange(0, int(self._duration_sec))
                if not self._is_user_seeking:
                    self.seek_slider.setValue(int(self._pos_sec))

                pos_str = time.strftime("%H:%M:%S", time.gmtime(self._pos_sec))
                dur_str = time.strftime("%H:%M:%S", time.gmtime(self._duration_sec))
                self.lbl_time_pos.setText(f"{pos_str} / {dur_str}")

        except Exception:
            pass

    # ── 滑块拖动与快进退 ──
    def _on_slider_moved(self, val):
        self._is_user_seeking = True
        self._show_osd()
        dur_str = time.strftime("%H:%M:%S", time.gmtime(self._duration_sec))
        pos_str = time.strftime("%H:%M:%S", time.gmtime(val))
        self.lbl_time_pos.setText(f"{pos_str} / {dur_str}")

    def _on_slider_released(self):
        val = self.seek_slider.value()
        self._seek_absolute(val)
        self._is_user_seeking = False

    def _seek_relative(self, delta_sec):
        current_val = self.seek_slider.value()
        max_val = self.seek_slider.maximum()
        target_sec = max(0, min(max_val, current_val + delta_sec))
        self.seek_slider.setValue(target_sec)
        self._seek_absolute(target_sec)

        sign = "+" if delta_sec > 0 else ""
        icon = "forward" if delta_sec > 0 else "rewind"
        self._show_center_popup(icon, f"{sign}{delta_sec}s")

    def _seek_absolute(self, target_sec):
        if self.mpv_instance:
            try:
                self.mpv_instance.seek(target_sec, reference="absolute")
            except Exception as e:
                logger.error("Seek 失败: %s", e)

    # ── OSD 显隐控制 ──
    def _show_osd(self):
        self.osd_overlay.show()
        self.osd_overlay.raise_()
        if not self.focusWidget() or not self.osd_overlay.isAncestorOf(self.focusWidget()):
            self.seek_slider.setFocus()
        self._osd_timer.start()

    def _hide_osd(self):
        if not self._has_video_started or self.loading_card.isVisible():
            return
        self.osd_overlay.hide()

    def _show_center_popup(self, icon_name, text):
        self.lbl_popup_icon.setPixmap(
            Theme.create_icon(icon_name, "#33D6E0", 28).pixmap(28, 28)
        )
        self.lbl_popup_text.setText(text)
        self.popup_card.show()
        self.popup_card.raise_()
        self._center_popup_timer.start()

    def _hide_center_popup(self):
        self.popup_card.hide()

    # ── 播放动作 ──
    def _toggle_pause(self):
        if not self.mpv_instance:
            return
        self._show_osd()
        self._is_paused = not self._is_paused
        self.mpv_instance.pause = self._is_paused
        if self._is_paused:
            self.btn_play_pause.setIcon(Theme.create_icon("play", "#E8EEF2", 18))
            self.btn_play_pause.setText(" 继续")
            self._show_center_popup("pause", "暂停")
        else:
            self.btn_play_pause.setIcon(Theme.create_icon("pause", "#E8EEF2", 18))
            self.btn_play_pause.setText(" 暂停")
            self._show_center_popup("play", "播放")

    def _cycle_speed(self):
        if not self.mpv_instance:
            return
        self._show_osd()
        idx = self._speeds.index(self._current_speed)
        next_idx = (idx + 1) % len(self._speeds)
        self._current_speed = self._speeds[next_idx]
        self.mpv_instance.speed = self._current_speed
        self.btn_speed.setText(f" {self._current_speed}x 倍速")
        self._show_center_popup("zap", f"{self._current_speed}x 倍速")

    def _play_next_episode(self):
        eps = self.routes[self.route_idx].get("episodes", []) if self.routes else []
        if self.ep_idx + 1 < len(eps):
            self._save_progress()
            self._destroy_mpv()
            self.ep_idx += 1
            self._has_video_started = False
            self._extract_ep_info()
            self.lbl_title.setText(f"《{self.title_text}》  {self.current_ep_name}")
            self.seek_slider.setValue(0)
            self._start_mpv()

    def _save_progress(self):
        if self._pos_sec > 5:
            ud = ConfigManager.load_user_data()
            pct = int((self._pos_sec / self._duration_sec) * 100) if self._duration_sec > 0 else 50
            ud["progress"][self.vod_id] = {
                "ep_name": self.current_ep_name,
                "route": self.route_idx,
                "position_sec": self._pos_sec,
                "duration_sec": self._duration_sec,
                "progress_percent": pct
            }
            ConfigManager.save_user_data(ud)
            logger.info("已保存播放进度: %s -> %s (已看 %d秒)", self.title_text, self.current_ep_name, self._pos_sec)

    def _destroy_mpv(self):
        if self._status_timer and self._status_timer.isActive():
            self._status_timer.stop()

        if self.mpv_instance:
            try:
                self.mpv_instance.terminate()
            except Exception:
                pass
            self.mpv_instance = None

    def _close_player(self):
        self._save_progress()
        self._destroy_mpv()
        try:
            QApplication.instance().removeEventFilter(self)
        except Exception:
            pass
        self.close()
        if self.main:
            if hasattr(self.main, "_current_player"):
                self.main._current_player = None
            self.main.show()
            self.main.raise_()
            self.main.activateWindow()

    def closeEvent(self, event):
        self._save_progress()
        self._destroy_mpv()
        try:
            QApplication.instance().removeEventFilter(self)
        except Exception:
            pass
        super().closeEvent(event)

    # ── 遥控器与 2D 焦点导航 ──
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()

            # 1. 随时响应 Exit/Esc/Backspace 退出播放
            if key in (Qt.Key.Key_Escape, Qt.Key.Key_Back, Qt.Key.Key_Backspace):
                self._close_player()
                return True

            is_osd_visible = self.osd_overlay.isVisible()

            # 2. 直觉控制：当 OSD 隐藏时，按方向键/OK 仅唤醒 OSD，防误触
            if not is_osd_visible:
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
                           Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    self._show_osd()
                    self.seek_slider.setFocus()
                    return True

            # 3. OSD 已唤醒状态：重置倒计时并响应物理导航
            self._show_osd()
            fw = QApplication.focusWidget()

            ctrl_buttons = [self.btn_play_pause, self.btn_speed, self.btn_next, self.btn_close]

            # A. 焦点在进度条滑块上
            if fw is self.seek_slider or fw not in ctrl_buttons:
                if key == Qt.Key.Key_Left:
                    self._seek_relative(-10)
                    return True
                elif key == Qt.Key.Key_Right:
                    self._seek_relative(10)
                    return True
                elif key == Qt.Key.Key_Down:
                    self.btn_play_pause.setFocus()
                    return True
                elif key == Qt.Key.Key_Up:
                    self.btn_close.setFocus()
                    return True
                elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    self._toggle_pause()
                    return True

            # B. 焦点在控制按钮上
            if fw in ctrl_buttons:
                if key == Qt.Key.Key_Up:
                    self.seek_slider.setFocus()
                    return True
                elif key == Qt.Key.Key_Left:
                    idx = ctrl_buttons.index(fw)
                    prev_idx = (idx - 1) % len(ctrl_buttons)
                    ctrl_buttons[prev_idx].setFocus()
                    return True
                elif key == Qt.Key.Key_Right:
                    idx = ctrl_buttons.index(fw)
                    next_idx = (idx + 1) % len(ctrl_buttons)
                    ctrl_buttons[next_idx].setFocus()
                    return True
                elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    fw.click()
                    return True

        return super().eventFilter(obj, event)

    # ── QSS 样式表 ──
    def _apply_theme(self):
        accent = "#00C2D1"
        accent_hover = "#33D6E0"
        text = "#E8EEF2"
        text2 = "#8CA0B0"

        self.setStyleSheet(f"""
            QWidget#EmbeddedPlayer {{
                background-color: #000000;
            }}

            QWidget#OSDOverlay {{
                background: transparent;
            }}

            QLabel#OSDTitle {{
                color: {text};
                font-size: 22px;
                font-weight: 800;
                background: transparent;
            }}

            QLabel#OSDTimeLabel {{
                color: {accent};
                font-size: 15px;
                font-weight: 700;
                background: transparent;
            }}

            QLabel#OSDHintLabel {{
                color: {text2};
                font-size: 13px;
                font-weight: 500;
                background: transparent;
            }}

            QFrame#OSDLoadingCard {{
                background-color: rgba(10, 15, 20, 0.88);
                border: 1.5px solid {accent};
                border-radius: 12px;
            }}

            QLabel#OSDLoadingText {{
                color: {accent};
                font-size: 15px;
                font-weight: 700;
                background: transparent;
            }}

            QFrame#CenterPopupCard {{
                background-color: rgba(10, 15, 20, 0.88);
                border: 2px solid {accent};
                border-radius: 16px;
            }}

            QLabel#CenterPopupText {{
                color: {accent_hover};
                font-size: 24px;
                font-weight: 800;
                background: transparent;
            }}

            QSlider#OSDSeekSlider::groove:horizontal {{
                height: 6px;
                background: rgba(255, 255, 255, 0.2);
                border-radius: 3px;
            }}
            QSlider#OSDSeekSlider::sub-page:horizontal {{
                background: {accent};
                border-radius: 3px;
            }}
            QSlider#OSDSeekSlider::handle:horizontal {{
                background: {accent_hover};
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }}
            QSlider#OSDSeekSlider::handle:horizontal:hover, QSlider#OSDSeekSlider::handle:horizontal:focus {{
                background: #FFFFFF;
                border: 2px solid {accent};
                width: 20px;
                height: 20px;
                margin: -7px 0;
                border-radius: 10px;
            }}
            QSlider#OSDSeekSlider:focus {{
                outline: none;
            }}

            QPushButton#OSDCtrlBtn {{
                background-color: rgba(255, 255, 255, 0.1);
                color: {text};
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 10px;
                padding: 8px 18px;
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton#OSDCtrlBtn:hover {{
                background-color: {accent};
                color: #0A0F14;
            }}
            QPushButton#OSDCtrlBtn:focus {{
                background-color: {accent};
                color: #0A0F14;
                border: 2px solid #FFFFFF;
                font-weight: 800;
                outline: none;
            }}

            QPushButton#OSDCloseBtn {{
                background-color: rgba(239, 68, 68, 0.2);
                color: #EF4444;
                border: 1px solid rgba(239, 68, 68, 0.4);
                border-radius: 10px;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#OSDCloseBtn:hover, QPushButton#OSDCloseBtn:focus {{
                background-color: #EF4444;
                color: #FFFFFF;
                border: 2px solid #FFFFFF;
                outline: none;
            }}
        """)