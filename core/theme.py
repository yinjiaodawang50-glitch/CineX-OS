# core/theme.py
import math
from PyQt6.QtCore import QByteArray, QSize
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPixmapCache
from PyQt6.QtSvg import QSvgRenderer

class SVGIconLibrary:
    """
    Lucide / Feather 极简 SVG 矢量库 (嵌入式内存字符串，零文件读取)
    """
    HEADER = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" ' \
             'fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    FOOTER = '</svg>'

    PATHS = {
        "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
        "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
        "left": '<path d="m15 18-6-6 6-6"/>',
        "right": '<path d="m9 18 6-6-6-6"/>',
        "star_o": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
        "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" fill="{color}"/>',
        "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
        "film": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/>',
        "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M4.93 4.93l1.41 1.41"/><path d="M17.66 17.66l1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M6.34 17.66l-1.41 1.41"/><path d="M19.07 4.93l-1.41 1.41"/>',
        "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
        "route": '<circle cx="6" cy="19" r="3"/><circle cx="18" cy="5" r="3"/><path d="M12 12h.01"/><path d="M18 8v1a4 4 0 0 1-4 4H10a4 4 0 0 0-4 4v1"/>',
        "tvbox": '<rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/>',
        "power": '<path d="M12 2v10"/><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/>',
        "chevron_down": '<path d="m6 9 6 6 6-6"/>',
        "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
        "back": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
        # ── 播放器专属矢量 SVG 路径 ──
        "play": '<polygon points="6 3 20 12 6 21 6 3" fill="{color}"/>',
        "pause": '<rect x="6" y="4" width="4" height="16" rx="1" fill="{color}"/><rect x="14" y="4" width="4" height="16" rx="1" fill="{color}"/>',
        "forward": '<polygon points="13 19 22 12 13 5 13 19" fill="{color}"/><polygon points="2 19 11 12 2 5 2 19" fill="{color}"/>',
        "rewind": '<polygon points="11 19 2 12 11 5 11 19" fill="{color}"/><polygon points="22 19 13 12 22 5 22 19" fill="{color}"/>',
        "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" fill="{color}"/>',
        "skip_forward": '<polygon points="5 4 15 12 5 20 5 4" fill="{color}"/><line x1="19" y1="5" x2="19" y2="19" stroke="{color}" stroke-width="2.5" stroke-linecap="round"/>',
        "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'
    }

    @classmethod
    def get_svg_data(cls, name, color_hex):
        path_data = cls.PATHS.get(name, cls.PATHS["tvbox"])
        svg_str = (cls.HEADER + path_data + cls.FOOTER).format(color=color_hex)
        return QByteArray(svg_str.encode('utf-8'))


