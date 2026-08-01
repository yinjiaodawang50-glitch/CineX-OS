# ui/dialogs.py
import os
import sys
import logging
import shutil
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QMessageBox, QLineEdit,
    QTabWidget, QFrame, QWidget, QApplication
)
from PyQt6.QtCore import Qt, QTimer, QSize, QEvent
from PyQt6.QtGui import QColor

from core.config import ConfigManager
from core.network import NetworkEngine, SafeThread
from core.theme import Theme

logger = logging.getLogger("Dialogs")


class SourceSelectorDialog(QDialog):
    def __init__(self, parent, sources, current_source, latencies):
        super().__init__(parent)
        self.setWindowTitle("切换数据源")
        self.resize(380, 440)
        self.setModal(True)
        self.setStyleSheet(parent.styleSheet())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        lbl = QLabel("<b>请选择数据源</b>")
        lbl.setStyleSheet("font-size:14px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lbl)

        self.lw = QListWidget()
        self.lw.setObjectName("SourceList")
        self.lw.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        for src in sources:
            ms = latencies.get(src, None)
            if ms is None:
                badge = "未检测"; dot = "⚪"
            elif ms < 0:
                badge = "超时/不可用"; dot = "🔴"
            elif ms < 400:
                badge = f"{ms}ms ✓"; dot = "🟢"
            elif ms < 1000:
                badge = f"{ms}ms"; dot = "🟡"
            else:
                badge = f"{ms}ms 慢"; dot = "🔴"

            item = QListWidgetItem(f"{dot}  {src}\n    {badge}")
            item.setData(Qt.ItemDataRole.UserRole, src)
            item.setSizeHint(QSize(320, 52))
            self.lw.addItem(item)
            if src == current_source:
                self.lw.setCurrentItem(item)

        lay.addWidget(self.lw)
        self.lw.itemDoubleClicked.connect(self.accept)
        self.lw.itemActivated.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("切换")
        btn_ok.setObjectName("AccentButton")
        btn_ok.clicked.connect(self.accept)
        btn_no = QPushButton("取消")
        btn_no.clicked.connect(self.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_no)
        lay.addLayout(btn_row)
        self.lw.setFocus()

    def get_selected(self):
        item = self.lw.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class AddSourceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加数据源")
        self.resize(480, 240)
        self.setModal(True)
        if hasattr(parent, 'is_dark_mode'):
            is_dark = parent.is_dark_mode
        elif hasattr(parent, 'main'):
            is_dark = parent.main.is_dark_mode
        else:
            is_dark = True
        self.setStyleSheet(parent.styleSheet())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)

        tip = QLabel("支持两种方式：① 普通采集站 API  ② TVBox 订阅链接（自动批量导入）")
        tip.setStyleSheet(f"color:{Theme.get('text2', is_dark)};font-size:11px;")
        lay.addWidget(tip)

        lay.addWidget(QLabel("数据源名称（TVBox 订阅可留空）："))
        self.inp_name = QLineEdit()
        self.inp_name.setPlaceholderText("如：卧龙资源（TVBox 订阅时可不填）")
        self.inp_name.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self.inp_name)

        lay.addWidget(QLabel("API 地址 / TVBox 订阅链接："))
        self.inp_url = QLineEdit()
        self.inp_url.setPlaceholderText("https://xxx/api.php/provide/vod/  或  TVBox 订阅 JSON 链接")
        self.inp_url.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self.inp_url)

        row = QHBoxLayout()
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setObjectName("BatchButton")
        self.btn_ok.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_ok.clicked.connect(self._save)
        self.btn_no = QPushButton("取消")
        self.btn_no.setObjectName("BatchButton")
        self.btn_no.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_no.clicked.connect(self.close)
        row.addWidget(self.btn_ok)
        row.addWidget(self.btn_no)
        lay.addLayout(row)

        self._nav_controls = [self.inp_name, self.inp_url, self.btn_ok, self.btn_no]
        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                fw = QApplication.focusWidget()
                if fw in self._nav_controls:
                    idx = self._nav_controls.index(fw)
                    if key == Qt.Key.Key_Down:
                        next_idx = (idx + 1) % len(self._nav_controls)
                    else:
                        next_idx = (idx - 1) % len(self._nav_controls)
                    self._nav_controls[next_idx].setFocus()
                    return True
        return super().eventFilter(obj, event)

    def _save(self):
        name = self.inp_name.text().strip()
        url = self.inp_url.text().strip()
        if not url:
            QMessageBox.warning(self, "错误", "地址不能为空")
            return
        if not url.startswith("http"):
            QMessageBox.warning(self, "错误", "地址须以 http/https 开头")
            return

        self.setEnabled(False)
        QApplication.processEvents()
        tvbox_result = NetworkEngine.fetch_tvbox_sources(url)
        self.setEnabled(True)

        if tvbox_result:
            src = ConfigManager.load_sources()
            src.update(tvbox_result)
            ConfigManager.save_sources(src)
            QMessageBox.information(
                self, "TVBox 导入成功",
                f"成功识别 TVBox 订阅\n共导入 {len(tvbox_result)} 个数据源！"
            )
            self.accept()
            return

        if not name:
            QMessageBox.warning(self, "错误", "普通 API 需要填写数据源名称")
            return
        src = ConfigManager.load_sources()
        src[name] = url
        ConfigManager.save_sources(src)
        QMessageBox.information(self, "成功", f"已添加：{name}")
        self.accept()


