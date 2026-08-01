# ui/main_window.py
import os
import sys
import gc
import logging
import datetime
import urllib.request
import urllib.parse
import subprocess

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QFrame, QScrollArea, QGridLayout, QMessageBox,
    QDialog, QApplication, QLineEdit
)
from PyQt6.QtCore import (
    Qt, QTimer, QSize, QPointF, QRect, QEvent, QObject
)
from PyQt6.QtGui import QColor, QFont, QPixmap 

from core.config import ConfigManager, BASE_DIR

ASSETS_DIR = os.path.join(BASE_DIR, "assets")
from core.network import (
    NetworkEngine, FetchCategoriesThread, FetchMoviesThread,
    HealthCheckThread, NetworkCheckThread
)
from core.theme import Theme
from ui.widgets import SkeletonCard, MovieCard, SearchBox, SourceButton
from ui.dialogs import SourceSelectorDialog, AddSourceDialog, EpisodeDialog

logger = logging.getLogger("MainWindow")
BASE_CARD_W, BASE_CARD_H = 175, 262


class FocusRouter(QObject):
    """
    混合焦点路由器：
    - 卡片网格区域使用行列索引（grid模式），行为可预测（行内循环/跨行）
    - 网格边界（最后一行行尾/行首）降级为空间搜索，允许焦点跳出到按钮等控件
    - 非网格控件（按钮、搜索框）使用空间搜索
    - 增加 TopBar 与左侧导航栏的直觉联动
    """
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main = main_window
        self.grid_enabled = True
        self.grid = {}
        self.row_cols = {}
        self.widget_pos = {}

    def clear_grid(self):
        self.grid.clear()
        self.row_cols.clear()
        self.widget_pos.clear()

    def register_widget(self, widget, row, col):
        if not widget:
            return
        if row not in self.grid:
            self.grid[row] = {}
        self.grid[row][col] = widget
        self.widget_pos[widget] = (row, col)
        if row not in self.row_cols or col > self.row_cols[row]:
            self.row_cols[row] = col

    def unregister_widget(self, widget):
        if widget in self.widget_pos:
            row, col = self.widget_pos.pop(widget)
            if row in self.grid and col in self.grid[row]:
                del self.grid[row][col]

    def get_widget_pos(self, widget):
        return self.widget_pos.get(widget)

    def is_in_grid(self, widget):
        return widget in self.widget_pos

    def _find_next_grid(self, fw, key):
        pos = self.get_widget_pos(fw)
        if not pos:
            return None
        row, col = pos
        rows = sorted(self.grid.keys())
        if not rows:
            return None
        target_row, target_col = row, col

        if key == Qt.Key.Key_Right:
            max_col = self.row_cols.get(row, col)
            if col < max_col:
                target_col = col + 1
                while target_col <= max_col and target_col not in self.grid[row]:
                    target_col += 1
                if target_col > max_col:
                    next_row = row + 1
                    while next_row in self.grid and not self.grid[next_row]:
                        next_row += 1
                    if next_row in self.grid:
                        target_row = next_row
                        target_col = 0
                        while target_col <= self.row_cols.get(next_row, 0) and target_col not in self.grid[next_row]:
                            target_col += 1
                        if target_col > self.row_cols.get(next_row, 0):
                            return None
                    else:
                        return None
            else:
                next_row = row + 1
                while next_row in self.grid and not self.grid[next_row]:
                    next_row += 1
                if next_row in self.grid:
                    target_row = next_row
                    target_col = 0
                    while target_col <= self.row_cols.get(next_row, 0) and target_col not in self.grid[next_row]:
                        target_col += 1
                    if target_col > self.row_cols.get(next_row, 0):
                        return None
                else:
                    return None

        elif key == Qt.Key.Key_Left:
            if col > 0:
                target_col = col - 1
                while target_col >= 0 and target_col not in self.grid[row]:
                    target_col -= 1
                if target_col >= 0:
                    target = self.grid.get(row, {}).get(target_col)
                    if target and target != fw:
                        return target
            # 最左列按 Left 降级为空间搜索，切回侧边栏
            return None

        elif key == Qt.Key.Key_Down:
            next_row = row + 1
            while next_row in self.grid and not self.grid[next_row]:
                next_row += 1
            if next_row in self.grid:
                target_row = next_row
                if col in self.grid[next_row]:
                    target_col = col
                else:
                    max_col = self.row_cols.get(next_row, 0)
                    if col <= max_col:
                        for c in range(col, max_col + 1):
                            if c in self.grid[next_row]:
                                target_col = c
                                break
                        else:
                            for c in range(col - 1, -1, -1):
                                if c in self.grid[next_row]:
                                    target_col = c
                                    break
                            else:
                                return None
                    else:
                        target_col = max_col
                        if target_col not in self.grid[next_row]:
                            for c in range(target_col, -1, -1):
                                if c in self.grid[next_row]:
                                    target_col = c
                                    break
                            else:
                                return None
            else:
                return None

        elif key == Qt.Key.Key_Up:
            prev_row = row - 1
            while prev_row >= 0 and prev_row in self.grid and not self.grid[prev_row]:
                prev_row -= 1
            if prev_row >= 0 and prev_row in self.grid:
                target_row = prev_row
                if col in self.grid[prev_row]:
                    target_col = col
                else:
                    max_col = self.row_cols.get(prev_row, 0)
                    if col <= max_col:
                        for c in range(col, max_col + 1):
                            if c in self.grid[prev_row]:
                                target_col = c
                                break
                        else:
                            for c in range(col - 1, -1, -1):
                                if c in self.grid[prev_row]:
                                    target_col = c
                                    break
                            else:
                                return None
                    else:
                        target_col = max_col
                        if target_col not in self.grid[prev_row]:
                            for c in range(target_col, -1, -1):
                                if c in self.grid[prev_row]:
                                    target_col = c
                                    break
                            else:
                                return None
            else:
                return None

        target = self.grid.get(target_row, {}).get(target_col)
        if target and target != fw:
            return target
        return None

    def _find_next_space(self, fw, key):
        candidates = []

        def collect(parent):
            for child in parent.children():
                if (isinstance(child, QWidget) and child.isVisibleTo(self.main)
                        and child.isEnabled() and child != fw
                        and isinstance(child, (QPushButton, QLineEdit, QListWidget, MovieCard))):
                    candidates.append(child)
                collect(child)

        collect(self.main)
        if not candidates:
            return None

        if isinstance(fw, QListWidget) and fw.currentItem():
            rect = fw.visualItemRect(fw.currentItem())
            cg = fw.viewport().mapToGlobal(rect.center())
        else:
            cg = fw.mapToGlobal(fw.rect().center())
        cx, cy = cg.x(), cg.y()

        best, min_score = None, float("inf")
        for c in candidates:
            if isinstance(c, QListWidget) and c.count() > 0:
                item = c.currentItem() or c.item(0)
                rect = c.visualItemRect(item)
                tg = c.viewport().mapToGlobal(rect.center()) if rect.isValid() \
                     else c.mapToGlobal(c.rect().center())
            else:
                tg = c.mapToGlobal(c.rect().center())
            dx, dy = tg.x() - cx, tg.y() - cy

            if key == Qt.Key.Key_Right and dx > 0:
                score = dx + abs(dy) * 6
            elif key == Qt.Key.Key_Left and dx < 0:
                score = -dx + abs(dy) * 6
            elif key == Qt.Key.Key_Down and dy > 0:
                score = dy + abs(dx) * 6
            elif key == Qt.Key.Key_Up and dy < 0:
                score = -dy + abs(dx) * 6
            else:
                continue

            if score < min_score:
                min_score = score
                best = c
        return best

    def _find_next(self, fw, key):
        if self.grid_enabled and self.is_in_grid(fw):
            target = self._find_next_grid(fw, key)
            if target:
                return target
            return self._find_next_space(fw, key)
        else:
            return self._find_next_space(fw, key)

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.KeyPress:
            return False
        key = event.key()
        if key not in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            return False

        fw = QApplication.focusWidget()
        if not fw or fw.window() != self.main:
            return False

        # ──【核心修复】：侧边栏与 TopBar 区域边界拦截 ──
        # 1. 在「收藏」按钮上按 Up，直接精准跳到 TopBar 的数据源选择按钮 (btn_src)
        if hasattr(self.main, "btn_fav") and fw is self.main.btn_fav and key == Qt.Key.Key_Up:
            if hasattr(self.main, "btn_src") and self.main.btn_src.isVisible():
                self.main.btn_src.setFocus()
                return True

        # 2. 在 TopBar 数据源选择按钮按 Down，精准切回左侧侧边栏「收藏」按钮 (btn_fav)
        if hasattr(self.main, "btn_src") and fw is self.main.btn_src and key == Qt.Key.Key_Down:
            if hasattr(self.main, "btn_fav") and self.main.btn_fav.isVisible():
                self.main.btn_fav.setFocus()
                return True

        # QLineEdit 内光标移动逻辑
        if isinstance(fw, QLineEdit):
            if key == Qt.Key.Key_Left and fw.cursorPosition() > 0:
                return False
            if key == Qt.Key.Key_Right and fw.cursorPosition() < len(fw.text()):
                return False

        # QListWidget (分类栏) 边界逻辑
        if isinstance(fw, QListWidget) and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            if key == Qt.Key.Key_Up and fw.currentRow() > 0:
                return False
            if key == Qt.Key.Key_Down and fw.currentRow() < fw.count() - 1:
                return False

        target = self._find_next(fw, key)
        if target:
            target.setFocus()
            if hasattr(self.main, "scroll"):
                self.main.scroll.ensureWidgetVisible(target, 40, 40)
            return True
        return False