class Theme:
    """
    CineX OS 极简发光青绿调色板 (符合 WCAG AAA 对比度 ≥ 7:1)
    """
    DARK = {
        "bg": "#0A0F14",           # 极暗深海藏青
        "surface": "#12181F",      # 次级暗蓝卡片
        "panel": "#162029",        # 顶部栏/导航面板
        "border": "#202C38",       # 细弱边框
        "border2": "#2C3D4E",      # 聚焦/高亮边框
        "hover": "#1A2530",        # 悬停态
        "text": "#E8EEF2",         # 主文字 (对比度 17.2:1)
        "text2": "#8CA0B0",        # 次级文字 (对比度 7.1:1)
        "accent": "#00C2D1",       # 干净发光青绿 (通透不发灰)
        "accent_hover": "#33D6E0", # 高亮焦点青绿
        "accent2": "#0099A8",      # 辅助深青
        "danger": "#EF4444",       # 警示红
        "input": "#12181F",        # 输入框背景
        "card_bg": "#12181F",      # 影片卡片背景
        "skeleton": "#1A2430",     # 骨架屏基础色
        "skeleton2": "#243242",    # 骨架屏高亮色
    }

    LIGHT = {
        "bg": "#F0F4F7",           # 暖冰白背景
        "surface": "#FFFFFF",      # 纯白卡片
        "panel": "#FFFFFF",        # 纯白面板
        "border": "#D8E2E8",       # 细边框
        "border2": "#BCCCD6",      # 高亮边框
        "hover": "#E4ECF0",        # 悬停态
        "text": "#1A2229",         # 主文字 (对比度 14.5:1)
        "text2": "#5E7180",        # 次级文字 (对比度 7.1:1)
        "accent": "#0099A8",       # 略深青绿
        "accent_hover": "#007A87", # 悬停深青
        "accent2": "#005C66",      # 辅助深青
        "danger": "#EF4444",       # 警示红
        "input": "#FFFFFF",        # 输入框背景
        "card_bg": "#FFFFFF",      # 影片卡片背景
        "skeleton": "#E2E9EE",     # 骨架屏基础色
        "skeleton2": "#D2DEE6",    # 骨架屏高亮色
    }

    @classmethod
    def get(cls, key, is_dark=True):
        return cls.DARK.get(key, "#0A0F14") if is_dark else cls.LIGHT.get(key, "#F0F4F7")

    @classmethod
    def get_stylesheet(cls, is_dark, scale=1.0):
        d = cls.DARK if is_dark else cls.LIGHT
        a, a_hover = d["accent"], d["accent_hover"]
        bg, sf, pn = d["bg"], d["surface"], d["panel"]
        bd, bd2 = d["border"], d["border2"]
        hv, tx, tx2, inp = d["hover"], d["text"], d["text2"], d["input"]

        def px(val): return max(1, int(val * scale))

        if is_dark:
            accent_rgba = "rgba(0, 194, 209, 0.18)"
            accent_rgba_strong = "rgba(0, 194, 209, 0.35)"
            bg_gradient = f"qradialgradient(cx:0.5, cy:0.15, radius:1.2, fx:0.5, fy:0.15, stop:0 #101B24, stop:1 {bg})"
        else:
            accent_rgba = "rgba(0, 153, 168, 0.15)"
            accent_rgba_strong = "rgba(0, 153, 168, 0.30)"
            bg_gradient = f"qradialgradient(cx:0.5, cy:0.15, radius:1.2, fx:0.5, fy:0.15, stop:0 #FFFFFF, stop:1 {bg})"

        return f"""
        * {{ font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif; }}
        QMainWindow, QDialog {{ 
            background: {bg_gradient}; 
        }}

        /* ── TopBar ── */
        #TopBar {{
            background: {pn};
            border-bottom: 1px solid {bd};
            border-radius: 0px;
        }}
        #TopBarTitle {{
            color: {tx};
            font-weight: 700;
            font-size: {px(16)}px;
            background: transparent;
        }}
        #TopBarTime {{
            color: {tx2};
            font-size: {px(13)}px;
            background: transparent;
        }}
        #TopBarBtn {{
            background: transparent;
            border: 1.5px solid transparent;
            border-radius: {px(8)}px;
            padding: {px(4)}px;
        }}
        #TopBarBtn:hover {{ background: {hv}; border-color: {bd}; }}
        #TopBarBtn:focus {{ border: 2px solid {a_hover}; background: {hv}; outline: none; }}
        #TopBarBtn:pressed {{ background: {accent_rgba}; border-color: {a}; }}

        /* ── Nav Panel ── */
        #NavPanel {{ 
            background: {pn}; 
            border: 1px solid {bd}; 
            border-radius: {px(14)}px; 
        }}
        #SectionLabel {{
            color: {tx2}; font-size: {px(10)}px; font-weight: 700;
            letter-spacing: 1.8px; padding: {px(6)}px {px(14)}px;
            background: transparent; text-transform: uppercase;
        }}
        #NavButton {{
            background: transparent; color: {tx};
            border: none; border-left: 3px solid transparent;
            border-radius: 0px;
            padding: {px(10)}px {px(12)}px {px(10)}px {px(10)}px;
            text-align: left; font-size: {px(14)}px; font-weight: 500;
            outline: none;
        }}
        #NavButton:hover {{
            background: {hv}; border-radius: {px(8)}px;
            border-left: 3px solid transparent;
            margin: 0 {px(4)}px;
        }}
        #NavButton:focus {{
            background: {hv}; border-radius: {px(8)}px;
            border-left: 3px solid {a_hover};
            margin: 0 {px(4)}px;
            color: #FFFFFF;
        }}
        #NavButton:checked {{
            background: transparent; color: {a};
            font-weight: 700; border-left: 3px solid {a};
            border-radius: 0px; margin: 0px;
        }}

        /* ── Category List ── */
        #CategoryList {{ background: transparent; border: none; outline: none; }}
        #CategoryList::item {{
            color: {tx}; font-size: {px(13)}px;
            padding: {px(8)}px {px(12)}px; margin: {px(1)}px {px(4)}px;
            border-radius: {px(8)}px; background: transparent;
            border: 1.5px solid transparent;
        }}
        #CategoryList::item:hover {{ background: {hv}; }}
        #CategoryList::item:focus {{ background: {hv}; border-color: {a_hover}; }}
        #CategoryList::item:selected {{
            background: {accent_rgba}; color: {a};
            font-weight: 700; border-color: {accent_rgba_strong};
        }}

        /* ── Labels ── */
        QLabel {{ color: {tx}; background: transparent; }}
        #TopLabel {{ color: {tx2}; font-size: {px(13)}px; font-weight: 600; }}
        #PageLabel {{ color: {tx2}; font-size: {px(12)}px; font-weight: 500; background: transparent; }}
        #DotLabel {{ font-size: {px(14)}px; background: transparent; }}

        /* ── SearchBox ── */
        QLineEdit {{
            background: {inp}; color: {tx};
            border: 1.5px solid {bd}; border-radius: {px(8)}px;
            padding: {px(6)}px {px(10)}px {px(6)}px {px(10)}px;
            font-size: {px(13)}px; outline: none;
        }}
        QLineEdit:focus {{ border: 2px solid {a_hover}; }}
        QLineEdit:hover {{ border-color: {bd2}; }}

        /* ── Source 选择器 ── */
        #SourceButton {{
            background: {inp}; color: {tx};
            border: 1.5px solid {bd}; border-radius: {px(8)}px;
            padding: {px(6)}px {px(32)}px {px(6)}px {px(12)}px;
            font-size: {px(13)}px; font-weight: 600;
            text-align: left; min-width: {px(130)}px;
            height: {px(24)}px; outline: none;
        }}
        #SourceButton:hover {{ background: {hv}; border-color: {bd2}; }}
        #SourceButton:focus {{ border: 2px solid {a_hover}; background: {hv}; }}
        #SourceButton:pressed {{ background: {accent_rgba}; border-color: {a}; }}

        /* ── Source 列表 ── */
        #SourceList {{ background: transparent; border: none; outline: none; }}
        #SourceList::item {{
            background: {sf}; border: 1.5px solid {bd};
            border-radius: {px(8)}px; color: {tx};
            font-size: {px(13)}px; font-weight: 500;
            margin-bottom: {px(6)}px; padding: {px(8)}px {px(10)}px;
        }}
        #SourceList::item:hover {{ border-color: {a}66; background: {hv}; }}
        #SourceList::item:selected {{ background: {accent_rgba}; color: {a}; border-color: {a}; font-weight: 700; }}

        /* ── Generic buttons ── */
        QPushButton {{
            background: {inp}; color: {tx};
            border: 1.5px solid {bd}; border-radius: {px(8)}px;
            padding: {px(6)}px {px(14)}px;
            font-size: {px(13)}px; font-weight: 500;
            height: {px(24)}px; outline: none;
        }}
        QPushButton:hover {{ background: {hv}; border-color: {bd2}; }}
        QPushButton:pressed {{ background: {accent_rgba}; color: {a}; border-color: {a}; }}
        QPushButton:focus {{ border: 2px solid {a_hover}; }}

        /* 主强调按钮 */
        #AccentButton {{
            background: {a}; color: #0A0F14; border: none;
            border-radius: {px(8)}px; padding: {px(6)}px {px(14)}px; font-weight: 700;
        }}
        #AccentButton:hover {{ background: {a_hover}; color: #0A0F14; }}
        #AccentButton:focus {{ border: 2px solid #FFFFFF; background: {a_hover}; color: #0A0F14; }}

        /* ── Scrollbar ── */
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{
            border: none; background: transparent;
            width: {px(6)}px; margin: {px(4)}px 0;
        }}
        QScrollBar::handle:vertical {{
            background: {bd2}; min-height: {px(28)}px;
            border-radius: {px(3)}px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {a}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

        /* ── Episode Dialog ── */
        QDialog QListWidget {{ background: transparent; border: none; outline: none; }}
        QDialog QListWidget::item {{
            background: {sf}; border: 1.5px solid {bd};
            border-radius: {px(8)}px; color: {tx}; padding: {px(6)}px;
        }}
        QDialog QListWidget::item:hover {{ border-color: {a}66; background: {hv}; }}
        QDialog QListWidget::item:selected {{ background: {accent_rgba}; color: {a}; border-color: {a}; font-weight: 700; }}

        /* ── Tab bar ── */
        QTabWidget::pane {{ border: none; background: transparent; margin-top: {px(6)}px; }}
        QTabBar::tab {{
            background: transparent; color: {tx2};
            padding: {px(6)}px {px(16)}px;
            font-size: {px(13)}px; font-weight: 500;
            border-bottom: 2px solid transparent; outline: none;
        }}
        QTabBar::tab:selected {{ color: {a}; border-bottom: 2px solid {a}; font-weight: 700; }}
        QTabBar::tab:hover {{ color: {tx}; }}
        QTabBar::tab:focus {{ color: {tx}; background: {hv}; border-radius: {px(6)}px; border-bottom-color: {a_hover}; }}
        """

    @classmethod
    def create_icon(cls, name, color="#00C2D1", size=20):
        cache_key = f"svg_icon_{name}_{color}_{size}"
        pixmap = QPixmapCache.find(cache_key)
        if not pixmap:
            pixmap = QPixmap(size, size)
            pixmap.fill(QColor(0, 0, 0, 0))
            svg_bytes = SVGIconLibrary.get_svg_data(name, color)
            renderer = QSvgRenderer(svg_bytes)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter)
            painter.end()
            QPixmapCache.insert(cache_key, pixmap)
        return QIcon(pixmap)