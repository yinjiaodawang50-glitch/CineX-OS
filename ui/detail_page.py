# ui/detail_page.py
"""
CineX OS – 影片详情全屏页（现代影院 UI + 完整 2D 遥控焦点 + 内嵌播放器对接）
"""

import logging
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QFrame, QListWidget, QListWidgetItem, QStackedWidget,
    QApplication, QMessageBox, QButtonGroup, QScrollArea,
    QGraphicsDropShadowEffect, QSizePolicy
)
from PyQt6.QtCore import Qt, QSize, QEvent, QTimer
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont

from core.config import ConfigManager
from core.network import NetworkEngine, SafeThread
from core.theme import Theme
from core.poster import PosterManager

logger = logging.getLogger("DetailPage")


class DetailPage(QWidget):
    def __init__(self, main_window, movie_data, current_api):
        super().__init__(parent=None)
        self.main = main_window
        self.movie_data = movie_data
        self.api = movie_data.get("api", current_api)
        self.vod_id = str(movie_data["vod_id"])
        self.title_text = movie_data.get("vod_name", "未知")
        self.is_dark = main_window.is_dark_mode
        self.routes = []
        self._playing = False
        self._poster_url = None
        self._extra = {}
        self._load_thread = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("DetailPage")
        


        # 1. 优先绘制 UI 框架，显示加载状态（主线程零卡顿）
        self._build_ui_skeleton()
        self._apply_theme()

        # 2. 异步海报请求
        self._poster_url = movie_data.get("vod_pic", "")
        if self._poster_url:
            PosterManager.get().request(
                self._poster_url,
                self._on_poster_ready,
                priority=0
            )

        # 3. 异步获取详情数据（解决进入详情页卡死问题）
        self._fetch_detail_async()

            # 强制铺满物理屏幕
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        self.showFullScreen()

    def _colors(self):
        return Theme.DARK if self.is_dark else Theme.LIGHT

    def _build_ui_skeleton(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(self.main.s(48), self.main.s(48), self.main.s(48), self.main.s(48))
        root.setSpacing(self.main.s(40))

        # ── 左侧面板（海报 + 主操作） ──
        left = QVBoxLayout()
        left.setSpacing(self.main.s(20))
        left.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        poster_width = self.main.s(300)
        poster_height = int(poster_width * 1.48)
        self._poster_size = (poster_width, poster_height)

        poster_frame = QFrame()
        poster_frame.setObjectName("PosterContainer")
        poster_frame.setFixedSize(poster_width, poster_height)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(self.main.s(40))
        shadow.setOffset(0, self.main.s(12))
        shadow.setColor(QColor(0, 0, 0, 180))
        poster_frame.setGraphicsEffect(shadow)

        self._poster_label = QLabel()
        self._poster_label.setFixedSize(poster_width, poster_height)
        self._poster_label.setScaledContents(True)
        self._poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_poster_placeholder()

        poster_layout = QVBoxLayout(poster_frame)
        poster_layout.setContentsMargins(0, 0, 0, 0)
        poster_layout.addWidget(self._poster_label)

        left.addWidget(poster_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        # 按钮区
        self._btn_play = QPushButton("▶   立即播放")
        self._btn_play.setObjectName("PlayButton")
        self._btn_play.setFixedSize(poster_width, self.main.s(52))
        self._btn_play.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._btn_play.setEnabled(False)  # 加载完成前禁用
        self._btn_play.clicked.connect(self._play_default)

        self._btn_fav = QPushButton()
        self._btn_fav.setObjectName("FavButton")
        self._btn_fav.setFixedSize(poster_width, self.main.s(46))
        self._btn_fav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._update_fav_text()
        self._btn_fav.clicked.connect(self._toggle_fav)

        left.addWidget(self._btn_play, alignment=Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self._btn_fav, alignment=Qt.AlignmentFlag.AlignCenter)
        left.addStretch()

        left_widget = QWidget()
        left_widget.setLayout(left)

        # ── 右侧面板（影视信息 + 剧集选择） ──
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if right_scroll.verticalScrollBar():
            right_scroll.verticalScrollBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        right_scroll.setObjectName("RightScroll")

        self._right_content = QWidget()
        self._right_content.setObjectName("RightContent")
        self._right_layout = QVBoxLayout(self._right_content)
        self._right_layout.setSpacing(self.main.s(20))
        self._right_layout.setContentsMargins(self.main.s(10), 0, self.main.s(10), 0)

        # 标题
        self._title_label = QLabel(self.title_text)
        self._title_label.setObjectName("DetailTitle")
        self._title_label.setWordWrap(True)
        self._right_layout.addWidget(self._title_label)

        # 元数据标签容器
        self._meta_box = QHBoxLayout()
        self._meta_box.setSpacing(self.main.s(10))
        self._right_layout.addLayout(self._meta_box)

        # 演职人员标签
        self._cast_label = QLabel("正在加载影片信息...")
        self._cast_label.setObjectName("DetailCast")
        self._cast_label.setWordWrap(True)
        self._right_layout.addWidget(self._cast_label)

        # 简介卡片
        self._desc_card = QFrame()
        self._desc_card.setObjectName("DescCard")
        self._desc_layout = QVBoxLayout(self._desc_card)
        self._desc_layout.setContentsMargins(self.main.s(18), self.main.s(14), self.main.s(18), self.main.s(14))

        desc_title = QLabel("剧情简介")
        desc_title.setObjectName("SubSectionTitle")
        self._desc_layout.addWidget(desc_title)

        self._desc_label = QLabel("数据加载中，请稍候...")
        self._desc_label.setObjectName("DetailDesc")
        self._desc_label.setWordWrap(True)
        self._desc_layout.addWidget(self._desc_label)

        self._right_layout.addWidget(self._desc_card)

        # 线路与剧集选择区
        self._ep_header = QHBoxLayout()
        self._ep_title = QLabel("选择播放源")
        self._ep_title.setObjectName("SectionTitle")
        self._ep_header.addWidget(self._ep_title)
        self._right_layout.addLayout(self._ep_header)

        # 线路 Tab 按钮容器
        self._route_buttons = []
        self._route_btn_layout = QHBoxLayout()
        self._route_btn_layout.setSpacing(self.main.s(10))
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)
        self._right_layout.addLayout(self._route_btn_layout)

        # 剧集网格 Stack
        self._ep_stacks = QStackedWidget()
        self._ep_stacks.setObjectName("EpisodeStack")
        self._right_layout.addWidget(self._ep_stacks)

        footer = QLabel("按「返回」键关闭详情  |  按「方向键」导航  |  按「确认」播放")
        footer.setObjectName("DetailFooter")
        self._right_layout.addWidget(footer)

        self._right_layout.addStretch()
        right_scroll.setWidget(self._right_content)

        root.addWidget(left_widget)
        root.addWidget(right_scroll, 1)

    def _fetch_detail_async(self):
        """后台异步线程拉取详情 JSON"""
        detail_url = NetworkEngine.get_api_url(self.api, f"ac=detail&ids={self.vod_id}")

        def task():
            return NetworkEngine.fetch_json_with_retry(detail_url)

        self._load_thread = SafeThread(task)
        self._load_thread.finished.connect(self._on_detail_fetched)
        self._load_thread.start()

    def _on_detail_fetched(self, data):
        """数据拉取完毕回调（回到 UI 主线程）"""
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

        self._extra = {
            "content": vod.get("vod_content", ""),
            "actor": vod.get("vod_actor", ""),
            "director": vod.get("vod_director", ""),
            "year": vod.get("vod_year", ""),
            "area": vod.get("vod_area", ""),
        }

        # 批量更新 UI 填充完整内容
        self.setUpdatesEnabled(False)
        try:
            self._populate_detail_info()
            self._populate_routes_and_episodes()
            self._btn_play.setEnabled(True)
        finally:
            self.setUpdatesEnabled(True)

        self._setup_focus()

    def _populate_detail_info(self):
        # 填充元数据 Pills
        meta_parts = []
        if self._extra.get("year"): meta_parts.append(self._extra["year"])
        if self._extra.get("area"): meta_parts.append(self._extra["area"])
        if self.movie_data.get("vod_remarks"): meta_parts.append(self.movie_data["vod_remarks"])

        for tag in meta_parts:
            pill = QLabel(str(tag))
            pill.setObjectName("MetaPill")
            self._meta_box.addWidget(pill)
        self._meta_box.addStretch()

        # 演职人员
        info_lines = []
        if self._extra.get("director"):
            info_lines.append(f"导演：{self._extra['director']}")
        if self._extra.get("actor"):
            info_lines.append(f"主演：{self._extra['actor'].replace(',', ' / ')}")
        if info_lines:
            self._cast_label.setText("\n".join(info_lines))
        else:
            self._cast_label.hide()

        # 剧情简介
        content = self._extra.get("content", "").strip()
        if content:
            self._desc_label.setText(content[:350] + ("..." if len(content) > 350 else ""))
        else:
            self._desc_card.hide()

        # 上次播放记录提示
        ud = ConfigManager.load_user_data()
        prog = ud["progress"].get(self.vod_id, {})
        if prog.get("ep_name"):
            hint = QLabel(f"▶ 上次看到：{prog['ep_name']}")
            hint.setObjectName("DetailHint")
            self._ep_header.addStretch()
            self._ep_header.addWidget(hint)

    def _populate_routes_and_episodes(self):
        for idx, route in enumerate(self.routes):
            btn = QPushButton(route["name"])
            btn.setCheckable(True)
            btn.setObjectName("RouteTab")
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            btn.clicked.connect(lambda checked, i=idx: self._switch_route(i))
            self._route_btn_layout.addWidget(btn)
            self._route_buttons.append(btn)
            self._btn_group.addButton(btn, idx)

            # Stack 剧集列表
            ep_list = QListWidget()
            ep_list.setObjectName("EpisodeList")
            ep_list.setViewMode(QListWidget.ViewMode.IconMode)
            ep_list.setResizeMode(QListWidget.ResizeMode.Adjust)
            ep_list.setMovement(QListWidget.Movement.Static)
            ep_list.setSpacing(self.main.s(10))
            ep_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

            for ep in route["episodes"]:
                item = QListWidgetItem(ep["name"])
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setSizeHint(QSize(self.main.s(110), self.main.s(42)))
                ep_list.addItem(item)

            ep_list.itemActivated.connect(
                lambda it, r=idx, w=ep_list: self._play(r, w.currentRow())
            )
            self._ep_stacks.addWidget(ep_list)

        self._route_btn_layout.addStretch()

        if self._route_buttons:
            self._route_buttons[0].setChecked(True)
            self._ep_stacks.setCurrentIndex(0)

    def _update_poster_placeholder(self):
        w, h = self._poster_size
        pix = QPixmap(w, h)
        pix.fill(QColor(self._colors()["card_bg"]))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ch = (self.title_text or "?")[0]
        font = QFont(QApplication.font().family(), max(w // 4, 20), QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor(self._colors()["text2"]))
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, ch)
        painter.end()
        self._poster_label.setPixmap(pix)

    def _on_poster_ready(self, pixmap):
        try:
            if hasattr(self, '_poster_label') and self._poster_label and pixmap and not pixmap.isNull():
                self._poster_label.setPixmap(pixmap.scaled(
                    *self._poster_size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                ))
        except RuntimeError:
            pass

    def _switch_route(self, index):
        self._ep_stacks.setCurrentIndex(index)
        widget = self._ep_stacks.widget(index)
        if widget and isinstance(widget, QListWidget) and widget.count() > 0:
            if widget.currentRow() < 0:
                widget.setCurrentRow(0)

    def _play_default(self):
        if not self.routes:
            return
        ud = ConfigManager.load_user_data()
        prog = ud["progress"].get(self.vod_id, {})
        route_idx = prog.get("route", 0)
        ep_idx = 0
        if prog.get("ep_name"):
            for i, route in enumerate(self.routes):
                for j, ep in enumerate(route["episodes"]):
                    if ep["name"] == prog["ep_name"]:
                        route_idx = i
                        ep_idx = j
                        break
        self._play(route_idx, ep_idx)

    def _play(self, route_idx, ep_idx):
        if self._playing:
            return
        self._playing = True

        # 【关键修正 1】：点击播放立刻隐藏详情页，彻底消除“卡在详情页”的假死错觉
        self.hide()

        # 实例化播放器
        from ui.player_view import EmbeddedPlayerWindow
        player = EmbeddedPlayerWindow(
            parent_window=self.main,
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
        self.main._current_player = player

        # 展示全屏播放器
        player.showFullScreen()
        player.raise_()
        player.activateWindow()

        # 【关键修正 2】：安全异步销毁详情页，不再硬编码 100ms
        QTimer.singleShot(0, self.close)


    def _update_fav_text(self):
        ids = [str(x["vod_id"]) for x in ConfigManager.load_user_data()["favorites"]]
        self._btn_fav.setText("★  已收藏" if self.vod_id in ids else "☆  加入收藏")

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
                "vod_remarks": self.movie_data.get("vod_remarks", ""),
                "api": self.api
            })
        ConfigManager.save_user_data(ud)
        self._update_fav_text()

    # ── 2D 空间物理导航 TV 遥控系统 ──
    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(60, self._set_initial_focus)

    def _set_initial_focus(self):
        if hasattr(self, '_btn_play') and self._btn_play:
            self._btn_play.setFocus()

    def _setup_focus(self):
        targets = [self._btn_play, self._btn_fav] + self._route_buttons
        for i in range(self._ep_stacks.count()):
            lw = self._ep_stacks.widget(i)
            if isinstance(lw, QListWidget):
                targets.append(lw)

        for w in targets:
            w.installEventFilter(self)
            if isinstance(w, QListWidget):
                w.viewport().installEventFilter(self)

    def _get_focus_node(self):
        fw = QApplication.focusWidget()
        if fw is None or not self.isAncestorOf(fw):
            return "UNKNOWN", None

        if fw is self._btn_play:
            return "PLAY", self._btn_play
        if fw is self._btn_fav:
            return "FAV", self._btn_fav
        if fw in self._route_buttons:
            return "ROUTE", fw

        curr_list = self._ep_stacks.currentWidget()
        if curr_list and (fw is curr_list or fw is curr_list.viewport()):
            return "EPISODE", curr_list

        for i in range(self._ep_stacks.count()):
            lw = self._ep_stacks.widget(i)
            if lw and (fw is lw or fw is lw.viewport()):
                return "EPISODE", lw

        return "UNKNOWN", fw

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)

        key = event.key()

        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Back, Qt.Key.Key_Backspace):
            self.close()
            return True

        node_type, widget = self._get_focus_node()

        if node_type == "UNKNOWN":
            self._btn_play.setFocus()
            return True

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if node_type == "PLAY":
                self._btn_play.click()
                return True
            if node_type == "FAV":
                self._btn_fav.click()
                return True
            if node_type == "ROUTE":
                widget.click()
                return True
            if node_type == "EPISODE":
                return False

        if key == Qt.Key.Key_Up:
            if node_type == "PLAY":
                return True
            if node_type == "FAV":
                self._btn_play.setFocus()
                return True
            if node_type == "ROUTE":
                return True
            if node_type == "EPISODE":
                ep_list = widget
                idx = ep_list.currentRow()
                item_w = self.main.s(110) + self.main.s(10)
                cols = max(1, ep_list.viewport().width() // item_w)
                if idx < cols or idx < 0:
                    curr_route = self._ep_stacks.currentIndex()
                    if 0 <= curr_route < len(self._route_buttons):
                        self._route_buttons[curr_route].setFocus()
                    else:
                        self._btn_play.setFocus()
                    return True
                return False

        elif key == Qt.Key.Key_Down:
            if node_type == "PLAY":
                self._btn_fav.setFocus()
                return True
            if node_type == "FAV":
                return True
            if node_type == "ROUTE":
                curr_list = self._ep_stacks.currentWidget()
                if curr_list and curr_list.count() > 0:
                    curr_list.setFocus()
                    if curr_list.currentRow() < 0:
                        curr_list.setCurrentRow(0)
                return True
            if node_type == "EPISODE":
                ep_list = widget
                idx = ep_list.currentRow()
                total = ep_list.count()
                item_w = self.main.s(110) + self.main.s(10)
                cols = max(1, ep_list.viewport().width() // item_w)
                if idx + cols >= total or idx < 0:
                    return True
                return False

        elif key == Qt.Key.Key_Left:
            if node_type in ("PLAY", "FAV"):
                return True

            if node_type == "ROUTE":
                idx = self._route_buttons.index(widget)
                if idx > 0:
                    self._route_buttons[idx - 1].setFocus()
                    self._route_buttons[idx - 1].click()
                else:
                    self._btn_play.setFocus()
                return True

            if node_type == "EPISODE":
                ep_list = widget
                idx = ep_list.currentRow()
                item_w = self.main.s(110) + self.main.s(10)
                cols = max(1, ep_list.viewport().width() // item_w)
                if idx % cols == 0 or idx <= 0:
                    self._btn_play.setFocus()
                    return True
                return False

        elif key == Qt.Key.Key_Right:
            if node_type in ("PLAY", "FAV"):
                curr_route = self._ep_stacks.currentIndex()
                if 0 <= curr_route < len(self._route_buttons):
                    self._route_buttons[curr_route].setFocus()
                else:
                    curr_list = self._ep_stacks.currentWidget()
                    if curr_list and curr_list.count() > 0:
                        curr_list.setFocus()
                        if curr_list.currentRow() < 0:
                            curr_list.setCurrentRow(0)
                return True

            if node_type == "ROUTE":
                idx = self._route_buttons.index(widget)
                if idx < len(self._route_buttons) - 1:
                    self._route_buttons[idx + 1].setFocus()
                    self._route_buttons[idx + 1].click()
                else:
                    return True
                return True

            if node_type == "EPISODE":
                ep_list = widget
                idx = ep_list.currentRow()
                total = ep_list.count()
                item_w = self.main.s(110) + self.main.s(10)
                cols = max(1, ep_list.viewport().width() // item_w)
                if (idx + 1) % cols == 0 or idx >= total - 1:
                    return True
                return False

        return super().eventFilter(obj, event)

    def _apply_theme(self):
        d = self._colors()
        accent = d.get('accent', '#00C2D1')
        accent_hover = d.get('accent_hover', '#33D6E0')
        card_bg = d.get('card_bg', '#12181F')
        text = d.get('text', '#E8EEF2')
        text2 = d.get('text2', '#8CA0B0')
        panel_bg = d.get('panel', '#162029')

        self.setStyleSheet(f"""
            QWidget#DetailPage {{
                background-color: {panel_bg};
            }}
            QFrame#PosterContainer {{
                border-radius: {self.main.s(16)}px;
                background-color: {card_bg};
                border: 1px solid rgba(255, 255, 255, 0.1);
            }}

            QPushButton#PlayButton {{
                background-color: {accent};
                color: #0A0F14;
                border: 2px solid transparent;
                border-radius: {self.main.s(12)}px;
                font-size: {self.main.s(18)}px;
                font-weight: 800;
            }}
            QPushButton#PlayButton:hover {{
                background-color: {accent_hover};
            }}
            QPushButton#PlayButton:focus {{
                background-color: {accent_hover};
                border: 3px solid #ffffff;
            }}
            QPushButton#PlayButton:disabled {{
                background-color: rgba(255, 255, 255, 0.1);
                color: {text2};
            }}

            QPushButton#FavButton {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                color: {text2};
                border-radius: {self.main.s(12)}px;
                font-size: {self.main.s(15)}px;
                font-weight: 600;
            }}
            QPushButton#FavButton:hover {{
                border-color: {accent};
                color: {text};
            }}
            QPushButton#FavButton:focus {{
                border: 3px solid {accent};
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }}

            QScrollArea#RightScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#RightContent {{
                background: transparent;
            }}

            QLabel#DetailTitle {{
                font-size: {self.main.s(36)}px;
                font-weight: 800;
                color: {text};
                line-height: 1.2;
            }}

            QLabel#MetaPill {{
                background-color: rgba(255, 255, 255, 0.08);
                color: {accent};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: {self.main.s(6)}px;
                padding: {self.main.s(4)}px {self.main.s(12)}px;
                font-size: {self.main.s(13)}px;
                font-weight: 600;
            }}

            QLabel#DetailCast {{
                font-size: {self.main.s(14)}px;
                color: {text2};
                line-height: 1.5;
            }}

            QFrame#DescCard {{
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: {self.main.s(12)}px;
            }}
            QLabel#SubSectionTitle {{
                font-size: {self.main.s(14)}px;
                font-weight: 700;
                color: {text};
            }}
            QLabel#DetailDesc {{
                font-size: {self.main.s(14)}px;
                color: {text2};
                line-height: 1.6;
            }}

            QLabel#SectionTitle {{
                font-size: {self.main.s(20)}px;
                font-weight: 700;
                color: {text};
            }}
            QLabel#DetailHint {{
                color: {accent};
                font-size: {self.main.s(13)}px;
                font-weight: 600;
            }}
            QLabel#DetailFooter {{
                color: {text2};
                font-size: {self.main.s(13)}px;
                padding-top: {self.main.s(16)}px;
                border-top: 1px solid rgba(255, 255, 255, 0.08);
            }}

            QPushButton#RouteTab {{
                background-color: rgba(255, 255, 255, 0.06);
                color: {text2};
                border: 2px solid transparent;
                border-radius: {self.main.s(8)}px;
                padding: {self.main.s(8)}px {self.main.s(20)}px;
                font-size: {self.main.s(14)}px;
                font-weight: 600;
            }}
            QPushButton#RouteTab:checked {{
                background-color: {accent};
                color: #0A0F14;
            }}
            QPushButton#RouteTab:focus {{
                border: 2px solid #ffffff;
            }}

            QListWidget#EpisodeList {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QListWidget#EpisodeList::item {{
                background-color: rgba(255, 255, 255, 0.06);
                color: {text};
                border-radius: {self.main.s(8)}px;
                border: 2px solid transparent;
                font-size: {self.main.s(14)}px;
                font-weight: 500;
            }}
            QListWidget#EpisodeList::item:selected {{
                background-color: {accent};
                color: #0A0F14;
                font-weight: 700;
            }}
            QListWidget#EpisodeList::item:hover {{
                background-color: rgba(255, 255, 255, 0.15);
            }}
            QListWidget#EpisodeList:focus {{
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: {self.main.s(10)}px;
            }}
        """)

    def closeEvent(self, event):
        if hasattr(self, '_load_thread') and self._load_thread:
            try:
                self._load_thread.finished.disconnect()
            except Exception:
                pass
            self._load_thread.request_abort()

        if self._poster_url:
            PosterManager.get().cancel(self._poster_url)
        super().closeEvent(event)


    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        super().keyPressEvent(event)
