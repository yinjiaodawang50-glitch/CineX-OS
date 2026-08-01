# ui/widgets.py
import os
import sys
import math
import gc
import logging
import time
from PyQt6.QtWidgets import (
    QFrame, QLabel, QLineEdit, QListWidget, QPushButton, QHBoxLayout, QVBoxLayout,
    QWidget, QGridLayout, QScrollArea, QCompleter, QApplication
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QTimer, QRect, QRectF, QPointF,
    QStringListModel, QSize, QVariantAnimation, QEasingCurve
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QBrush, QPen, QPainterPath,
    QLinearGradient, QFont, QFontMetrics
)

from core.config import ConfigManager
from core.theme import Theme
from core.poster import PosterManager

BASE_CARD_W, BASE_CARD_H = 175, 262


# ── 全局骨架屏驱动 ──────────────────────────────────────────────────
class _SkeletonDriver:
    """单例扫光驱动：统一推进 phase，通知所有活跃骨架屏刷新"""
    _instance = None

    def __init__(self):
        self.phase = 0.0
        self._cards: list["SkeletonCard"] = []
        self._timer = QTimer()
        self._timer.setInterval(100)          # 10 fps，扫光流畅
        self._timer.timeout.connect(self._tick)

    @classmethod
    def get(cls) -> "_SkeletonDriver":
        if cls._instance is None:
            cls._instance = _SkeletonDriver()
        return cls._instance

    def register(self, card: "SkeletonCard"):
        self._cards.append(card)
        if not self._timer.isActive():
            self._timer.start()

    def unregister(self, card: "SkeletonCard"):
        try:
            self._cards.remove(card)
        except ValueError:
            pass
        if not self._cards:
            self._timer.stop()

    def _tick(self):
        self.phase = (self.phase + 0.05) % 1.0
        for card in self._cards:
            card.update()


class SkeletonCard(QFrame):
    def __init__(self, is_dark=True, w=BASE_CARD_W, h=BASE_CARD_H, parent=None):
        super().__init__(parent)
        self.is_dark = is_dark
        self.card_w = w
        self.card_h = h
        self.setFixedSize(w, h)
        _SkeletonDriver.get().register(self)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = Theme.DARK if self.is_dark else Theme.LIGHT
        w, h = self.card_w, self.card_h
        phase = _SkeletonDriver.get().phase

        margin = 12
        base_rect = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)

        clip = QPainterPath()
        clip.addRoundedRect(base_rect, 14, 14)
        p.setClipPath(clip)
        p.fillRect(base_rect, QColor(d["card_bg"]))

        sweep_x = -w + phase * (w * 2)
        grad = QLinearGradient(QPointF(sweep_x, 0), QPointF(sweep_x + w, 0))
        grad.setColorAt(0.0,  QColor(d["skeleton"]))
        grad.setColorAt(0.45, QColor(d["skeleton2"]))
        grad.setColorAt(0.55, QColor(d["skeleton2"]))
        grad.setColorAt(1.0,  QColor(d["skeleton"]))
        p.fillRect(base_rect, QBrush(grad))

        p.end()

    def stop(self):
        """从全局驱动注销，停止参与刷新"""
        _SkeletonDriver.get().unregister(self)