class EpisodeDialog(QDialog):
    def __init__(self, parent, movie_data, current_api):
        super().__init__(parent)
        self.parent_window = parent
        self.movie_data = movie_data
        self.api = movie_data.get("api", current_api)
        self.vod_id = str(movie_data["vod_id"])
        self.title_text = movie_data["vod_name"]
        self.is_dark = parent.is_dark_mode
        self.routes = []
        self._playing = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.resize(parent.s(580), parent.s(520))
        self.setModal(True)

        d = Theme.DARK if self.is_dark else Theme.LIGHT
        self.setStyleSheet(parent.styleSheet() + f"""
            QDialog {{
                background: {d['panel']};
                border: 2px solid {d['accent']};
                border-radius: {parent.s(16)}px;
            }}
        """)

        self._loading_label = QLabel("正在获取播放源...", self)
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(f"font-size:{parent.s(16)}px; color:{d['text']};")

        main_lay = QVBoxLayout(self)
        main_lay.addWidget(self._loading_label)

        self._fetch_detail_async()

    def s(self, px_val):
        return self.parent_window.s(px_val)

    def _fetch_detail_async(self):
        detail_url = NetworkEngine.get_api_url(self.api, f"ac=detail&ids={self.vod_id}")

        def task():
            return NetworkEngine.fetch_json_with_retry(detail_url)

        self._load_thread = SafeThread(task)
        self._load_thread.finished.connect(self._on_detail_fetched)
        self._load_thread.start()

    def _on_detail_fetched(self, data):
        if not data or "list" not in data or not data["list"]:
            QMessageBox.warning(self, "错误", "获取播放源失败")
            QTimer.singleShot(0, self.close)
            return

        vod = data["list"][0]
        self.routes = NetworkEngine.parse_all_routes(vod.get("vod_play_url", ""))
        if not self.routes:
            QMessageBox.warning(self, "无播放源", "该影片暂无可用链接")
            QTimer.singleShot(0, self.close)
            return

        # 移除 loading 界面并构建完整 UI
        if self.layout():
            QWidget().setLayout(self.layout())
        self._build_ui()

    def _build_ui(self):
        d = Theme.DARK if self.is_dark else Theme.LIGHT
        lay = QVBoxLayout(self)
        lay.setContentsMargins(self.s(20), self.s(20), self.s(20), self.s(20))
        lay.setSpacing(self.s(12))

        title_row = QHBoxLayout()
        lbl = QLabel(f"<b>《{self.title_text}》</b>")
        lbl.setStyleSheet(f"font-size:{self.s(16)}px; color:{d['text']};")
        title_row.addWidget(lbl)
        title_row.addStretch()

        self.btn_fav = QPushButton()
        self.btn_fav.setFixedSize(self.s(90), self.s(32))
        self._update_fav_btn()
        self.btn_fav.clicked.connect(self._toggle_fav)
        title_row.addWidget(self.btn_fav)
        title_row.addSpacing(self.s(8))

        self.btn_close_top = QPushButton("✕")
        self.btn_close_top.setFixedSize(self.s(32), self.s(32))
        self.btn_close_top.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {d['text2']};
                font-size: {self.s(14)}px;
                font-weight: bold;
                border-radius: {self.s(6)}px;
            }}
            QPushButton:hover {{ background: {d['hover']}; color: {d['danger']}; }}
            QPushButton:focus {{ background: {d['danger']}; color: #fff; }}
        """)
        self.btn_close_top.clicked.connect(self.close)
        title_row.addWidget(self.btn_close_top)
        lay.addLayout(title_row)

        ud = ConfigManager.load_user_data()
        prog = ud["progress"].get(self.vod_id, {})
        if prog.get("ep_name"):
            hint = QLabel(f"▶ 上次看到：{prog['ep_name']}，双击对应集数续播")
            hint.setStyleSheet(f"color:{d['accent']}; font-size:{self.s(12)}px; padding:{self.s(2)}px 0;")
            lay.addWidget(hint)

        self.tab = QTabWidget()
        self.tab.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for ri, route in enumerate(self.routes):
            lw = QListWidget()
            lw.setViewMode(QListWidget.ViewMode.IconMode)
            lw.setResizeMode(QListWidget.ResizeMode.Adjust)
            lw.setMovement(QListWidget.Movement.Static)
            lw.setSpacing(self.s(8))
            for ep in route["episodes"]:
                item = QListWidgetItem(ep["name"])
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if ep["name"] == prog.get("ep_name", ""):
                    item.setForeground(QColor(d["accent"]))
                item.setSizeHint(QSize(self.s(120), self.s(38)))
                lw.addItem(item)
            lw.itemActivated.connect(lambda it, r=ri, w=lw: self._play(r, w.currentRow()))
            self.tab.addTab(lw, route["name"])
        lay.addWidget(self.tab)

        last_route = ud.get("last_route", {}).get(self.vod_id, 0)
        if 0 <= last_route < self.tab.count():
            self.tab.setCurrentIndex(last_route)
            lw = self.tab.widget(last_route)
            if lw and lw.count() > 0:
                lw.setFocus()
                lw.setCurrentRow(0)

        btn_close = QPushButton("关闭")
        btn_close.setFixedHeight(self.s(36))
        btn_close.clicked.connect(self.close)
        lay.addWidget(btn_close)

    def _play(self, route_idx, ep_idx):
        if self._playing:
            return
        self._playing = True
        self.current_route_idx = route_idx
        self.current_ep_idx = ep_idx
        self._try_play(route_idx, ep_idx)

    def _try_play(self, route_idx, ep_idx):
        if route_idx >= len(self.routes):
            QMessageBox.warning(self, "播放失败", "所有线路均不可用，请稍后重试")
            self._playing = False
            return

        self.close()

        from ui.player_view import EmbeddedPlayerWindow
        parent = self.parent_window
        parent._current_player = EmbeddedPlayerWindow(
            parent_window=parent,
            movie_data={
                "vod_id": self.vod_id,
                "vod_name": self.title_text,
                "vod_pic": self.movie_data.get("vod_pic", ""),
                "api": self.api,
                "parsed_routes": self.routes
            },
            route_idx=route_idx,
            ep_idx=ep_idx,
            api_name=self.api
        )
        parent._current_player.showFullScreen()

    def _update_fav_btn(self):
        ids = [str(x["vod_id"]) for x in ConfigManager.load_user_data()["favorites"]]
        d = Theme.DARK if self.is_dark else Theme.LIGHT
        if self.vod_id in ids:
            self.btn_fav.setText("★ 已收藏")
            self.btn_fav.setStyleSheet(
                f"background:{d['accent']}; color:#fff; border:none; border-radius:{self.s(8)}px; font-weight:600;")
        else:
            self.btn_fav.setText("☆ 收藏")
            self.btn_fav.setStyleSheet("")

    def _toggle_fav(self):
        ud = ConfigManager.load_user_data()
        ids = [str(x["vod_id"]) for x in ud["favorites"]]
        if self.vod_id in ids:
            ud["favorites"] = [x for x in ud["favorites"] if str(x["vod_id"]) != self.vod_id]
        else:
            ud["favorites"].insert(0, {
                "vod_id": self.vod_id,
                "vod_name": self.title_text,
                "vod_pic": self.movie_data.get("vod_pic", ""),
                "vod_remarks": self.movie_data.get("vod_remarks", "高清"),
                "api": self.api
            })
        ConfigManager.save_user_data(ud)
        self._update_fav_btn()

    def closeEvent(self, event):
        if hasattr(self, '_load_thread') and self._load_thread:
            try:
                self._load_thread.finished.disconnect()
            except Exception:
                pass
            self._load_thread.request_abort()
        super().closeEvent(event)