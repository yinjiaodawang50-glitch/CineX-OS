# ui/player_view.py
"""
CineX OS — 10-Foot 内嵌式 MPV 播放器与矢量 SVG 图标 OSD 菜单
- 彻底解决“打开播放器无操作不自动隐藏”问题（首帧连接成功后自动重启 4 秒隐退计时器）
- 彻底修复聚焦失效与高亮不显示问题（回归父子控件树，共享窗口焦点）
- 物理清屏机制 (CompositionMode_Clear)：彻底根除旧图像重叠、按钮多高亮、卡片不消失 Bug
- 10-Foot 遥控器直觉唤醒机制（隐藏时按键仅唤醒 OSD，防止误切倍速/误快进）
- 全新发光可聚焦/可拖动进度条 (QSlider)，支持 2D 空间物理焦点导航
- 彻底解决“有声音无画面/黑屏”问题 (禁用 Qt 重绘背景 + 显式 MPV VO 降级链)
- 彻底解决主线程阻塞卡死（IPC 状态轮询与命令全部移入后台 Worker 线程）
- 秒级精准断点续播进度回写 user_data.json
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
    QFrame, QSlider, QProgressBar, QApplication
)
from PyQt6.QtCore import (
    Qt, QTimer, QSize, QEvent, QThread, pyqtSignal
)
from PyQt6.QtGui import QColor, QFont, QPainter

from core.config import ConfigManager
from core.theme import Theme

logger = logging.getLogger("EmbeddedPlayer")


class OSDOverlay(QWidget):
    """置顶全透明 OSD 悬浮蒙层"""
    def __init__(self, parent_player):
        super().__init__(parent_player)
        self.player = parent_player

        if sys.platform == "win32":
            # Windows 下保持子控件，DWM 完美渲染
            self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        else:
            # Linux/X11 下使用顶级置顶 + BypassWindowManagerHint 绕过 X11 遮挡
            self.setWindowFlags(
                Qt.WindowType.Window |
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint |
                Qt.WindowType.BypassWindowManagerHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setObjectName("OSDOverlay")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.end()
        super().paintEvent(event)


class MPVStatusWorker(QThread):
    """后台异步 IPC 线程：专门负责与 MPV 管道通信，彻底解除主线程卡死"""
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

        # 安装全局与本地事件过滤器
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

    def _sync_overlay_geometry(self):
        if hasattr(self, "osd_overlay") and self.osd_overlay:
            if sys.platform == "win32":
                self.osd_overlay.setGeometry(0, 0, self.width(), self.height())
            else:
                try:
                    global_pos = self.mapToGlobal(QPoint(0, 0))
                    self.osd_overlay.setGeometry(global_pos.x(), global_pos.y(), self.width(), self.height())
                except Exception:
                    pass


    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        if not self._mpv_started:
            self._mpv_started = True
            QTimer.singleShot(50, self._start_mpv)

    def _extract_ep_info(self):
        if self.routes and self.route_idx < len(self.routes):
            eps = self.routes[self.route_idx].get("episodes", [])
            if eps and self.ep_idx < len(eps):
                self.current_ep_name = eps[self.ep_idx].get("name", "正片")
                self.video_url = eps[self.ep_idx].get("url", "").strip()

    # ── UI 构建 ──────────────────────────────────────────────────
    def _build_ui(self):
        # 视频渲染容器与 OSDOverlay 作为同级原生子控件
        self.video_container = QWidget(self)
        self.video_container.setObjectName("VideoContainer")
        self.video_container.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_container.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)

        self.osd_overlay = OSDOverlay(self)

        # 布局直接加在 osd_overlay 上
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

        # 中央动效与网络缓冲提示
        osd_lay.addStretch()
        center_box = QHBoxLayout()
        center_box.addStretch()

        # 加载缓冲提示卡片
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

        # 中央弹窗（快进退/暂停）卡片
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

        # 底部 OSD 播放控制栏
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

        # 核心改动：支持遥控器聚焦与拖动的进度条滑块
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

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w, h = self.width(), self.height()
        # 画面与 OSD 在同一窗口内部精准重叠
        self.video_container.setGeometry(0, 0, w, h)
        self.osd_overlay.setGeometry(0, 0, w, h)
        self.osd_overlay.raise_()  # 在同级原生窗口中，将 OSD 原生子窗口压在视频原生子窗口上方

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

        # 1. 精准识别并应用设置页里的“硬件加速”选项
        if hw == "强制硬解":
            cmd.append("--hwdec=auto")      # 强制启用 GPU 硬件解码
        elif hw == "软解":
            cmd.append("--hwdec=no")        # 完全关闭硬解，纯 CPU 软解
        else:
            cmd.append("--hwdec=auto-safe") # 默认“自动”：优先硬解，失败自动切软解

        # 2. 区分平台配置视频渲染驱动 (防止 Linux 因识别不了 direct3d11 报错闪退)
        if sys.platform == "win32":
            cmd.append("--vo=gpu,direct3d11,gdi")
        else:
            cmd.append("--vo=gpu,x11")

        # 3. 处理跳过片头与断点续播 (之前漏掉了这里)
        if skip_start > 0:
            cmd.append(f"--start={skip_start}")

        prog = ud.get("progress", {}).get(self.vod_id, {})
        pos_sec = prog.get("position_sec", 0)
        if pos_sec > 10 and skip_start == 0:
            cmd.append(f"--start={int(pos_sec)}")

        # 4. 传入视频地址 (必须放最后)
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

            # 启动后台非阻塞 IPC 状态轮询线程
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
        """后台 Worker 异步回调：捕捉首帧画面，清除加载卡片并刷新进度"""
        self._pos_sec = pos_sec
        self._duration_sec = duration_sec

        first_start = False
        # 只要接收到图像画面、播放秒数或总时长，立刻判定视频建立成功
        if has_video or self._pos_sec > 0 or self._duration_sec > 0:
            if not self._has_video_started:
                self._has_video_started = True
                first_start = True

        # 首帧连接成功后立刻强制隐藏中央加载卡片
        if self._has_video_started and not is_buffering:
            if self.loading_card.isVisible():
                self.loading_card.hide()
                self.osd_overlay.update()
            # 核心修正：首帧播放成功后，重新激活启动 4 秒隐藏计时器！
            if first_start:
                self._osd_timer.start()
        else:
            if is_buffering:
                self.lbl_loading_text.setText("正在缓冲视频数据，请稍候...")
                if not self.loading_card.isVisible():
                    self.loading_card.show()
                    self.osd_overlay.update()
            elif not self._has_video_started:
                self.lbl_loading_text.setText("正在解析并连接视频源，请稍候...")
                if not self.loading_card.isVisible():
                    self.loading_card.show()
                    self.osd_overlay.update()

        # 更新进度条范围与当前秒数（拖动滑块时不被覆盖）
        if self._duration_sec > 0:
            self.seek_slider.setRange(0, int(self._duration_sec))
            if not self._is_user_seeking:
                self.seek_slider.setValue(int(self._pos_sec))

            pos_str = time.strftime("%H:%M:%S", time.gmtime(self._pos_sec))
            dur_str = time.strftime("%H:%M:%S", time.gmtime(self._duration_sec))
            self.lbl_time_pos.setText(f"{pos_str} / {dur_str}")

    # ── 滑块拖动与快进退动作 ──
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
        self._sync_overlay_geometry()
        self.osd_overlay.show()
        self.osd_overlay.raise_()

        # 【关键修正 3】：强行把被 MPV X11 窗口抢走的焦点抢回来给 OSD
        if sys.platform != "win32":
            self.osd_overlay.activateWindow()

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
        self.osd_overlay.update()
        self._center_popup_timer.start()

    def _hide_center_popup(self):
        self.popup_card.hide()
        self.osd_overlay.update()

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
        self.osd_overlay.close()
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
        self.osd_overlay.close()
        super().closeEvent(event)

    # ── 电视遥控器防误触唤醒与 2D 焦点物理导航 ──
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

            # 3. OSD 已唤醒状态：重置倒计时，重绘并按 2D 焦点导航
            self._show_osd()
            self.osd_overlay.update()
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
                    self.osd_overlay.update()
                    return True
                elif key == Qt.Key.Key_Up:
                    self.btn_close.setFocus()
                    self.osd_overlay.update()
                    return True
                elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    self._toggle_pause()
                    return True

            # B. 焦点在控制按钮上
            if fw in ctrl_buttons:
                if key == Qt.Key.Key_Up:
                    self.seek_slider.setFocus()
                    self.osd_overlay.update()
                    return True
                elif key == Qt.Key.Key_Left:
                    idx = ctrl_buttons.index(fw)
                    prev_idx = (idx - 1) % len(ctrl_buttons)
                    ctrl_buttons[prev_idx].setFocus()
                    self.osd_overlay.update()
                    return True
                elif key == Qt.Key.Key_Right:
                    idx = ctrl_buttons.index(fw)
                    next_idx = (idx + 1) % len(ctrl_buttons)
                    ctrl_buttons[next_idx].setFocus()
                    self.osd_overlay.update()
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

            QWidget#OSDOverlay {{
                background: transparent;
                background-color: transparent;
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

            /* ── 可聚焦进度条滑块 ── */
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