class MovieCard(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, movie_data, is_dark=True, w=BASE_CARD_W, h=BASE_CARD_H,
                 parent=None, poster_priority: int = 50):
        super().__init__(parent)
        self.movie_data = movie_data
        self.is_dark = is_dark
        self.card_w = w
        self.card_h = h
        self.setFixedSize(w, h)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._original_pixmap = None
        self._scaled_pixmap = None
        self._poster_url = ""
        self.is_hovered = False
        self.is_focused = False

        # ── 3D 焦点缩放动画属性 ──
        self._scale_factor = 1.0
        self._scale_anim = QVariantAnimation(self)
        self._scale_anim.setDuration(180)  # 180ms 极佳动画触感
        self._scale_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scale_anim.valueChanged.connect(self._on_scale_anim)

        self._load_progress()
        self._init_poster(movie_data.get("vod_pic", ""), priority=poster_priority)

    def _load_progress(self):
        """读取观影进度数据"""
        ud = ConfigManager.load_user_data()
        vod_id = str(self.movie_data.get("vod_id", ""))
        prog_data = ud.get("progress", {}).get(vod_id, {})
        self._progress_ep = prog_data.get("ep_name", "")
        self._progress_pct = prog_data.get("progress_percent", 50 if self._progress_ep else 0)

    def _on_scale_anim(self, val):
        self._scale_factor = val
        self.update()

    def _start_scale_animation(self, target_scale):
        if abs(self._scale_factor - target_scale) < 0.001:
            return
        self._scale_anim.stop()
        self._scale_anim.setStartValue(self._scale_factor)
        self._scale_anim.setEndValue(target_scale)
        self._scale_anim.start()

    def set_card_size(self, w, h):
        if self.card_w == w and self.card_h == h:
            return
        self.card_w = w
        self.card_h = h
        self.setFixedSize(w, h)
        self._scale_pixmap()
        self.update()

    def _scale_pixmap(self):
        if self._original_pixmap and not self._original_pixmap.isNull():
            margin = 12
            target_w = int(self.card_w - 2 * margin)
            target_h = int(self.card_h - 2 * margin)
            self._scaled_pixmap = self._original_pixmap.scaled(
                target_w, target_h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.FastTransformation
            )
        else:
            self._scaled_pixmap = None

    def _init_poster(self, pic_url: str, priority: int = 50):
        if not pic_url or not pic_url.startswith("http"):
            return
        self._poster_url = pic_url
        PosterManager.get().request(pic_url, self._on_poster, priority=priority)

    def set_priority(self, priority: int):
        url = getattr(self, "_poster_url", "")
        if url and self._original_pixmap is None:
            PosterManager.get().reprioritize(url, priority)

    def _on_poster(self, px: QPixmap):
        if px and not px.isNull():
            self._original_pixmap = px
            self._scale_pixmap()
            self.update()

    def closeEvent(self, event):
        url = getattr(self, "_poster_url", "")
        if url:
            PosterManager.get().cancel(url)
        super().closeEvent(event)

    # ── 焦点与悬停触发 3D 缩放动画 ──
    def focusInEvent(self, e):
        self.is_focused = True
        self._start_scale_animation(1.05)  # 获焦放至 1.05 倍
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.is_focused = False
        if not self.is_hovered:
            self._start_scale_animation(1.0)  # 失焦缩回 1.0 倍
        super().focusOutEvent(e)

    def enterEvent(self, e):
        self.is_hovered = True
        self._start_scale_animation(1.05)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.is_hovered = False
        if not self.is_focused:
            self._start_scale_animation(1.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.movie_data)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.movie_data)
            e.accept()
        else:
            super().keyPressEvent(e)

    def _get_poster_radius(self):
        r_map = {"小": 8, "中": 14, "大": 20}
        try:
            ud = ConfigManager.load_user_data()
            level = ud.get("settings", {}).get("poster_radius", "中")
            return r_map.get(level, 14)
        except:
            return 14

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        d = Theme.DARK if self.is_dark else Theme.LIGHT
        w, h = self.card_w, self.card_h

        # 【核心修复】：预留 12px 缩放安全外边距，确保 1.05x 放大 + 3px 边框完全落在 Widget 内部不被裁剪
        margin = 12
        base_rect = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)
        radius = self._get_poster_radius()

        # ── 1. 应用 3D 矩阵缩放变换（以卡片中心为原点） ──
        p.save()
        p.translate(w / 2, h / 2)
        p.scale(self._scale_factor, self._scale_factor)
        p.translate(-w / 2, -h / 2)

        # ── 2. 绘制柔和动态阴影 ──
        shadow_alpha = 30 if (self.is_hovered or self.is_focused) else 12
        shadow_offsets = [
            (0, 4, 8, shadow_alpha),
            (0, 2, 4, shadow_alpha + 8)
        ]
        p.setPen(Qt.PenStyle.NoPen)
        for off_x, off_y, blur, alpha in shadow_offsets:
            p.setBrush(QColor(0, 0, 0, alpha))
            shadow_rect = base_rect.adjusted(off_x, off_y, off_x, off_y)
            p.drawRoundedRect(shadow_rect, radius + 2, radius + 2)

        # ── 3. 剪裁圆角卡片区域 ──
        clip = QPainterPath()
        clip.addRoundedRect(base_rect, radius, radius)
        p.setClipPath(clip)

        # ── 4. 绘制海报或占位图 ──
        if self._scaled_pixmap and not self._scaled_pixmap.isNull():
            sc = self._scaled_pixmap
            p.drawPixmap(int(base_rect.x()), int(base_rect.y()), sc)
        else:
            p.fillRect(base_rect, QColor(d["card_bg"]))
            ch = (self.movie_data.get("vod_name", "?") or "?")[0]
            base_font = QApplication.font()
            f = QFont(base_font)
            f.setPointSize(36)
            f.setWeight(QFont.Weight.Normal)
            p.setFont(f)
            p.setPen(QColor(d["border2"]))
            p.drawText(base_rect, Qt.AlignmentFlag.AlignCenter, ch)

        # ── 5. 毛玻璃底部信息栏 ──
        info_height = 50
        info_rect = QRectF(base_rect.x(), base_rect.bottom() - info_height, base_rect.width(), info_height)
        glass_bg = QColor(20, 25, 32, 180) if self.is_dark else QColor(240, 244, 248, 180)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glass_bg)
        p.drawRect(info_rect)

        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.drawLine(QPointF(info_rect.left(), info_rect.top()), QPointF(info_rect.right(), info_rect.top()))

        # ── 6. 标题单行截断（带省略号 ...） ──
        base_font = QApplication.font()
        title_font = QFont(base_font)
        title_font.setPointSize(10)
        title_font.setWeight(QFont.Weight.Bold)
        p.setFont(title_font)
        p.setPen(QColor(255, 255, 255))

        raw_title = self.movie_data.get("vod_name", "未知")
        fm = QFontMetrics(title_font)
        elided_title = fm.elidedText(raw_title, Qt.TextElideMode.ElideRight, int(base_rect.width() - 20))
        title_rect = QRectF(base_rect.x() + 10, info_rect.top(), base_rect.width() - 20, info_height)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_title)

        # 备注角标 (右上角)
        remarks = self.movie_data.get("vod_remarks", "")
        if remarks:
            remarks_font = QFont(base_font)
            remarks_font.setPointSize(9)
            remarks_font.setWeight(QFont.Weight.Bold)
            p.setFont(remarks_font)
            fm_r = QFontMetrics(remarks_font)
            text_r = remarks[:8]
            tw_r = fm_r.horizontalAdvance(text_r) + 10
            br = QRectF(base_rect.right() - tw_r - 6, base_rect.top() + 6, tw_r, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(d["accent"]))
            p.drawRoundedRect(br, 4, 4)
            p.setPen(QColor("#0A0F14"))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, text_r)

        # 上次看到角标 (左上角)
        if self._progress_ep:
            progress_font = QFont(base_font)
            progress_font.setPointSize(9)
            progress_font.setWeight(QFont.Weight.Bold)
            p.setFont(progress_font)
            fm_p = QFontMetrics(progress_font)
            prog = f"▶ {self._progress_ep}"[:9]
            tw_p = fm_p.horizontalAdvance(prog) + 10
            pr = QRectF(base_rect.x() + 6, base_rect.top() + 6, tw_p, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(d["accent"]))
            p.drawRoundedRect(pr, 4, 4)
            p.setPen(QColor("#0A0F14"))
            p.drawText(pr, Qt.AlignmentFlag.AlignCenter, prog)

        # ── 7. 底部观影进度条 Overlay ──
        if self._progress_pct > 0:
            bar_h = 4
            bar_y = base_rect.bottom() - bar_h
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRect(QRectF(base_rect.x(), bar_y, base_rect.width(), bar_h))

            fill_w = base_rect.width() * min(1.0, max(0.0, self._progress_pct / 100.0))
            p.setBrush(QColor(d["accent"]))
            p.drawRect(QRectF(base_rect.x(), bar_y, fill_w, bar_h))

        # ── 8. 焦点/悬停蒂芙尼青高亮边框（完全落在 Widget 安全范围内） ──
        p.setClipping(False)
        if self.is_hovered or self.is_focused:
            pen_w = 3.0 if self.is_focused else 2.0
            border_color = QColor(d["accent_hover"]) if self.is_focused else QColor(d["accent"])
            p.setPen(QPen(border_color, pen_w))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(base_rect, radius, radius)

        p.restore()
        p.end()


