# ui/player_view.py
"""
CineX OS — 10-Foot 内嵌式 MPV 播放器与多面板响应式 OSD 菜单
- 彻底解决 Linux/X11 遮挡导致的 4 秒黑屏（顶栏与底栏独立面板排版，观影区域 0 遮挡）
- 响应式 DPI 放缩（self.s()）：自动完美适配 4K、1080p 及 720p 等任意尺寸电视大屏
- 10-Foot 电视遥控器防误触唤醒与 2D 空间物理焦点导航
- 异步 IPC 状态轮询与断点续播进度回写
"""

import os
import sys
import json
import time
import socket
import logging
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSlider, QApplication
)
from PyQt6.QtCore import (
    Qt, QTimer, QSize, QEvent, QThread, pyqtSignal
)
from PyQt6.QtGui import QColor, QFont, QPainter

from core.config import ConfigManager
from core.theme import Theme

logger = logging.getLogger("EmbeddedPlayer")


class MPVStatusWorker(QThread):
    """后台异步 IPC 线程：负责与 MPV 管道通信，彻底解除主线程卡死"""
    status_updated = pyqtSignal(float, float, bool, bool)  # pos, dur, is_buf, has_video

    def __init__(self, ipc_path, parent=None):
        super().__init__(parent)
        self.ipc_path = ipc_path
        self._running = True
        self._executor = ThreadPoolExecutor(max_workers=2)

    def stop(self):
        self._running = False
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass

    def send_cmd_async(self, command_list):
        if not self._running or not self.ipc_path:
            return
        self._executor.submit(self._exec_cmd, command_list)

    def _exec_cmd(self, command_list) -> dict:
        if not self.ipc_path:
            return {}
        msg = json.dumps({"command": command_list}) + "\n"
        try:
            if sys.platform == "win32":
                with open(self.ipc_path, "r+b", buffering=0) as f:
                    f.write(msg.encode("utf-8"))
                    res = f.readline().decode("utf-8", errors="ignore")
                    return json.loads(res) if res else {}
            else:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(0.2)
                s.connect(self.ipc_path)
                s.sendall(msg.encode("utf-8"))
                res = s.recv(1024).decode("utf-8", errors="ignore")
                s.close()
                return json.loads(res) if res else {}
        except Exception:
            return {}

    def run(self):
        while self._running:
            time.sleep(0.3)
            if not self._running:
                break
            try:
                res_time = self._exec_cmd(["get_property", "time-pos"])
                res_dur = self._exec_cmd(["get_property", "duration"])
                res_buf = self._exec_cmd(["get_property", "paused-for-cache"])
                res_w = self._exec_cmd(["get_property", "width"])

                pos = float(res_time.get("data", 0.0)) if (res_time and res_time.get("error") == "success" and res_time.get("data") is not None) else 0.0
                dur = float(res_dur.get("data", 0.0)) if (res_dur and res_dur.get("error") == "success" and res_dur.get("data") is not None) else 0.0
                is_buf = bool(res_buf.get("data")) if (res_buf and res_buf.get("error") == "success") else False
                vw = int(res_w.get("data", 0)) if (res_w and res_w.get("error") == "success" and res_w.get("data") is not None) else 0

                has_video = (vw > 0 or dur > 0 or pos > 0)

                if self._running:
                    self.status_updated.emit(pos, dur, is_buf, has_video)
            except Exception:
                pass


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

        self._process = None
        self._worker = None
        self._is_paused = False
        self._is_user_seeking = False
        self._current_speed = 1.0
        self._speeds = [1.0, 1.25, 1.5, 2.0]
        self._duration_sec = 0.0
        self._pos_sec = 0.0
        self._mpv_started = False
        self._has_video_started = False

        # 解析选中的线路与集数 URL
        self._extract_ep_info()

        # 窗口属性（全屏无边框置顶）
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("EmbeddedPlayer")

        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())

        self._build_ui()
        self._apply_theme()

        # 安装事件过滤器
        self.installEventFilter(self)
        QApplication.instance().installEventFilter(self)

        # 4 秒无操作自动隐藏 OSD 菜单
        self._osd_timer = QTimer(self)
        self._osd_timer.setSingleShot(True)
        self._osd_timer.setInterval(4000)
        self._osd_timer.timeout.connect(self._hide_osd)

        # 中央弹窗图标 1.2 秒自动消失
        self._center_popup_timer = QTimer(self)
        self._center_popup_timer.setSingleShot(True)
        self._center_popup_timer.setInterval(1200)
        self._center_popup_timer.timeout.connect(self._hide_center_popup)

    def s(self, px_val: int) -> int:
        """根据当前电视物理分辨率（如 4K/1080p/720p），动态计算缩放后的像素值"""
        if hasattr(self, "main") and self.main and hasattr(self.main, "s"):
            return self.main.s(px_val)
        screen = QApplication.primaryScreen()
        h = screen.geometry().height() if screen else 1080
        return max(1, int(px_val * (h / 1080.0)))

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
        
        # 1. 视频容器铺满
        self.video_container.setGeometry(0, 0, w, h)
        
        # 2. 顶部栏贴顶 (根据 DPI 动态自适应高度)
        top_h = max(self.s(70), self.top_bar_frame.sizeHint().height())
        self.top_bar_frame.setGeometry(0, 0, w, top_h)
        
        # 3. 底部栏贴底 (根据 DPI 动态自适应高度)
        bottom_h = max(self.s(130), self.bottom_bar_frame.sizeHint().height())
        self.bottom_bar_frame.setGeometry(0, h - bottom_h, w, bottom_h)
        
        # 4. 中央卡片动态居中
        cw, ch = self.s(420), self.s(80)
        self.center_card_frame.setGeometry((w - cw) // 2, (h - ch) // 2, cw, ch)

        # 保障原生 Z 轴层级置顶
        self.top_bar_frame.raise_()
        self.bottom_bar_frame.raise_()
        self.center_card_frame.raise_()

    def _extract_ep_info(self):
        if self.routes and self.route_idx < len(self.routes):
            eps = self.routes[self.route_idx].get("episodes", [])
            if eps and self.ep_idx < len(eps):
                self.current_ep_name = eps[self.ep_idx].get("name", "正片")
                self.video_url = eps[self.ep_idx].get("url", "").strip()

    # ── UI 构建 ──────────────────────────────────────────────────
    def _build_ui(self):
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)

        # 1. 底层：视频渲染容器 (全屏)
        self.video_container = QWidget(self)
        self.video_container.setObjectName("VideoContainer")
        self.video_container.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_container.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        main_lay.addWidget(self.video_container)

        # 2. 上层：顶部控制面板 (贴顶，只占顶部区域)
        self.top_bar_frame = QFrame(self)
        self.top_bar_frame.setObjectName("OSDTopBar")
        top_lay = QHBoxLayout(self.top_bar_frame)
        top_lay.setContentsMargins(self.s(40), self.s(12), self.s(40), self.s(12))

        self.lbl_title = QLabel(f"《{self.title_text}》  {self.current_ep_name}")
        self.lbl_title.setObjectName("OSDTitle")
        top_lay.addWidget(self.lbl_title)
        top_lay.addStretch()

        btn_icon_sz = QSize(self.s(16), self.s(16))
        self.btn_close = QPushButton(" 退出播放")
        self.btn_close.setObjectName("OSDCloseBtn")
        self.btn_close.setIcon(Theme.create_icon("x", "#EF4444", 16))
        self.btn_close.setIconSize(btn_icon_sz)
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_close.clicked.connect(self._close_player)
        top_lay.addWidget(self.btn_close)

        # 3. 上层：底部控制面板 (贴底，只占底部区域)
        self.bottom_bar_frame = QFrame(self)
        self.bottom_bar_frame.setObjectName("OSDBottomBar")
        bottom_lay = QVBoxLayout(self.bottom_bar_frame)
        bottom_lay.setContentsMargins(self.s(40), self.s(10), self.s(40), self.s(15))
        bottom_lay.setSpacing(self.s(8))

        time_row = QHBoxLayout()
        self.lbl_time_pos = QLabel("00:00:00 / 00:00:00")
        self.lbl_time_pos.setObjectName("OSDTimeLabel")

        self.lbl_hint = QLabel("按「方向键」导航/调整进度  |  按「OK」确认")
        self.lbl_hint.setObjectName("OSDHintLabel")

        time_row.addWidget(self.lbl_time_pos)
        time_row.addStretch()
        time_row.addWidget(self.lbl_hint)
        bottom_lay.addLayout(time_row)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("OSDSeekSlider")
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setValue(0)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.seek_slider.sliderMoved.connect(self._on_slider_moved)
        self.seek_slider.sliderReleased.connect(self._on_slider_released)
        bottom_lay.addWidget(self.seek_slider)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(self.s(16))

        ctrl_icon_sz = QSize(self.s(18), self.s(18))

        self.btn_play_pause = QPushButton(" 暂停")
        self.btn_play_pause.setObjectName("OSDCtrlBtn")
        self.btn_play_pause.setIcon(Theme.create_icon("pause", "#E8EEF2", 18))
        self.btn_play_pause.setIconSize(ctrl_icon_sz)
        self.btn_play_pause.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_play_pause.clicked.connect(self._toggle_pause)

        self.btn_speed = QPushButton(f" {self._current_speed}x 倍速")
        self.btn_speed.setObjectName("OSDCtrlBtn")
        self.btn_speed.setIcon(Theme.create_icon("zap", "#00C2D1", 18))
        self.btn_speed.setIconSize(ctrl_icon_sz)
        self.btn_speed.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_speed.clicked.connect(self._cycle_speed)

        self.btn_next = QPushButton(" 下一集")
        self.btn_next.setObjectName("OSDCtrlBtn")
        self.btn_next.setIcon(Theme.create_icon("skip_forward", "#E8EEF2", 18))
        self.btn_next.setIconSize(ctrl_icon_sz)
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_next.clicked.connect(self._play_next_episode)

        ctrl_row.addWidget(self.btn_play_pause)
        ctrl_row.addWidget(self.btn_speed)
        ctrl_row.addWidget(self.btn_next)
        ctrl_row.addStretch()

        bottom_lay.addLayout(ctrl_row)

        # 4. 上层：中央卡片容器 (居中小卡片)
        self.center_card_frame = QFrame(self)
        self.center_card_frame.setObjectName("OSDCenterFrame")
        center_lay = QVBoxLayout(self.center_card_frame)
        center_lay.setContentsMargins(0, 0, 0, 0)

        self.loading_card = QFrame()
        self.loading_card.setObjectName("OSDLoadingCard")
        loading_lay = QHBoxLayout(self.loading_card)
        loading_lay.setContentsMargins(self.s(20), self.s(10), self.s(20), self.s(10))
        loading_lay.setSpacing(self.s(10))

        self.lbl_loading_icon = QLabel()
        self.lbl_loading_icon.setPixmap(
            Theme.create_icon("zap", "#00C2D1", self.s(20)).pixmap(self.s(20), self.s(20))
        )
        self.lbl_loading_text = QLabel("正在解析并连接视频源，请稍候...")
        self.lbl_loading_text.setObjectName("OSDLoadingText")

        loading_lay.addWidget(self.lbl_loading_icon)
        loading_lay.addWidget(self.lbl_loading_text)

        self.popup_card = QFrame()
        self.popup_card.setObjectName("CenterPopupCard")
        popup_lay = QHBoxLayout(self.popup_card)
        popup_lay.setContentsMargins(self.s(28), self.s(14), self.s(28), self.s(14))
        popup_lay.setSpacing(self.s(12))

        self.lbl_popup_icon = QLabel()
        self.lbl_popup_text = QLabel()
        self.lbl_popup_text.setObjectName("CenterPopupText")

        popup_lay.addWidget(self.lbl_popup_icon)
        popup_lay.addWidget(self.lbl_popup_text)
        self.popup_card.hide()

        center_lay.addWidget(self.loading_card)
        center_lay.addWidget(self.popup_card)

    # ── MPV 启动与异步 Worker ──
    def _start_mpv(self):
        if not self.video_url:
            logger.error("无有效播放地址，无法启动 MPV")
            self._close_player()
            return

        mpv_exe = shutil.which("mpv") or "mpv"

        if sys.platform == "win32":
            self.ipc_path = r"\\.\pipe\cinex_mpv_ipc"
        else:
            self.ipc_path = "/tmp/cinex_mpv_ipc.sock"
            if os.path.exists(self.ipc_path):
                try:
                    os.remove(self.ipc_path)
                except Exception:
                    pass

        ud = ConfigManager.load_user_data()
        s = ud.get("settings", {})
        hw = s.get("hardware_accel", "自动")
        skip_start = s.get("skip_start", 0)

        wid = str(int(self.video_container.winId()))
        cmd = [
            mpv_exe,
            f"--wid={wid}",
            f"--input-ipc-server={self.ipc_path}",
            "--no-border",
            "--keep-open=yes",
            "--idle=yes",
            "--force-window=yes",
            "--no-input-default-bindings",
            "--input-vo-keyboard=no",
            "--input-cursor=no",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            f"--title={self.title_text} - {self.current_ep_name}"
        ]

        if hw == "强制硬解":
            cmd.append("--hwdec=auto")
        elif hw == "软解":
            cmd.append("--hwdec=no")
        else:
            cmd.append("--hwdec=auto-safe")

        if sys.platform == "win32":
            cmd.append("--vo=gpu,direct3d11,gdi")
        else:
            cmd.append("--vo=gpu,x11")

        if skip_start > 0:
            cmd.append(f"--start={skip_start}")

        prog = ud.get("progress", {}).get(self.vod_id, {})
        pos_sec = prog.get("position_sec", 0)
        if pos_sec > 10 and skip_start == 0:
            cmd.append(f"--start={int(pos_sec)}")

        cmd.append(self.video_url)

        logger.info("启动内嵌 MPV: %s", " ".join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )

            self._worker = MPVStatusWorker(self.ipc_path)
            self._worker.status_updated.connect(self._on_status_updated)
            self._worker.start()

            self.loading_card.show()
            self._show_osd()
            self.seek_slider.setFocus()
        except Exception as e:
            logger.error("无法启动 MPV: %s", e)
            self._close_player()

    def _on_status_updated(self, pos_sec, duration_sec, is_buffering, has_video):
        self._pos_sec = pos_sec
        self._duration_sec = duration_sec

        if has_video or self._pos_sec > 0 or self._duration_sec > 0:
            if not self._has_video_started:
                self._has_video_started = True

        if self._has_video_started and not is_buffering:
            if self.loading_card.isVisible():
                self.loading_card.hide()
                if not self.popup_card.isVisible():
                    self.center_card_frame.hide()

        else:
            if is_buffering:
                self.lbl_loading_text.setText("正在缓冲视频数据，请稍候...")
                if not self.loading_card.isVisible():
                    self.loading_card.show()
            elif not self._has_video_started:
                self.lbl_loading_text.setText("正在解析并连接视频源，请稍候...")
                if not self.loading_card.isVisible():
                    self.loading_card.show()

        if self._duration_sec > 0:
            self.seek_slider.setRange(0, int(self._duration_sec))
            if not self._is_user_seeking:
                self.seek_slider.setValue(int(self._pos_sec))

            pos_str = time.strftime("%H:%M:%S", time.gmtime(self._pos_sec))
            dur_str = time.strftime("%H:%M:%S", time.gmtime(self._duration_sec))
            self.lbl_time_pos.setText(f"{pos_str} / {dur_str}")

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
        if self._worker:
            self._worker.send_cmd_async(["seek", target_sec, "absolute"])

    # ── OSD 显示与隐藏控制 ──
    def _show_osd(self):
        self.top_bar_frame.show()
        self.bottom_bar_frame.show()
        self.top_bar_frame.raise_()
        self.bottom_bar_frame.raise_()
        if not self.loading_card.isVisible() and not self.popup_card.isVisible():
            self.center_card_frame.hide()

        if not self.focusWidget() or not self.bottom_bar_frame.isAncestorOf(self.focusWidget()):
            self.seek_slider.setFocus()
        self._osd_timer.start()


    def _hide_osd(self):
        if not self._has_video_started:
            return
        if not self.loading_card.isVisible() and not self.popup_card.isVisible():
            self.center_card_frame.show()
        self.top_bar_frame.hide()
        self.bottom_bar_frame.hide()

    def _show_center_popup(self, icon_name, text):
        self.lbl_popup_icon.setPixmap(
            Theme.create_icon(icon_name, "#33D6E0", self.s(28)).pixmap(self.s(28), self.s(28))
        )
        self.lbl_popup_text.setText(text)
        self.popup_card.show()
        self.loading_card.hide()
        self.center_card_frame.show()  # 仅在需要弹窗提示时短暂显示
        self.center_card_frame.raise_()
        self._center_popup_timer.start()

    def _hide_center_popup(self):
        self.popup_card.hide()
        # 1.2 秒提示结束后，彻底隐藏中央容器
        if not self.loading_card.isVisible():
            self.center_card_frame.hide()

    def _hide_center_popup(self):
        self.popup_card.hide()

    # ── 播放动作控制 ──
    def _toggle_pause(self):
        if not self._worker:
            return
        self._show_osd()
        self._is_paused = not self._is_paused
        self._worker.send_cmd_async(["set_property", "pause", self._is_paused])
        if self._is_paused:
            self.btn_play_pause.setIcon(Theme.create_icon("play", "#E8EEF2", 18))
            self.btn_play_pause.setText(" 继续")
            self._show_center_popup("pause", "暂停")
        else:
            self.btn_play_pause.setIcon(Theme.create_icon("pause", "#E8EEF2", 18))
            self.btn_play_pause.setText(" 暂停")
            self._show_center_popup("play", "播放")

    def _cycle_speed(self):
        if not self._worker:
            return
        self._show_osd()
        idx = self._speeds.index(self._current_speed)
        next_idx = (idx + 1) % len(self._speeds)
        self._current_speed = self._speeds[next_idx]
        self._worker.send_cmd_async(["set_property", "speed", self._current_speed])
        self.btn_speed.setText(f" {self._current_speed}x 倍速")
        self._show_center_popup("zap", f"{self._current_speed}x 倍速")

    def _play_next_episode(self):
        eps = self.routes[self.route_idx].get("episodes", []) if self.routes else []
        if self.ep_idx + 1 < len(eps):
            self._save_progress()
            self._close_mpv_process()
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

    def _close_mpv_process(self):
        if self._worker:
            self._worker.stop()
            self._worker.send_cmd_async(["quit"])
            self._worker = None

        if self._process:
            try:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
            except Exception:
                pass
            self._process = None

    def _close_player(self):
        self._save_progress()
        self._close_mpv_process()
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
        self._close_mpv_process()
        try:
            QApplication.instance().removeEventFilter(self)
        except Exception:
            pass
        super().closeEvent(event)

    # ── 电视遥控器防误触唤醒与 2D 焦点物理导航 ──
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()

            # 1. 随时响应 Exit/Esc/Backspace 退出播放
            if key in (Qt.Key.Key_Escape, Qt.Key.Key_Back, Qt.Key.Key_Backspace):
                self._close_player()
                return True

            is_osd_visible = self.top_bar_frame.isVisible() or self.bottom_bar_frame.isVisible()

            # 2. 直觉控制：当 OSD 隐藏时，按方向键/OK 仅唤醒 OSD，防误触
            if not is_osd_visible:
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
                           Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    self._show_osd()
                    self.seek_slider.setFocus()
                    return True

            # 3. OSD 已唤醒状态：重置倒计时，重绘并按 2D 焦点导航
            self._show_osd()
            fw = QApplication.focusWidget()

            ctrl_buttons = [self.btn_play_pause, self.btn_speed, self.btn_next, self.btn_close]

            # A. 焦点在进度条滑块上 (或默认回退状态)
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

    # ── 样式表 ───────────────────────────────────────
    def _apply_theme(self):
        accent = "#00C2D1"
        accent_hover = "#33D6E0"
        text = "#E8EEF2"
        text2 = "#8CA0B0"

        self.setStyleSheet(f"""
            QWidget#EmbeddedPlayer {{
                background-color: #000000;
            }}

            QFrame#OSDTopBar {{
                background-color: rgba(12, 18, 24, 0.85);
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }}

            QFrame#OSDBottomBar {{
                background-color: rgba(12, 18, 24, 0.85);
                border-top: 1px solid rgba(255, 255, 255, 0.1);
            }}

            QFrame#OSDCenterFrame {{
                background: transparent;
            }}

            QLabel#OSDTitle {{
                color: {text};
                font-size: {self.s(22)}px;
                font-weight: 800;
                background: transparent;
            }}

            QLabel#OSDTimeLabel {{
                color: {accent};
                font-size: {self.s(15)}px;
                font-weight: 700;
                background: transparent;
            }}

            QLabel#OSDHintLabel {{
                color: {text2};
                font-size: {self.s(13)}px;
                font-weight: 500;
                background: transparent;
            }}

            QFrame#OSDLoadingCard {{
                background-color: rgba(10, 15, 20, 0.88);
                border: 1.5px solid {accent};
                border-radius: {self.s(12)}px;
            }}

            QLabel#OSDLoadingText {{
                color: {accent};
                font-size: {self.s(15)}px;
                font-weight: 700;
                background: transparent;
            }}

            QFrame#CenterPopupCard {{
                background-color: rgba(10, 15, 20, 0.88);
                border: 2px solid {accent};
                border-radius: {self.s(16)}px;
            }}

            QLabel#CenterPopupText {{
                color: {accent_hover};
                font-size: {self.s(24)}px;
                font-weight: 800;
                background: transparent;
            }}

            QSlider#OSDSeekSlider::groove:horizontal {{
                height: {self.s(6)}px;
                background: rgba(255, 255, 255, 0.2);
                border-radius: {self.s(3)}px;
            }}
            QSlider#OSDSeekSlider::sub-page:horizontal {{
                background: {accent};
                border-radius: {self.s(3)}px;
            }}
            QSlider#OSDSeekSlider::handle:horizontal {{
                background: {accent_hover};
                width: {self.s(16)}px;
                height: {self.s(16)}px;
                margin: -{self.s(5)}px 0;
                border-radius: {self.s(8)}px;
            }}
            QSlider#OSDSeekSlider::handle:horizontal:hover, QSlider#OSDSeekSlider::handle:horizontal:focus {{
                background: #FFFFFF;
                border: 2px solid {accent};
                width: {self.s(20)}px;
                height: {self.s(20)}px;
                margin: -{self.s(7)}px 0;
                border-radius: {self.s(10)}px;
            }}
            QSlider#OSDSeekSlider:focus {{
                outline: none;
            }}

            QPushButton#OSDCtrlBtn {{
                background-color: rgba(255, 255, 255, 0.1);
                color: {text};
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: {self.s(10)}px;
                padding: {self.s(8)}px {self.s(18)}px;
                font-size: {self.s(14)}px;
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
                border-radius: {self.s(10)}px;
                padding: {self.s(6)}px {self.s(16)}px;
                font-size: {self.s(13)}px;
                font-weight: 600;
            }}
            QPushButton#OSDCloseBtn:hover, QPushButton#OSDCloseBtn:focus {{
                background-color: #EF4444;
                color: #FFFFFF;
                border: 2px solid #FFFFFF;
                outline: none;
            }}
        """)