# ui/settings_page.py
"""
CineX OS — 设置全屏页（数据源列表 2D 焦点导航修复版）
- 修复 QListWidget 无法被遥控器/键盘聚焦选中的问题（封装入 SettingRow 体系）
- 集成数据源展现、添加源、删除源、延迟检测与默认启动源设置
- 左右导航强对比焦点交互（专为 TV 遥控器与大屏设计）
"""

import os
import sys
import shutil
import logging

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QFrame, QListWidget, QListWidgetItem, QStackedWidget,
    QApplication, QMessageBox, QComboBox,
    QLineEdit, QSpinBox, QCheckBox, QFileDialog, QDialog
)
from PyQt6.QtCore import Qt, QSize, QEvent
from PyQt6.QtGui import QFont

from core.config import ConfigManager
from core.theme import Theme


class SettingsPage(QWidget):
    GROUPS = ["播放", "界面", "数据源", "系统"]

    def __init__(self, main_window):
        super().__init__(parent=None)
        self.main = main_window
        self.setObjectName("SettingsPage")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        QApplication.setStyle("Fusion")

        self._current_row_focus = None
        self._in_control_mode = False
        self._button_group_mode = False
        self._active_page = None

        # 1. 优先构建 UI 控件！
        self._build_ui()
        self._apply_theme()
        self._apply_font_size()
        self.installEventFilter(self)

        # 2. 控件构建完成后，再进行全屏铺满！
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        self.showFullScreen()

    @property
    def is_dark_mode(self):
        return self.main.is_dark_mode

    def _colors(self):
        return Theme.DARK if self.main.is_dark_mode else Theme.LIGHT

    # ── UI 构建 ──────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧导航
        self._nav = QFrame()
        self._nav.setObjectName("NavPanel")
        self._nav.setFixedWidth(self.main.s(240))
        nav_lay = QVBoxLayout(self._nav)
        nav_lay.setContentsMargins(self.main.s(16), self.main.s(24),
                                   self.main.s(16), self.main.s(24))
        nav_lay.setSpacing(0)

        self._btn_back = QPushButton()
        self._btn_back.setObjectName("TopBarBtn")
        self._btn_back.setFixedSize(self.main.s(36), self.main.s(36))
        self._btn_back.setIconSize(QSize(self.main.s(20), self.main.s(20)))
        self._btn_back.setToolTip("返回 (Esc)")
        self._btn_back.clicked.connect(self.close)
        self._btn_back.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        nav_lay.addWidget(self._btn_back, alignment=Qt.AlignmentFlag.AlignLeft)

        nav_lay.addSpacing(self.main.s(24))

        title = QLabel("设置")
        title.setObjectName("TopBarTitle")
        nav_lay.addWidget(title)
        nav_lay.addSpacing(self.main.s(20))

        self._group_list = QListWidget()
        self._group_list.setObjectName("CategoryList")
        self._group_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._group_list.setStyleSheet("background: transparent; border: none;")
        self._group_list.viewport().setStyleSheet("background: transparent;")
        self._group_list.viewport().setAutoFillBackground(False)

        for name in self.GROUPS:
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(self.main.s(180), self.main.s(44)))
            self._group_list.addItem(item)
        self._group_list.setCurrentRow(0)
        self._group_list.currentRowChanged.connect(self._on_group_changed)
        nav_lay.addWidget(self._group_list)
        nav_lay.addStretch()

        root.addWidget(self._nav)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName("SettingsSep")
        root.addWidget(sep)

        self._stack = QStackedWidget()
        self._stack.setObjectName("SettingsContent")
        self._stack.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._stack.addWidget(self._build_playback_page())
        self._stack.addWidget(self._build_interface_page())
        self._stack.addWidget(self._build_sources_page())
        self._stack.addWidget(self._build_system_page())
        root.addWidget(self._stack, 1)

    # ── 辅助组件 ─────────────────────────────────────────────────
    def _section_title(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("SectionLabel")
        return lbl

    def _row_label(self, text, sub=None):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self.main.s(2))
        lbl = QLabel(text)
        lbl.setObjectName("RowTitleLabel")
        lay.addWidget(lbl)
        if sub:
            sub_lbl = QLabel(sub)
            sub_lbl.setObjectName("RowSubLabel")
            lay.addWidget(sub_lbl)
        return w

    def _option_row(self, label_widget, control_widget):
        row = QFrame()
        row.setObjectName("SettingRow")
        row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(self.main.s(12), self.main.s(8), self.main.s(12), self.main.s(8))
        lay.setSpacing(self.main.s(20))
        lay.addWidget(label_widget, 1)
        lay.addWidget(control_widget)
        return row

    def _button_row(self, *buttons, stretch=True):
        row = QFrame()
        row.setObjectName("SettingRow")
        row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(self.main.s(12), self.main.s(8), self.main.s(12), self.main.s(8))
        lay.setSpacing(self.main.s(12))
        for btn in buttons:
            lay.addWidget(btn)
        if stretch:
            lay.addStretch()
        return row

    # ── 页面：播放 ─────────────────────────────────────────────────
    def _build_playback_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(self.main.s(48), self.main.s(40),
                               self.main.s(48), self.main.s(40))
        lay.setSpacing(0)

        lay.addWidget(self._section_title("播放器"))

        player = self._get_setting("player", "内置")
        self._player_combo = QComboBox()
        self._player_combo.setFixedSize(self.main.s(160), self.main.s(32))
        self._player_combo.addItems(["内置", "mpv", "VLC", "系统默认"])
        self._player_combo.setCurrentText(player)
        self._player_combo.currentTextChanged.connect(lambda t: self._save_setting("player", t))
        self._player_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("播放器", "选择使用内置播放器或外部播放器"),
            self._player_combo
        ))

        hw = self._get_setting("hardware_accel", "自动")
        self._hw_combo = QComboBox()
        self._hw_combo.setFixedSize(self.main.s(160), self.main.s(32))
        self._hw_combo.addItems(["自动", "强制硬解", "软解"])
        self._hw_combo.setCurrentText(hw)
        self._hw_combo.currentTextChanged.connect(lambda t: self._save_setting("hardware_accel", t))
        self._hw_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("硬件加速", "视频解码方式（重启后生效）"),
            self._hw_combo
        ))

        lay.addSpacing(self.main.s(16))
        lay.addWidget(self._section_title("播放行为"))

        auto_next = self._get_setting("auto_next_episode", True)
        self._auto_next_chk = QCheckBox()
        self._auto_next_chk.setChecked(auto_next)
        self._auto_next_chk.stateChanged.connect(lambda s: self._save_setting("auto_next_episode", s == Qt.CheckState.Checked.value))
        self._auto_next_chk.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("自动播放下一集", "当前剧集结束后自动切换至下一集"),
            self._auto_next_chk
        ))

        skip_start = self._get_setting("skip_start", 0)
        spin_start = QSpinBox()
        spin_start.setRange(0, 300)
        spin_start.setSingleStep(5)
        spin_start.setSuffix(" 秒")
        spin_start.setValue(skip_start)
        spin_start.valueChanged.connect(lambda v: self._save_setting("skip_start", v))
        spin_start.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("跳过片头", "播放时自动跳过开头的秒数"),
            spin_start
        ))

        skip_end = self._get_setting("skip_end", 0)
        spin_end = QSpinBox()
        spin_end.setRange(0, 300)
        spin_end.setSingleStep(5)
        spin_end.setSuffix(" 秒")
        spin_end.setValue(skip_end)
        spin_end.valueChanged.connect(lambda v: self._save_setting("skip_end", v))
        spin_end.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("跳过片尾", "播放时自动跳过结尾的秒数"),
            spin_end
        ))

        lay.addStretch()
        return page

    # ── 页面：界面 ─────────────────────────────────────────────────
    def _build_interface_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(self.main.s(48), self.main.s(40),
                               self.main.s(48), self.main.s(40))
        lay.setSpacing(0)

        lay.addWidget(self._section_title("显示"))
        btn_theme = QPushButton("切换为浅色" if self.main.is_dark_mode else "切换为深色")
        btn_theme.setObjectName("BatchButton")
        btn_theme.setFixedSize(self.main.s(140), self.main.s(36))
        btn_theme.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        def _do_toggle():
            self.main._toggle_theme()
            btn_theme.setText("切换为浅色" if self.main.is_dark_mode else "切换为深色")
            self._apply_theme()

        btn_theme.clicked.connect(_do_toggle)
        lay.addWidget(self._option_row(
            self._row_label("主题", "深色护眼，浅色明亮"), btn_theme))

        current_font_size = self._get_setting("font_size", 12)
        spin_font = QSpinBox()
        spin_font.setRange(8, 18)
        spin_font.setSingleStep(1)
        spin_font.setSuffix(" pt")
        spin_font.setValue(current_font_size)
        spin_font.valueChanged.connect(self._on_font_size_changed)
        spin_font.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("字体大小", "调整界面字体大小（即时预览）"),
            spin_font
        ))

        radius = self._get_setting("poster_radius", "中")
        self._radius_combo = QComboBox()
        self._radius_combo.setFixedSize(self.main.s(120), self.main.s(32))
        self._radius_combo.addItems(["小", "中", "大"])
        self._radius_combo.setCurrentText(radius)
        self._radius_combo.currentTextChanged.connect(lambda t: self._save_setting("poster_radius", t))
        self._radius_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("海报圆角", "卡片四角的圆弧大小"),
            self._radius_combo
        ))

        hover = self._get_setting("hover_intensity", "弱")
        self._hover_combo = QComboBox()
        self._hover_combo.setFixedSize(self.main.s(120), self.main.s(32))
        self._hover_combo.addItems(["无", "弱", "强"])
        self._hover_combo.setCurrentText(hover)
        self._hover_combo.currentTextChanged.connect(lambda t: self._save_setting("hover_intensity", t))
        self._hover_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("悬停效果", "鼠标悬停时播放按钮的透明度"),
            self._hover_combo
        ))

        lay.addSpacing(self.main.s(16))
        lay.addWidget(self._section_title("内容"))

        batch_row = QFrame()
        batch_row.setObjectName("SettingRow")
        batch_row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        batch_lay = QHBoxLayout(batch_row)
        batch_lay.setContentsMargins(self.main.s(12), self.main.s(8), self.main.s(12), self.main.s(8))
        batch_lay.setSpacing(self.main.s(12))
        for n in [16, 20, 24, 32]:
            btn = QPushButton(str(n))
            btn.setFixedSize(self.main.s(52), self.main.s(34))
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            btn.setObjectName("AccentButton" if n == self._get_batch_size() else "BatchButton")
            btn.clicked.connect(lambda checked, v=n, b=btn: self._set_batch_size(v, b))
            batch_lay.addWidget(btn)
            setattr(self, f"_batch_btn_{n}", btn)
        batch_lay.addStretch()

        label_widget = self._row_label("每次加载数量", "切换分类时单批拉取的影片数")
        lay.addWidget(self._option_row(label_widget, batch_row))

        lay.addStretch()
        return page

    # ── 页面：数据源（包含完整管理与删除功能） ───────────────────────
    def _build_sources_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(self.main.s(48), self.main.s(40),
                               self.main.s(48), self.main.s(40))
        lay.setSpacing(0)

        lay.addWidget(self._section_title("已配置数据源列表"))

        # 关键修正 1：将源列表包装进 SettingRow 容器，使其接入设置页的 2D 焦点链
        row_sources = QFrame()
        row_sources.setObjectName("SettingRow")
        row_sources.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row_sources_lay = QVBoxLayout(row_sources)
        row_sources_lay.setContentsMargins(self.main.s(6), self.main.s(6), self.main.s(6), self.main.s(6))

        self._sources_lw = QListWidget()
        self._sources_lw.setObjectName("SourceList")
        self._sources_lw.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._sources_lw.setFixedHeight(self.main.s(180))
        self._refresh_sources_list()

        row_sources_lay.addWidget(self._sources_lw)
        lay.addWidget(row_sources)

        lay.addSpacing(self.main.s(10))

        # 数据源操作按钮行
        btn_row = QFrame()
        btn_row.setObjectName("SettingRow")
        btn_row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_lay = QHBoxLayout(btn_row)
        btn_lay.setContentsMargins(self.main.s(12), self.main.s(8), self.main.s(12), self.main.s(8))
        btn_lay.setSpacing(self.main.s(12))

        btn_add = QPushButton("添加数据源")
        btn_add.setObjectName("BatchButton")
        btn_add.setFixedSize(self.main.s(120), self.main.s(36))
        btn_add.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_add.clicked.connect(self._add_source)

        btn_del = QPushButton("删除选中源")
        btn_del.setObjectName("DangerButton")
        btn_del.setFixedSize(self.main.s(120), self.main.s(36))
        btn_del.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_del.clicked.connect(self._delete_selected_source)

        btn_health = QPushButton("测试所有源延迟")
        btn_health.setObjectName("BatchButton")
        btn_health.setFixedSize(self.main.s(130), self.main.s(36))
        btn_health.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_health.clicked.connect(self._run_health_check)

        btn_lay.addWidget(btn_add)
        btn_lay.addWidget(btn_del)
        btn_lay.addWidget(btn_health)
        btn_lay.addStretch()

        lay.addWidget(btn_row)

        lay.addSpacing(self.main.s(16))
        lay.addWidget(self._section_title("启动设置"))

        # 默认源
        self._default_src_combo = QComboBox()
        self._default_src_combo.setFixedSize(self.main.s(220), self.main.s(32))
        self._refresh_default_src_combo()

        self._default_src_combo.currentIndexChanged.connect(
            lambda _: self._save_setting("default_source", self._default_src_combo.currentData())
        )
        self._default_src_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("默认启动源", "下次启动软件时自动切换至该数据源"),
            self._default_src_combo
        ))

        self._health_status_label = QLabel("提示：按「OK/回车」进入源列表，按「上下键」选择具体数据源")
        self._health_status_label.setStyleSheet(
            f"color: {self._colors()['text2']}; font-size: {self.main.s(12)}px; padding-top: {self.main.s(8)}px;"
        )
        lay.addWidget(self._health_status_label)

        lay.addStretch()
        return page

    # ── 数据源管理辅助方法 ──────────────────────────────────────────
    def _refresh_sources_list(self):
        self._sources_lw.clear()
        sources = ConfigManager.load_sources()
        for name, url in sources.items():
            ms = self.main.source_latency.get(name, None)
            if ms is None:
                badge = "⚪ 未检测"
            elif ms < 0:
                badge = "🔴 超时/不可用"
            elif ms < 400:
                badge = f"🟢 {ms}ms"
            elif ms < 1000:
                badge = f"🟡 {ms}ms"
            else:
                badge = f"🔴 {ms}ms 慢"

            item = QListWidgetItem(f"{name}  [{badge}]\n  {url}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setSizeHint(QSize(self.main.s(320), self.main.s(48)))
            self._sources_lw.addItem(item)
        if self._sources_lw.count() > 0 and self._sources_lw.currentRow() < 0:
            self._sources_lw.setCurrentRow(0)

    def _refresh_default_src_combo(self):
        default_src = self._get_setting("default_source", "")
        self._default_src_combo.blockSignals(True)
        self._default_src_combo.clear()
        sources = ConfigManager.load_sources()
        self._default_src_combo.addItem("（自动选择第一个）", "")
        for name in sources.keys():
            self._default_src_combo.addItem(name, name)
        idx = self._default_src_combo.findData(default_src)
        if idx >= 0:
            self._default_src_combo.setCurrentIndex(idx)
        else:
            self._default_src_combo.setCurrentIndex(0)
        self._default_src_combo.blockSignals(False)

    def _add_source(self):
        from ui.dialogs import AddSourceDialog
        dlg = AddSourceDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh_sources_list()
            self._refresh_default_src_combo()
            self.main.reload_sources()
            logging.info("数据源已更新")

    def _delete_selected_source(self):
        item = self._sources_lw.currentItem()
        if not item:
            QMessageBox.warning(self, "未选择", "请先在上方列表中选中要删除的数据源")
            return
        src_name = item.data(Qt.ItemDataRole.UserRole)
        sources = ConfigManager.load_sources()
        if len(sources) <= 1:
            QMessageBox.warning(self, "无法删除", "至少需要保留一个数据源")
            return

        reply = QMessageBox.question(
            self, "删除确认", f"确定要删除数据源《{src_name}》吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            del sources[src_name]
            ConfigManager.save_sources(sources)
            self._refresh_sources_list()
            self._refresh_default_src_combo()
            self.main.reload_sources()
            logging.info(f"已删除数据源: {src_name}")

    def _run_health_check(self):
        if not self.main.sources:
            self._health_status_label.setText("无可用数据源")
            return
        self._health_status_label.setText("正在并发测试延迟中…")
        QApplication.processEvents()
        from core.network import HealthCheckThread
        self._health_thread = HealthCheckThread(self.main.sources)
        self._health_thread.result.connect(self._on_health_result)
        self._health_thread.start()

    def _on_health_result(self, results):
        self.main.source_latency = results
        self._refresh_sources_list()
        self._health_status_label.setText("测速已完成，列表已更新延迟显示")
        self._health_status_label.setStyleSheet(
            f"color: {self._colors()['accent']}; font-size: {self.main.s(12)}px; padding-top: {self.main.s(8)}px;"
        )

    # ── 页面：系统 ─────────────────────────────────────────────────
    def _build_system_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(self.main.s(48), self.main.s(40),
                               self.main.s(48), self.main.s(40))
        lay.setSpacing(0)

        lay.addWidget(self._section_title("缓存"))
        cache_size = self._get_cache_size_mb()
        self._cache_lbl = QLabel(f"当前占用：{cache_size:.1f} MB")
        self._cache_lbl.setObjectName("RowTitleLabel")

        btn_clear = QPushButton("清除海报缓存")
        btn_clear.setObjectName("BatchButton")
        btn_clear.setFixedSize(self.main.s(140), self.main.s(36))
        btn_clear.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_clear.clicked.connect(self._clear_cache)
        lay.addWidget(self._option_row(self._cache_lbl, btn_clear))

        clean_val = self._get_setting("cache_auto_clean_mb", 500)
        spin_clean = QSpinBox()
        spin_clean.setRange(100, 2000)
        spin_clean.setSingleStep(50)
        spin_clean.setSuffix(" MB")
        spin_clean.setValue(clean_val)
        spin_clean.valueChanged.connect(lambda v: self._save_setting("cache_auto_clean_mb", v))
        spin_clean.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("自动清理阈值", "超过该大小时自动清理最旧的缓存文件"),
            spin_clean
        ))

        lay.addSpacing(self.main.s(16))
        lay.addWidget(self._section_title("网络"))
        proxy_type = self._get_setting("proxy_type", "无")
        self._proxy_type_combo = QComboBox()
        self._proxy_type_combo.setFixedSize(self.main.s(120), self.main.s(32))
        self._proxy_type_combo.addItems(["无", "HTTP", "SOCKS5"])
        self._proxy_type_combo.setCurrentText(proxy_type)
        self._proxy_type_combo.currentTextChanged.connect(
            lambda t: self._save_setting("proxy_type", t))
        self._proxy_type_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("代理类型", "无 / HTTP / SOCKS5（重启后生效）"),
            self._proxy_type_combo
        ))
        proxy_host = self._get_setting("proxy_host", "")
        self._proxy_host_edit = QLineEdit()
        self._proxy_host_edit.setPlaceholderText("例如 127.0.0.1")
        self._proxy_host_edit.setText(proxy_host)
        self._proxy_host_edit.setFixedWidth(self.main.s(160))
        self._proxy_host_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._proxy_host_edit.textChanged.connect(lambda t: self._save_setting("proxy_host", t))
        lay.addWidget(self._option_row(
            self._row_label("代理地址", "主机 IP 或域名"),
            self._proxy_host_edit
        ))
        proxy_port = self._get_setting("proxy_port", "")
        self._proxy_port_spin = QSpinBox()
        self._proxy_port_spin.setRange(1, 65535)
        self._proxy_port_spin.setFixedWidth(self.main.s(100))
        self._proxy_port_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        try:
            self._proxy_port_spin.setValue(int(proxy_port) if proxy_port else 1080)
        except Exception:
            self._proxy_port_spin.setValue(1080)
        self._proxy_port_spin.valueChanged.connect(
            lambda v: self._save_setting("proxy_port", str(v)))
        lay.addWidget(self._option_row(
            self._row_label("代理端口", "端口号"),
            self._proxy_port_spin
        ))
        timeout_val = self._get_setting("request_timeout", 8)
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(3, 30)
        self._timeout_spin.setValue(timeout_val)
        self._timeout_spin.setFixedWidth(self.main.s(80))
        self._timeout_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._timeout_spin.valueChanged.connect(
            lambda v: self._save_setting("request_timeout", v))
        lay.addWidget(self._option_row(
            self._row_label("请求超时", "网络请求超时秒数，默认 8"),
            self._timeout_spin
        ))

        lay.addSpacing(self.main.s(16))
        lay.addWidget(self._section_title("日志"))
        self._log_level_combo = QComboBox()
        self._log_level_combo.setFixedSize(self.main.s(140), self.main.s(32))
        self._log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self._log_level_combo.setCurrentText(self._get_log_level())
        self._log_level_combo.currentTextChanged.connect(self._set_log_level)
        self._log_level_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay.addWidget(self._option_row(
            self._row_label("日志保存级别", "控制 app.log 的最低记录等级"),
            self._log_level_combo
        ))

        lay.addSpacing(self.main.s(16))

        btn_reset = QPushButton("重置所有设置")
        btn_reset.setObjectName("BatchButton")
        btn_reset.setFixedSize(self.main.s(140), self.main.s(36))
        btn_reset.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_reset.clicked.connect(self._reset_settings)

        btn_export = QPushButton("导出配置")
        btn_export.setObjectName("BatchButton")
        btn_export.setFixedSize(self.main.s(100), self.main.s(36))
        btn_export.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_export.clicked.connect(self._export_config)

        btn_import = QPushButton("导入配置")
        btn_import.setObjectName("BatchButton")
        btn_import.setFixedSize(self.main.s(100), self.main.s(36))
        btn_import.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_import.clicked.connect(self._import_config)

        lay.addWidget(self._button_row(btn_reset, btn_export, btn_import, stretch=True))

        ver_lbl = QLabel("CineX OS  v0.1.0-dev")
        ver_lbl.setObjectName("RowSubLabel")
        lay.addWidget(ver_lbl)
        lay.addSpacing(self.main.s(12))

        btn_reboot = QPushButton("重启系统")
        btn_reboot.setObjectName("BatchButton")
        btn_reboot.setFixedSize(self.main.s(120), self.main.s(40))
        btn_reboot.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_reboot.clicked.connect(self._reboot)

        btn_power = QPushButton("关机")
        btn_power.setObjectName("DangerButton")
        btn_power.setFixedSize(self.main.s(100), self.main.s(40))
        btn_power.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn_power.clicked.connect(self._power_off)

        lay.addWidget(self._button_row(btn_reboot, btn_power, stretch=True))

        lay.addStretch()
        return page

    # ── 通用设置存取 ─────────────────────────────────────────────
    def _get_setting(self, key, default=None):
        ud = ConfigManager.load_user_data()
        return ud.get("settings", {}).get(key, default)

    def _save_setting(self, key, value):
        ud = ConfigManager.load_user_data()
        if "settings" not in ud:
            ud["settings"] = {}
        ud["settings"][key] = value
        ConfigManager.save_user_data(ud)

    def _get_batch_size(self):
        return self._get_setting("batch_size", 20)

    def _set_batch_size(self, value, clicked_btn):
        self._save_setting("batch_size", value)
        for n in [16, 20, 24, 32]:
            btn = getattr(self, f"_batch_btn_{n}", None)
            if btn:
                btn.setObjectName("AccentButton" if n == value else "BatchButton")
                btn.setStyle(btn.style())
        logging.info(f"每批加载数量设为 {value}")

        if hasattr(self.main, "_last_content_mode") and self.main._last_content_mode == "api":
            self.main._load_movies(mode="reset")

    def _get_log_level(self):
        return self._get_setting("log_level", "INFO")

    def _set_log_level(self, level_str):
        level_str = level_str.strip().upper()
        if level_str not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            return
        self._save_setting("log_level", level_str)
        level = getattr(logging, level_str, logging.INFO)
        logging.getLogger().setLevel(level)
        for handler in logging.getLogger().handlers:
            handler.setLevel(level)
        logging.info(f"日志级别已更改为 {level_str}")

    def _get_cache_size_mb(self):
        cache_dir = ConfigManager.POSTER_CACHE_DIR
        if not os.path.exists(cache_dir):
            return 0.0
        total = sum(
            os.path.getsize(os.path.join(cache_dir, f))
            for f in os.listdir(cache_dir)
            if os.path.isfile(os.path.join(cache_dir, f))
        )
        return total / (1024 * 1024)

    def _clear_cache(self):
        cache_dir = ConfigManager.POSTER_CACHE_DIR
        if not os.path.exists(cache_dir):
            return
        count = 0
        for f in os.listdir(cache_dir):
            path = os.path.join(cache_dir, f)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    count += 1
                except Exception:
                    pass
        self._cache_lbl.setText(f"已清除 {count} 个文件，当前：0.0 MB")
        logging.info(f"手动清除海报缓存：{count} 个文件")

    def _reset_settings(self):
        reply = QMessageBox.question(
            self, "重置确认", "确定要恢复所有设置项为默认值吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            ud = ConfigManager.load_user_data()
            ud.pop("settings", None)
            ConfigManager.save_user_data(ud)
            logging.info("所有设置已重置")
            self.close()

    def _export_config(self):
        default_filename = "cinex_config_backup.json"
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出配置文件", default_filename, "JSON 文件 (*.json)"
        )
        if filepath:
            try:
                shutil.copy2(ConfigManager.USER_DATA_FILE, filepath)
                QMessageBox.information(self, "导出成功", f"配置已导出至：\n{filepath}")
            except Exception as e:
                QMessageBox.warning(self, "导出失败", str(e))

    def _import_config(self):
        reply = QMessageBox.warning(
            self, "导入确认",
            "导入配置将覆盖当前所有用户数据（收藏、历史、设置等）。\n确定要导入吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择配置文件", "", "JSON 文件 (*.json)"
        )
        if filepath:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    import json
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("无效的配置文件")
                ConfigManager.save_user_data(data)
                QMessageBox.information(self, "导入成功", "配置已导入，建议重启程序。")
                self.close()
            except Exception as e:
                QMessageBox.warning(self, "导入失败", f"无法读取配置文件：{e}")

    def _reboot(self):
        reply = QMessageBox.question(self, "重启确认", "确定要重启系统吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if sys.platform == "win32":
                import subprocess
                subprocess.Popen(["shutdown", "/r", "/t", "0"])
            else:
                import subprocess
                subprocess.Popen(["systemctl", "reboot"])

    def _power_off(self):
        reply = QMessageBox.question(self, "关机确认", "确定要关闭系统吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if sys.platform == "win32":
                import subprocess
                subprocess.Popen(["shutdown", "/s", "/t", "0"])
            else:
                import subprocess
                subprocess.Popen(["systemctl", "poweroff"])

    # ── 字体大小调节 ─────────────────────────────────────────
    def _on_font_size_changed(self, size):
        self._save_setting("font_size", int(size))
        self._apply_font_size()

    def _apply_font_size(self):
        size = self._get_setting("font_size", 12)
        font = QApplication.font()
        font.setPointSize(int(size))
        QApplication.setFont(font)
        self.update()

    # ── 焦点管理 ───────────────────────────────────────────────
    def _on_group_changed(self, idx):
        self._stack.setCurrentIndex(idx)
        if self._current_row_focus or self._in_control_mode:
            self._exit_control_mode(keep_row_focus=False)
            self._clear_row_focus()
            self._group_list.setFocus()
        self._active_page = self._stack.currentWidget()

    def _first_row(self, widget):
        for child in widget.findChildren(QFrame):
            if child.objectName() == "SettingRow":
                return child
        return None

    def _find_all_rows(self, page):
        rows = []
        for child in page.findChildren(QFrame):
            if child.objectName() == "SettingRow":
                rows.append(child)
        return rows

    def _set_row_focus(self, row):
        self._clear_row_focus()
        self._current_row_focus = row
        if row:
            row.setProperty("focused", True)
            row.style().unpolish(row)
            row.style().polish(row)
            row.setFocus()
            row.update()

    def _clear_row_focus(self):
        if self._current_row_focus:
            self._current_row_focus.setProperty("focused", False)
            self._current_row_focus.style().unpolish(self._current_row_focus)
            self._current_row_focus.style().polish(self._current_row_focus)
            self._current_row_focus = None

    def _enter_control_mode(self, row):
        if not row:
            return
        buttons = self._get_buttons_in_row(row)
        if len(buttons) > 1:
            self._in_control_mode = True
            self._button_group_mode = True
            self._current_button_row = row
            row.setProperty("focused", False)
            row.style().unpolish(row)
            row.style().polish(row)
            buttons[0].setFocus()
            return
        first_ctrl = self._first_focusable_inner(row)
        if first_ctrl:
            self._in_control_mode = True
            self._button_group_mode = False
            row.setProperty("focused", False)
            row.style().unpolish(row)
            row.style().polish(row)
            first_ctrl.setFocus()

    def _exit_control_mode(self, keep_row_focus=False):
        if self._in_control_mode:
            self._in_control_mode = False
            self._button_group_mode = False
            if keep_row_focus and self._current_row_focus:
                self._set_row_focus(self._current_row_focus)

    def _first_focusable_inner(self, container):
        for child in container.children():
            if isinstance(child, QWidget) and child.isEnabled() and child.focusPolicy() != Qt.FocusPolicy.NoFocus:
                return child
        return None

    # 关键修正 2：增加 QListWidget 感知能力
    def _get_focusable_controls(self, row):
        controls = []
        for child in row.children():
            if isinstance(child, (QPushButton, QCheckBox, QComboBox, QSpinBox, QLineEdit, QListWidget)):
                if child.isEnabled() and child.focusPolicy() != Qt.FocusPolicy.NoFocus:
                    controls.append(child)
        return controls

    def _find_parent_row(self, widget):
        while widget:
            if isinstance(widget, QFrame) and widget.objectName() == "SettingRow":
                return widget
            widget = widget.parentWidget()
        return None

    def _get_buttons_in_row(self, row):
        buttons = []
        for child in row.children():
            if isinstance(child, QPushButton) and child.isEnabled():
                buttons.append(child)
        return buttons

    def _is_in_nav(self, widget):
        while widget:
            if widget is self._nav:
                return True
            widget = widget.parentWidget()
        return False

    def _move_to_right_first_row(self):
        page = self._stack.currentWidget()
        self._active_page = page
        first_row = self._first_row(page)
        if first_row:
            self._group_list.clearFocus()
            self._set_row_focus(first_row)

    def _move_to_nav(self):
        self._clear_row_focus()
        self._group_list.setFocus()
        self._group_list.update()

    def _move_row_down(self):
        rows = self._find_all_rows(self._stack.currentWidget())
        if not self._current_row_focus or not rows:
            return
        if self._current_row_focus in rows:
            idx = rows.index(self._current_row_focus)
            next_idx = min(idx + 1, len(rows) - 1)
            self._set_row_focus(rows[next_idx])

    def _move_row_up(self):
        rows = self._find_all_rows(self._stack.currentWidget())
        if not self._current_row_focus or not rows:
            return
        if self._current_row_focus in rows:
            idx = rows.index(self._current_row_focus)
            prev_idx = max(idx - 1, 0)
            self._set_row_focus(rows[prev_idx])

    # ── 事件过滤 ───────────────────────────────────────────────
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            fw = QApplication.focusWidget()
            if fw is None:
                return False

            in_nav = self._is_in_nav(fw)
            is_confirm = key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)

            if key == Qt.Key.Key_Escape:
                if self._in_control_mode:
                    self._exit_control_mode(keep_row_focus=True)
                    return True
                else:
                    self.close()
                    return True

            if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Back):
                if self._in_control_mode:
                    self._exit_control_mode(keep_row_focus=True)
                    return True
                return False

            if key == Qt.Key.Key_Right:
                if in_nav:
                    self._move_to_right_first_row()
                    return True
                else:
                    if self._in_control_mode and self._button_group_mode:
                        fw = QApplication.focusWidget()
                        if fw and isinstance(fw, QPushButton):
                            row = self._find_parent_row(fw)
                            if row:
                                buttons = self._get_buttons_in_row(row)
                                if buttons and fw in buttons:
                                    idx = buttons.index(fw)
                                    next_idx = (idx + 1) % len(buttons)
                                    buttons[next_idx].setFocus()
                                    return True
                    return False

            elif key == Qt.Key.Key_Left:
                if not in_nav:
                    if self._in_control_mode and self._button_group_mode:
                        fw = QApplication.focusWidget()
                        if fw and isinstance(fw, QPushButton):
                            row = self._find_parent_row(fw)
                            if row:
                                buttons = self._get_buttons_in_row(row)
                                if buttons and fw in buttons:
                                    idx = buttons.index(fw)
                                    prev_idx = (idx - 1) % len(buttons)
                                    buttons[prev_idx].setFocus()
                                    return True
                    else:
                        if not self._in_control_mode:
                            self._move_to_nav()
                            return True
                        return False
                return True

            if key == Qt.Key.Key_Down:
                if in_nav:
                    return False
                else:
                    if self._in_control_mode:
                        if self._button_group_mode:
                            self._exit_control_mode(keep_row_focus=False)
                            self._move_row_down()
                            return True
                        else:
                            return False
                    else:
                        self._move_row_down()
                        return True

            elif key == Qt.Key.Key_Up:
                if in_nav:
                    return False
                else:
                    if self._in_control_mode:
                        if self._button_group_mode:
                            self._exit_control_mode(keep_row_focus=False)
                            self._move_row_up()
                            return True
                        else:
                            return False
                    else:
                        self._move_row_up()
                        return True

            if is_confirm:
                if in_nav:
                    return False

                if not self._in_control_mode and self._current_row_focus:
                    focusable = self._get_focusable_controls(self._current_row_focus)
                    if len(focusable) == 1:
                        ctrl = focusable[0]
                        if isinstance(ctrl, QPushButton):
                            ctrl.click()
                            return True
                        elif isinstance(ctrl, QCheckBox):
                            ctrl.setChecked(not ctrl.isChecked())
                            return True
                        else:
                            self._enter_control_mode(self._current_row_focus)
                            return True
                    else:
                        self._enter_control_mode(self._current_row_focus)
                        return True

                return False

        return super().eventFilter(obj, event)

    # ── 主题 ─────────────────────────────────────────────────────
    def _apply_theme(self):
        d = self._colors()
        accent = d['accent']
        hover_bg = d.get('hover', '#333')
        card_bg = d['card_bg']
        text = d['text']
        text2 = d.get('text2', '#aaa')
        panel_bg = d.get('panel', d['bg'])
        border = d['border']
        danger = d.get('danger', '#e55')

        self._btn_back.setIcon(Theme.create_icon("back", text, self.main.s(20)))

        self.setStyleSheet(
            QApplication.instance().styleSheet() + f"""
            QWidget#SettingsPage {{
                background: {d['bg']};
            }}
            QFrame#NavPanel {{
                background: {panel_bg};
                border: none;
            }}
            #SettingsSep {{
                color: {border};
                max-width: 1px;
            }}
            #SettingsContent {{
                background: {d['bg']};
            }}

            QLabel#TopBarTitle {{
                color: {text};
                font-size: {self.main.s(20)}px;
                font-weight: 700;
            }}
            QLabel#SectionLabel {{
                color: {accent};
                font-size: {self.main.s(15)}px;
                font-weight: 700;
                padding-top: {self.main.s(12)}px;
                padding-bottom: {self.main.s(8)}px;
            }}
            QLabel#RowTitleLabel {{
                color: {text};
                font-size: {self.main.s(14)}px;
                font-weight: 500;
            }}
            QLabel#RowSubLabel {{
                color: {text2};
                font-size: {self.main.s(12)}px;
            }}

            QListWidget#CategoryList, QListWidget#CategoryList QWidget {{
                background: transparent;
                background-color: transparent;
                color: {text};
                border: none;
                outline: none;
                padding: 0px;
            }}
            QListWidget#CategoryList::item {{
                padding: 0px {self.main.s(12)}px;
                margin: {self.main.s(3)}px 0px;
                border-radius: {self.main.s(8)}px;
                color: {text2};
                background: transparent;
                border: 2px solid transparent;
                min-height: {self.main.s(40)}px;
            }}
            QListWidget#CategoryList::item:hover {{
                background: {hover_bg};
                color: {text};
            }}
            QListWidget#CategoryList::item:selected {{
                background: {hover_bg};
                color: {text};
                font-weight: bold;
                border: 2px solid transparent;
            }}
            QListWidget#CategoryList:focus::item:selected {{
                background: {accent};
                color: #ffffff;
                font-weight: bold;
                border: 2px solid {text};
                outline: none;
            }}

            QListWidget#SourceList, QListWidget#SourceList QWidget {{
                background: {card_bg};
                color: {text};
                border: none;
                outline: none;
            }}
            QListWidget#SourceList::item {{
                padding: {self.main.s(6)}px {self.main.s(12)}px;
                margin: {self.main.s(2)}px;
                border-radius: {self.main.s(6)}px;
                color: {text};
            }}
            QListWidget#SourceList::item:hover {{
                background: {hover_bg};
            }}
            QListWidget#SourceList::item:selected {{
                background: {accent};
                color: #FFFFFF;
                font-weight: bold;
            }}

            QFrame#SettingRow {{
                background: transparent;
                border: 2px solid transparent;
                border-radius: {self.main.s(10)}px;
                padding: {self.main.s(4)}px;
            }}
            QFrame#SettingRow[focused="true"] {{
                border: 2px solid {accent};
                background: {hover_bg};
            }}

            QComboBox {{
                background: {card_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: {self.main.s(6)}px;
                padding: {self.main.s(4)}px {self.main.s(12)}px;
                font-size: {self.main.s(13)}px;
            }}
            QComboBox:hover {{
                border-color: {accent};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: {self.main.s(24)}px;
                border: none;
                background: transparent;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: {self.main.s(4)}px solid transparent;
                border-right: {self.main.s(4)}px solid transparent;
                border-top: {self.main.s(5)}px solid {text2};
                width: 0px;
                height: 0px;
                margin-right: {self.main.s(8)}px;
            }}
            QComboBox QAbstractItemView {{
                background: {card_bg};
                color: {text};
                selection-background-color: {accent};
                selection-color: #ffffff;
                border: 1px solid {border};
                outline: none;
                padding: {self.main.s(4)}px;
            }}

            QLineEdit, QSpinBox {{
                background: {card_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: {self.main.s(6)}px;
                padding: {self.main.s(4)}px {self.main.s(8)}px;
                font-size: {self.main.s(13)}px;
            }}
            QLineEdit:hover, QSpinBox:hover {{
                border-color: {accent};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background: transparent;
                border: none;
                width: {self.main.s(16)}px;
            }}
            QSpinBox::up-arrow {{
                border-left: {self.main.s(3)}px solid transparent;
                border-right: {self.main.s(3)}px solid transparent;
                border-bottom: {self.main.s(4)}px solid {text2};
            }}
            QSpinBox::down-arrow {{
                border-left: {self.main.s(3)}px solid transparent;
                border-right: {self.main.s(3)}px solid transparent;
                border-top: {self.main.s(4)}px solid {text2};
            }}

            QCheckBox {{
                color: {text};
                font-size: {self.main.s(13)}px;
                spacing: {self.main.s(8)}px;
            }}
            QCheckBox::indicator {{
                width: {self.main.s(18)}px;
                height: {self.main.s(18)}px;
                border: 1px solid {border};
                border-radius: {self.main.s(4)}px;
                background: {card_bg};
            }}
            QCheckBox::indicator:checked {{
                background: {accent};
                border-color: {accent};
            }}
            QCheckBox::indicator:hover {{
                border-color: {accent};
            }}

            QPushButton#AccentButton {{
                background: {accent};
                color: #ffffff;
                border: none;
                border-radius: {self.main.s(8)}px;
                font-size: {self.main.s(13)}px;
                font-weight: 700;
            }}
            QPushButton#AccentButton:hover {{
                opacity: 0.9;
            }}
            QPushButton#BatchButton {{
                background: {card_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: {self.main.s(8)}px;
                font-size: {self.main.s(13)}px;
                font-weight: 600;
            }}
            QPushButton#BatchButton:hover {{
                border-color: {accent};
                color: {accent};
            }}
            QPushButton#DangerButton {{
                background: {danger};
                color: #ffffff;
                border: none;
                border-radius: {self.main.s(8)}px;
                font-size: {self.main.s(14)}px;
                font-weight: 700;
            }}
            QPushButton#DangerButton:hover {{
                background: {d.get('danger_hover', '#c33')};
            }}
            QPushButton#TopBarBtn {{
                background: transparent;
                border: none;
                border-radius: {self.main.s(18)}px;
            }}
            QPushButton#TopBarBtn:hover {{
                background: {hover_bg};
            }}

            QPushButton:focus {{
                border: 3px solid {accent};
                background: {accent};
                color: #ffffff;
                outline: none;
            }}
            QPushButton#BatchButton:focus {{
                background: {accent};
                color: #ffffff;
                border: 3px solid {accent};
            }}
            QPushButton#DangerButton:focus {{
                border: 3px solid {accent};
                background: {danger};
                color: #ffffff;
            }}
            QPushButton#TopBarBtn:focus {{
                border: 3px solid {accent};
                background: {hover_bg};
            }}

            QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QCheckBox:focus {{
                border: 2px solid {accent};
                outline: none;
            }}
            """
        )

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, '_group_list') and self._group_list:
            self._group_list.setFocus()
            self._clear_row_focus()
            self._active_page = self._stack.currentWidget()