class SourceButton(QPushButton):
    """数据源选择按钮：右侧绘制 chevron-down 图标"""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        chevron_color = QColor("#8CA0B0")
        p.setPen(QPen(chevron_color, 1.6, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        w, h = self.width(), self.height()
        cx = w - 14
        cy = h // 2
        p.drawLine(cx - 4, cy - 2, cx, cy + 3)
        p.drawLine(cx, cy + 3, cx + 4, cy - 2)
        p.end()


class SearchBox(QLineEdit):
    """搜索框：带图标与清除按钮"""
    search_triggered = pyqtSignal(str)

    _ICON_W = 30
    _CLEAR_W = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("搜索影片...")
        self.setFixedWidth(230)
        self.setTextMargins(self._ICON_W - 8, 0, self._CLEAR_W, 0)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(480)
        self._debounce.timeout.connect(self._emit)
        self.textChanged.connect(self._on_text_changed)
        self.returnPressed.connect(self._emit_now)

        self.completer = QCompleter(self)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompleter(self.completer)
        self._update_history_model()

    def _update_history_model(self):
        ud = ConfigManager.load_user_data()
        history = ud.get("search_history", [])
        model = QStringListModel(history)
        self.completer.setModel(model)

    def _on_text_changed(self):
        self.update()
        self._debounce.start()

    def _emit(self):
        t = self.text().strip()
        if len(t) >= 2 or t == "":
            self.search_triggered.emit(t)

    def _emit_now(self):
        self._debounce.stop()
        t = self.text().strip()
        if t:
            ud = ConfigManager.load_user_data()
            history = ud.get("search_history", [])
            if t in history:
                history.remove(t)
            history.insert(0, t)
            if len(history) > 10:
                history = history[:10]
            ud["search_history"] = history
            ConfigManager.save_user_data(ud)
            self._update_history_model()
        self.search_triggered.emit(t)

    def _clear_btn_rect(self) -> QRect:
        h = self.height()
        return QRect(self.width() - self._CLEAR_W, 0, self._CLEAR_W, h)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.text():
            if self._clear_btn_rect().contains(e.pos()):
                self.clear()
                self.search_triggered.emit("")
                return
        super().mousePressEvent(e)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        icon_color = QColor("#8CA0B0")

        cx, cy, r = 14, h // 2, 5
        p.setPen(QPen(icon_color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        p.drawLine(cx + 3, cy + 3, cx + 7, cy + 7)

        if self.text():
            btn = self._clear_btn_rect()
            bx = btn.x() + btn.width() // 2
            by = btn.y() + btn.height() // 2
            p.setPen(QPen(icon_color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            d = 4
            p.drawLine(bx - d, by - d, bx + d, by + d)
            p.drawLine(bx + d, by - d, bx - d, by + d)

        p.end()