class MainWindow(QMainWindow):
    def __init__(self, is_kiosk=True):
        super().__init__()
        ConfigManager.init_env()
        ConfigManager.clean_cache(max_size_mb=500)

        self.is_dark_mode = True
        self.is_kiosk = is_kiosk

        self.ui_scale = self._calc_ui_scale()
        self.setWindowTitle("CineX OS")

        if self.is_kiosk:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
            screen = QApplication.primaryScreen()
            if screen:
                self.setGeometry(screen.geometry()) # 强制铺满物理屏幕
            self.showFullScreen()

        else:
            self.resize(self.s(1200), self.s(760))
            self.setMinimumSize(self.s(900), self.s(600))

        self.sources = {}
        self.categories = []
        self.current_api = ""
        self.current_page = 1
        self.current_type_id = None
        self.search_query = ""
        self.source_latency = {}
        self._last_content_key = None
        self._last_content_mode = None
        self.current_movies = []       # 当前已渲染的完整影片列表（用于显示）
        self.current_movie_cards = []  # 卡片复用池
        self.movie_fetch_thread = None
        self.cat_fetch_thread = None
        self.health_thread = None
        self._load_timeout = None
        self._is_loading = False
        self._has_more = True
        self._pending_movies = []      # 未展示的剩余影片（客户端截断用）

        self.card_w = int(BASE_CARD_W * self.ui_scale)
        self.card_h = int(BASE_CARD_H * self.ui_scale)

        self.network_ok = False
        self.network_timer = QTimer(self)
        self.network_timer.timeout.connect(self._check_network)
        self.network_timer.start(30000)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self._on_resize_finished)

        self.focus_router = FocusRouter(self)
        QApplication.instance().installEventFilter(self.focus_router)

        QApplication.instance().setStyleSheet(Theme.get_stylesheet(self.is_dark_mode, self.ui_scale))
        self._init_ui()
        self.reload_sources()

        # 启动默认源
        ud = ConfigManager.load_user_data()
        default_src = ud.get("settings", {}).get("default_source", "")
        if default_src and default_src in self.sources:
            logger.info(f"切换至默认源: {default_src}")
            self._change_source(default_src)

        self._run_health_check()
        self._check_network()
        logger.info("MainWindow initialized (kiosk=%s)", self.is_kiosk)

    def _calc_ui_scale(self):
            screen = QApplication.primaryScreen()
            if not screen:
                return 1.0
            # 改用 geometry().height() 准确获取物理高度
            h = screen.geometry().height()
            scale = h / 1080.0
            return max(0.75, min(2.0, scale))

    def s(self, px_val):
        return max(1, int(px_val * self.ui_scale))

    # ── UI 构建 ──────────────────────────────────────────────
    def _init_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ========== Top Bar ==========
        self.top_bar = QFrame()
        self.top_bar.setObjectName("TopBar")
        self.top_bar.setFixedHeight(self.s(52))  # 高度微调到 52px，更加大气
        top_bar_layout = QHBoxLayout(self.top_bar)
        top_bar_layout.setContentsMargins(self.s(18), 0, self.s(18), 0)

        # 1. 放大 Logo 图标至 32x32，展示青光细节
        self._logo_mark = QLabel()
        self._logo_mark.setFixedSize(self.s(32), self.s(32))
        self._logo_mark.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        logo_file = os.path.join(ASSETS_DIR, "logo_pure.png")
        if not os.path.exists(logo_file):
            logo_file = os.path.join(ASSETS_DIR, "logo_pure_transparent.png")
        if not os.path.exists(logo_file):
            logo_file = os.path.join(ASSETS_DIR, "logo.png")

        if os.path.exists(logo_file):
            pix = QPixmap(logo_file).scaled(
                self.s(32), self.s(32),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self._logo_mark.setPixmap(pix)

        top_bar_layout.addWidget(self._logo_mark)
        top_bar_layout.addSpacing(self.s(10))

        # 2. 双色艺术字标题 (纯白 Cine + 青发光 X + 哑光灰 OS)
        self.title_lbl = QLabel()
        self.title_lbl.setObjectName("TopBarTitle")
        self.title_lbl.setText(
            f"<span style='font-size:{self.s(18)}px; font-weight:800; color:#FFFFFF;'>Cine</span>"
            f"<span style='font-size:{self.s(21)}px; font-weight:900; color:#00C2D1;'>X</span>"
            f"<span style='font-size:{self.s(12)}px; font-weight:700; color:#8CA0B0;'>&nbsp;OS</span>"
        )
        top_bar_layout.addWidget(self.title_lbl)
        top_bar_layout.addSpacing(self.s(10))

        top_bar_layout.addStretch()


        # 网络状态指示器
        self.network_dot = QLabel()
        self.network_dot.setFixedSize(self.s(12), self.s(12))
        self.network_dot.setStyleSheet(f"border-radius: {self.s(6)}px; background-color: #888;")
        self.network_dot.setToolTip("网络状态")
        self.network_dot.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        top_bar_layout.addWidget(self.network_dot)
        top_bar_layout.addSpacing(self.s(10))

        # 源切换按钮
        self.btn_src = SourceButton("未选择")
        self.btn_src.setObjectName("SourceButton")
        self.btn_src.setFixedSize(self.s(130), self.s(32))
        self.btn_src.clicked.connect(self._open_source_selector)
        self.btn_src.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        top_bar_layout.addWidget(self.btn_src)
        top_bar_layout.addSpacing(self.s(10))

        # 时间
        self.time_label = QLabel()
        self.time_label.setObjectName("TopBarTime")
        self._update_time()
        time_timer = QTimer(self)
        time_timer.timeout.connect(self._update_time)
        time_timer.start(10000)
        top_bar_layout.addWidget(self.time_label)

        top_bar_layout.addSpacing(self.s(16))

        # 设置按钮
        btn_sz = QSize(self.s(36), self.s(36))
        icon_sz = QSize(self.s(20), self.s(20))
        self.btn_settings = QPushButton()
        self.btn_settings.setObjectName("TopBarBtn")
        self.btn_settings.setFixedSize(btn_sz)
        self.btn_settings.setIconSize(icon_sz)
        self.btn_settings.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_settings.setToolTip("设置")
        self.btn_settings.clicked.connect(self._open_settings)
        top_bar_layout.addWidget(self.btn_settings)

        root_lay.addWidget(self.top_bar)

        # ========== 主内容区 ==========
        content = QWidget()
        content_lay = QHBoxLayout(content)
        content_lay.setContentsMargins(self.s(16), self.s(16), self.s(16), self.s(16))
        content_lay.setSpacing(self.s(16))

        # 左侧导航面板
        nav = QFrame()
        nav.setObjectName("NavPanel")
        nav.setFixedWidth(self.s(200))
        nav_lay = QVBoxLayout(nav)
        nav_lay.setContentsMargins(self.s(10), self.s(14), self.s(10), self.s(14))
        nav_lay.setSpacing(self.s(6))

        lbl_lib = QLabel("我的片库")
        lbl_lib.setObjectName("SectionLabel")
        nav_lay.addWidget(lbl_lib)

        self.btn_fav = QPushButton(" 收藏")
        self.btn_fav.setObjectName("NavButton")
        self.btn_fav.setCheckable(True)
        self.btn_fav.setAutoExclusive(True)
        self.btn_fav.clicked.connect(self._nav_fav)
        nav_lay.addWidget(self.btn_fav)

        self.btn_hist = QPushButton(" 最近")
        self.btn_hist.setObjectName("NavButton")
        self.btn_hist.setCheckable(True)
        self.btn_hist.setAutoExclusive(True)
        self.btn_hist.clicked.connect(self._nav_hist)
        nav_lay.addWidget(self.btn_hist)

        nav_lay.addSpacing(self.s(10))
        lbl_cat = QLabel("分类")
        lbl_cat.setObjectName("SectionLabel")
        nav_lay.addWidget(lbl_cat)

        self.cat_list = QListWidget()
        self.cat_list.setObjectName("CategoryList")
        self.cat_list.setIconSize(icon_sz)
        self.cat_list.itemClicked.connect(self._on_cat_click)
        self.cat_list.itemActivated.connect(self._on_cat_click)   # 键盘回车/空格支持
        nav_lay.addWidget(self.cat_list)

        content_lay.addWidget(nav)

        # 右侧核心区域
        right = QVBoxLayout()
        right.setSpacing(self.s(14))

        # 顶部仅保留搜索框（源切换已移至 Top Bar，健康检测和添加源按钮已移至设置页）
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(self.s(8))
        top.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        top.addStretch()

        self.search = SearchBox()
        self.search.setFixedWidth(self.s(230))
        self.search.search_triggered.connect(self._on_search)
        top.addWidget(self.search)

        right.addLayout(top)

        # 视频网格区域
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background:transparent;")
        self.grid = QGridLayout(self.scroll_content)
        self.grid.setSpacing(self.s(14))
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        right.addWidget(self.scroll)

        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

        self.lbl_load_status = QLabel("")
        self.lbl_load_status.setObjectName("PageLabel")
        self.lbl_load_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_load_status.setFixedHeight(self.s(32))
        right.addWidget(self.lbl_load_status)

        content_lay.addLayout(right)
        root_lay.addWidget(content)

        # 设置焦点策略（仅保留存在的按钮）
        for btn in [self.btn_fav, self.btn_hist, self.btn_src]:
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._refresh_icons()
        self.cat_list.setFocus()

    # ── Top Bar 响应 ──────────────────────────────────────────
    def _update_time(self):
        now = datetime.datetime.now().strftime("%H:%M")
        self.time_label.setText(now)

    def _check_network(self):
        self._net_check_thread = NetworkCheckThread()
        self._net_check_thread.result.connect(self._on_network_result)
        self._net_check_thread.start()

    def _on_network_result(self, ok: bool):
        self.network_ok = ok
        color = "#5ab865" if ok else "#ff6b6b"
        self.network_dot.setStyleSheet(f"border-radius: {self.s(6)}px; background-color: {color};")
        self.network_dot.setToolTip("网络正常" if ok else "网络断开")

    # ── 图标与主题 ────────────────────────────────────────────
    def _refresh_icons(self):
        d = Theme.DARK if self.is_dark_mode else Theme.LIGHT
        ic = d["accent"]
        tx = d["text"]
        sz = QSize(self.s(20), self.s(20))
        self.btn_settings.setIcon(Theme.create_icon("settings", tx, self.s(20)))
        self.btn_settings.setIconSize(sz)
        self.btn_fav.setIcon(Theme.create_icon("star" if self.btn_fav.isChecked() else "star_o", ic, self.s(20)))
        self.btn_fav.setIconSize(sz)
        self.btn_hist.setIcon(Theme.create_icon("clock", ic, self.s(20)))
        self.btn_hist.setIconSize(sz)
        # 已移除 btn_add 和 btn_health 的图标设置
        for i in range(self.cat_list.count()):
            item = self.cat_list.item(i)
            if item and "..." not in item.text() and "失败" not in item.text():
                item.setIcon(Theme.create_icon("film", ic, self.s(20)))

    def _update_logo_mark(self):
        d = Theme.DARK if self.is_dark_mode else Theme.LIGHT
        self._logo_mark.setStyleSheet(f"background: {d['accent']}; border-radius: {self.s(2)}px;")

    def _toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        QApplication.instance().setStyleSheet(Theme.get_stylesheet(self.is_dark_mode, self.ui_scale))
        self._update_logo_mark()
        self._refresh_icons()
        for i in range(self.grid.count()):
            w = self.grid.itemAt(i).widget()
            if isinstance(w, MovieCard):
                w.is_dark = self.is_dark_mode
                w.update()
            elif isinstance(w, SkeletonCard):
                w.is_dark = self.is_dark_mode
                w.update()

    def _open_settings(self):
        from ui.settings_page import SettingsPage
        page = SettingsPage(self)
        page.showFullScreen()

    # ── 数据源处理 ──────────────────────────────────────────
    def reload_sources(self):
        self.sources = ConfigManager.load_sources()
        if self.sources:
            first_source = list(self.sources.keys())[0]
            logger.info("Loaded %d sources, switching to '%s'", len(self.sources), first_source)
            self._change_source(first_source)
        else:
            logger.warning("No sources configured")

    def _add_source(self):                                          # 保留以兼容外部调用，但 UI 入口已移至设置页
        dlg = AddSourceDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload_sources()
            self._run_health_check()

    def _open_source_selector(self):
        dlg = SourceSelectorDialog(
            self, list(self.sources.keys()), self.btn_src.text(), self.source_latency)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected = dlg.get_selected()
            if selected:
                logger.info("User selected source '%s'", selected)
                self._change_source(selected)

    def _change_source(self, name):
        if not name or name not in self.sources:
            return
        self.current_api = self.sources[name]
        self.btn_src.setText(name)
        self._last_content_key = None
        self._last_content_mode = None
        self.cat_list.clear()
        self.cat_list.addItem("正在连接...")
        self._clear_grid()
        # 健康检测点已移除此处 UI 更新，仅更新内部延迟数据

        self._abort_thread("cat_fetch_thread")
        url = NetworkEngine.get_api_url(self.current_api, "ac=list")
        logger.info("Fetching categories from source '%s'", name)
        self.cat_fetch_thread = FetchCategoriesThread(url)
        self.cat_fetch_thread.finished.connect(self._on_cats)
        self.cat_fetch_thread.start()

    def _on_cats(self, data):
        self.cat_list.clear()
        if not data or "class" not in data:
            self.cat_list.addItem("连接失败")
            self.lbl_load_status.setText("数据源无法连接，请检测或更换")
            logger.error("Failed to fetch categories: %s", data)
            return
        self.categories = [c for c in data["class"]
                           if not any(w in c["type_name"]
                                      for w in ["伦理", "福利", "美女", "写真", "情色"])]
        for c in self.categories:
            item = QListWidgetItem(c["type_name"])
            self.cat_list.addItem(item)
        if self.categories:
            self.cat_list.setCurrentRow(0)
            self._on_cat_click(self.cat_list.currentItem())
        self._refresh_icons()
        logger.info("Categories loaded: %d", len(self.categories))

    # ── 导航点击 ─────────────────────────────────────────────
    def _nav_fav(self):
        self.btn_fav.setChecked(True)
        self.btn_hist.setChecked(False)
        self.cat_list.clearSelection()
        self.search_query = ""
        self.search.clear()
        self.current_type_id = None
        self._load_favs()
        self._refresh_icons()

    def _nav_hist(self):
        self.btn_fav.setChecked(False)
        self.btn_hist.setChecked(True)
        self.cat_list.clearSelection()
        self.search_query = ""
        self.search.clear()
        self.current_type_id = None
        self._load_hist()
        self._refresh_icons()

    def _on_cat_click(self, item):
        if not item or "..." in item.text() or "失败" in item.text():
            return
        self.btn_fav.setChecked(False)
        self.btn_hist.setChecked(False)
        self.search_query = ""
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._refresh_icons()
        idx = self.cat_list.row(item)
        if 0 <= idx < len(self.categories):
            self.current_type_id = self.categories[idx]["type_id"]
            logger.info("Category clicked: '%s' (type_id=%s)", item.text(), self.current_type_id)
            self._load_movies(mode="reset")

    def _on_search(self, query):
        self.search_query = query
        self.cat_list.clearSelection()
        self.btn_fav.setChecked(False)
        self.btn_hist.setChecked(False)
        self._refresh_icons()
        self.current_type_id = None
        logger.info("Search: '%s'", query)
        self._load_movies(mode="reset")

# ── 布局计算与渲染 ────────────────────────────────
    def _get_card_size(self):
        return self.s(BASE_CARD_W), self.s(BASE_CARD_H)

    def _calc_cols(self):
        w, _ = self._get_card_size()
        viewport_w = self.scroll.viewport().width()
        return max(2, viewport_w // (w + self.s(14)))

    def _clear_grid(self):
        for i in reversed(range(self.grid.count())):
            w = self.grid.itemAt(i).widget()
            if w:
                if isinstance(w, SkeletonCard):
                    w.stop()
                self.grid.removeWidget(w)
                w.setParent(None)
        self.focus_router.clear_grid()
        gc.collect()

    # ── 骨架屏与视口自动填满 ───────────────────────────
    def _show_skeletons(self, n=None):
        if n is None:
            n = self._get_batch_size_setting()
        self._clear_grid()
        self.current_movie_cards.clear()
        self.card_w, self.card_h = self._get_card_size()
        cols = self._calc_cols()
        for i in range(n):
            sk = SkeletonCard(self.is_dark_mode, self.card_w, self.card_h)
            self.grid.addWidget(sk, i // cols, i % cols)

    def _check_fill_viewport(self):
        """检查内容是否填满视口，若未填满（或已滚动到底部）且有更多数据，则自动加载下一批"""
        if self._is_loading or self._last_content_mode != "api":
            return

        bar = self.scroll.verticalScrollBar()
        is_not_filled = (bar.maximum() <= 0)
        is_near_bottom = (bar.maximum() > 0 and bar.value() >= bar.maximum() - bar.pageStep() * 2)

        if is_not_filled or is_near_bottom:
            if self._pending_movies:
                self._is_loading = True
                QTimer.singleShot(0, self._process_pending_append)
            elif self._has_more:
                self._load_movies(mode="append")

    def _on_scroll(self, value):
        self._check_fill_viewport()

    # ── 卡片渲染 ──────────────────────────────────────
    def _render_cards(self, movies):
        from core.poster import PosterManager
        PosterManager.get().clear_queue()
        logger.info("Rendering %d movies", len(movies))
        self.current_movies = movies
        if not movies:
            self._clear_grid()
            self.current_movie_cards.clear()
            return

        self.card_w, self.card_h = self._get_card_size()
        cols = self._calc_cols()

        for i in reversed(range(self.grid.count())):
            w = self.grid.itemAt(i).widget()
            if w:
                self.grid.removeWidget(w)
                w.setParent(None)
        self.focus_router.clear_grid()

        card_count = len(movies)
        current_count = len(self.current_movie_cards)

        if card_count > current_count:
            logger.debug("Creating %d new cards", card_count - current_count)
            for i in range(current_count, card_count):
                m = movies[i]
                row_idx = i // cols
                priority = min(100, row_idx * 20)
                card = MovieCard(m, self.is_dark_mode, self.card_w, self.card_h,
                                 poster_priority=priority)
                card.clicked.connect(self._on_card_click)
                self.current_movie_cards.append(card)
        elif card_count < current_count:
            logger.debug("Removing %d excess cards", current_count - card_count)
            for i in range(card_count, current_count):
                card = self.current_movie_cards[i]
                try:
                    card.clicked.disconnect()
                except Exception:
                    pass
                card.deleteLater()
            self.current_movie_cards = self.current_movie_cards[:card_count]

        ud = ConfigManager.load_user_data()
        for idx, (movie, card) in enumerate(zip(movies, self.current_movie_cards)):
            row_idx = idx // cols
            priority = min(100, row_idx * 20)

            if card.movie_data != movie:
                card.movie_data = movie
                card._progress_ep = ud["progress"].get(
                    str(movie.get("vod_id", "")), {}
                ).get("ep_name", "")
                card._original_pixmap = None
                card._scaled_pixmap = None
                card._init_poster(movie.get("vod_pic", ""), priority=priority)
                card.update()
            card.set_card_size(self.card_w, self.card_h)
            self.grid.addWidget(card, row_idx, idx % cols)
            self.focus_router.register_widget(card, row_idx, idx % cols)

    def _append_cards(self, new_movies):
        if not new_movies:
            return
        cols = self._calc_cols()
        existing_count = len(self.current_movies)
        self.current_movies = self.current_movies + new_movies

        ud = ConfigManager.load_user_data()
        for idx, movie in enumerate(new_movies):
            abs_idx = existing_count + idx
            row_idx = abs_idx // cols
            col_idx = abs_idx % cols
            priority = min(100, row_idx * 20)
            card = MovieCard(movie, self.is_dark_mode, self.card_w, self.card_h,
                             poster_priority=priority)
            card._progress_ep = ud["progress"].get(
                str(movie.get("vod_id", "")), {}).get("ep_name", "")
            card.clicked.connect(self._on_card_click)
            self.current_movie_cards.append(card)
            self.grid.addWidget(card, row_idx, col_idx)
            self.focus_router.register_widget(card, row_idx, col_idx)

    # ── 本地片库加载 ──────────────────────────────────
    def _load_favs(self):
        self._last_content_mode = "favorites"
        self._is_loading = False
        self._has_more = False
        self._pending_movies.clear()
        ud = ConfigManager.load_user_data()
        movies = ud.get("favorites", [])
        if not movies:
            self._clear_grid()
            self.current_movie_cards.clear()
            self.lbl_load_status.setText("暂无收藏")
            return
        self._render_cards(movies)
        self.lbl_load_status.setText(f"共 {len(movies)} 部收藏")

    def _load_hist(self):
        self._last_content_mode = "history"
        self._is_loading = False
        self._has_more = False
        self._pending_movies.clear()
        ud = ConfigManager.load_user_data()
        movies = ud.get("history", [])
        if not movies:
            self._clear_grid()
            self.current_movie_cards.clear()
            self.lbl_load_status.setText("暂无记录")
            return
        self._render_cards(movies)
        self.lbl_load_status.setText(f"共 {len(movies)} 条记录")

    # ── 数据加载与 API 累加器 ─────────────────────────
    def _load_movies(self, mode="reset"):
        if self._is_loading:
            return
        if mode == "append" and not self._has_more and not self._pending_movies:
            return

        self._load_mode = mode
        if mode == "reset":
            self.current_page = 1
            self._has_more = True
            self._pending_movies.clear()
            self._raw_fetched_movies = []
            self._show_skeletons()
            self.lbl_load_status.setText("加载中…")
        else:
            if self._pending_movies:
                batch_size = self._get_batch_size_setting()
                next_batch = self._pending_movies[:batch_size]
                self._pending_movies = self._pending_movies[batch_size:]
                self._append_cards(next_batch)
                if not self._pending_movies and not self._has_more:
                    self.lbl_load_status.setText("已加载全部内容")
                else:
                    self.lbl_load_status.setText("")
                QTimer.singleShot(100, self._check_fill_viewport)
                return
            else:
                self.current_page += 1
                self._raw_fetched_movies = []
                self.lbl_load_status.setText("加载更多…")

        self._is_loading = True
        self._fetch_api_page()

    def _fetch_api_page(self):
        """向 API 发起单页请求"""
        if self._load_timeout and self._load_timeout.isActive():
            self._load_timeout.stop()
        self._load_timeout = QTimer(self)
        self._load_timeout.setSingleShot(True)
        self._load_timeout.timeout.connect(self._on_load_timeout)
        self._load_timeout.start(8000)

        self._last_content_mode = "api"

        if self.search_query:
            url = NetworkEngine.get_api_url(
                self.current_api,
                f"ac=detail&wd={urllib.parse.quote(self.search_query)}&pg={self.current_page}")
        elif self.current_type_id is not None:
            url = NetworkEngine.get_api_url(
                self.current_api,
                f"ac=detail&t={self.current_type_id}&pg={self.current_page}")
        else:
            self._is_loading = False
            return

        self._abort_thread("movie_fetch_thread")
        logger.info("Fetching API page (page=%d): %s", self.current_page, url)
        self.movie_fetch_thread = FetchMoviesThread(url)
        self.movie_fetch_thread.finished.connect(self._on_movies)
        self.movie_fetch_thread.start()

    def _on_load_timeout(self):
        self._is_loading = False
        self.lbl_load_status.setText("⚠ 加载超时，向下滚动重试")
        logger.warning("Loading movies timed out")

    def _on_movies(self, data):
        if self._load_timeout and self._load_timeout.isActive():
            self._load_timeout.stop()

        mode = getattr(self, "_load_mode", "reset")

        if not data or "list" not in data or not data["list"]:
            logger.info("No movies returned (mode=%s)", mode)
            self._is_loading = False
            self._has_more = False
            if hasattr(self, "_raw_fetched_movies") and self._raw_fetched_movies:
                self._flush_fetched_movies()
            elif mode == "reset":
                self._clear_grid()
                self.current_movie_cards.clear()
                self.current_movies = []
                self.lbl_load_status.setText("暂无内容")
            else:
                self.lbl_load_status.setText("已加载全部内容")
            return

        movies = data["list"]
        logger.info("Received %d movies from API (page=%d)", len(movies), self.current_page)

        # 判断 API 是否还有更多页
        pagecount = int(data.get("pagecount", 0))
        pg = int(data.get("page", self.current_page))
        if pagecount > 0:
            self._has_more = (pg < pagecount)
        else:
            self._has_more = (len(movies) > 0)

        if not hasattr(self, "_raw_fetched_movies"):
            self._raw_fetched_movies = []
        self._raw_fetched_movies.extend(movies)

        batch_size = self._get_batch_size_setting()

        # 如果 API 单页返回数量不足设定的 batch_size（如 API 每次给20，设置要求32），自动请求下一页累加！
        if len(self._raw_fetched_movies) < batch_size and self._has_more:
            self.current_page += 1
            logger.info("Accumulating batch: fetched %d/%d, fetching page %d...",
                        len(self._raw_fetched_movies), batch_size, self.current_page)
            self._fetch_api_page()
            return

        # 数量已经凑够设定的 batch_size，开始渲染
        self._is_loading = False
        self._flush_fetched_movies()

    def _flush_fetched_movies(self):
        """将累加凑齐的影片渲染到界面上"""
        batch_size = self._get_batch_size_setting()
        mode = getattr(self, "_load_mode", "reset")
        all_fetched = getattr(self, "_raw_fetched_movies", [])
        self._raw_fetched_movies = []

        if mode == "reset":
            if len(all_fetched) > batch_size:
                self._pending_movies = all_fetched[batch_size:]
                display = all_fetched[:batch_size]
            else:
                self._pending_movies = []
                display = all_fetched
            self._render_cards(display)
            self.lbl_load_status.setText("")
            QTimer.singleShot(50, lambda: self.scroll.verticalScrollBar().setValue(0))
        else:
            if len(all_fetched) > batch_size:
                self._pending_movies = all_fetched[batch_size:]
                display = all_fetched[:batch_size]
            else:
                display = all_fetched
            self._append_cards(display)

            if not self._pending_movies and not self._has_more:
                self.lbl_load_status.setText("已加载全部内容")
            else:
                self.lbl_load_status.setText("")

        QTimer.singleShot(100, self._check_fill_viewport)

    def _process_pending_append(self):
        if not self._pending_movies:
            self._is_loading = False
            self.lbl_load_status.setText("已加载全部内容")
            return
        batch_size = self._get_batch_size_setting()
        next_batch = self._pending_movies[:batch_size]
        self._pending_movies = self._pending_movies[batch_size:]
        self._append_cards(next_batch)
        self._is_loading = False

        if not self._pending_movies and not self._has_more:
            self.lbl_load_status.setText("已加载全部内容")
        else:
            self.lbl_load_status.setText("")

        QTimer.singleShot(100, self._check_fill_viewport)

    def _get_batch_size_setting(self):
        ud = ConfigManager.load_user_data()
        return ud.get("settings", {}).get("batch_size", 20)

    def _on_card_click(self, movie_data):
        from ui.detail_page import DetailPage
        self._detail_page = DetailPage(self, movie_data, self.current_api)
        self._detail_page.showFullScreen()

    def _on_resize_finished(self):
        if self.current_movies:
            self._render_cards(self.current_movies)
            QTimer.singleShot(100, self._check_fill_viewport)

    def resizeEvent(self, e):
            super().resizeEvent(e)
            if hasattr(self, '_resize_timer') and self._resize_timer:
                self._resize_timer.start()

    # ── 健康检测（简化版，仅更新延迟数据，UI 交给设置页） ──
    def _run_health_check(self):
        if not self.sources:
            return
        self._abort_thread("health_thread")
        self.health_thread = HealthCheckThread(self.sources)
        self.health_thread.result.connect(self._on_health)
        self.health_thread.start()

    def _on_health(self, results):
        self.source_latency = results
        logger.info("Health check completed: %s", results)

    # ── 按键事件 ──────────────────────────────────────────
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
                self.setWindowFlags(Qt.WindowType.Window)
                self.show()
            else:
                self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
                self.showFullScreen()
            event.accept()
            return

        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Q:
            self.close()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape:
            if self.focusWidget() and isinstance(self.focusWidget(), QDialog):
                self.focusWidget().close()
            else:
                self.search.clear()
                self.search_query = ""
                if self.cat_list.count() > 0:
                    self.cat_list.setCurrentRow(0)
                    self._on_cat_click(self.cat_list.currentItem())
            event.accept()
            return

        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        self.search.clear()
        self.search_query = ""
        if self.cat_list.count() > 0:
            self.cat_list.setCurrentRow(0)
            self._on_cat_click(self.cat_list.currentItem())
        event.accept()

    def _abort_thread(self, attr):
        t = getattr(self, attr, None)
        if t and t.isRunning():
            try:
                t.finished.disconnect()
            except:
                pass
            try:
                t.result.disconnect()
            except:
                pass
            t.request_abort()
        setattr(self, attr, None)

    def closeEvent(self, e):
        self._abort_thread("movie_fetch_thread")
        self._abort_thread("cat_fetch_thread")
        self._abort_thread("health_thread")
        if self._load_timeout and self._load_timeout.isActive():
            self._load_timeout.stop()
        for card in self.current_movie_cards:
            try:
                card.clicked.disconnect()
            except:
                pass
            card.deleteLater()
        self.current_movie_cards.clear()
        e.accept()
