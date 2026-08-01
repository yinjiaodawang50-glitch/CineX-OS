# core/focus.py
"""
电视遥控器通用焦点导航器
- 方向键：上下在注册控件间移动，左右在行内组内循环
- 确认键：自动触发按钮、复选框，其他控件放行
- 返回键：可注册回调，编辑控件中不拦截
- 修复：正确识别 QListWidget 等复合控件的实际焦点
"""
from PyQt6.QtCore import QObject, QEvent, Qt
from PyQt6.QtWidgets import (
    QPushButton, QCheckBox, QComboBox, QSpinBox, QLineEdit, QListWidget, QApplication
)


class TVFocusNavigator(QObject):
    def __init__(self, parent_widget, on_return=None):
        super().__init__(parent_widget)
        self._parent = parent_widget
        self._widgets = []                 # 有序列表
        self._groups = {}                  # group_id -> [widget, ...]
        self._widget_to_group = {}         # widget -> group_id
        self._on_return = on_return        # 返回键回调（可选）
        self._parent.installEventFilter(self)

    # ── 注册 API ──
    def add_widget(self, widget, group_id=None):
        """注册单个控件，group_id 表示属于某个行内组（左右循环）"""
        if widget not in self._widgets:
            self._widgets.append(widget)
        if group_id is not None:
            if group_id not in self._groups:
                self._groups[group_id] = []
            if widget not in self._groups[group_id]:
                self._groups[group_id].append(widget)
            self._widget_to_group[widget] = group_id

    def add_widgets(self, widgets, group_id=None):
        for w in widgets:
            self.add_widget(w, group_id)

    # ── 导航辅助 ──
    def _next_focusable(self, current_idx, step):
        """在 _widgets 列表中查找下一个可见且启用的控件"""
        size = len(self._widgets)
        for i in range(1, size):
            idx = (current_idx + step * i) % size
            w = self._widgets[idx]
            if w.isVisible() and w.isEnabled() and w.focusPolicy() != Qt.FocusPolicy.NoFocus:
                return w
        return None

    def _group_start(self, group_id):
        """返回组内第一个控件在 _widgets 中的索引"""
        members = self._groups.get(group_id, [])
        if not members:
            return -1
        try:
            return self._widgets.index(members[0])
        except ValueError:
            return -1

    def _group_end(self, group_id):
        """返回组内最后一个控件在 _widgets 中的索引"""
        members = self._groups.get(group_id, [])
        if not members:
            return -1
        try:
            return self._widgets.index(members[-1])
        except ValueError:
            return -1

    # ── 事件过滤 ──
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.KeyPress:
            return False

        key = event.key()
        fw = QApplication.focusWidget()
        if fw is None or not self._parent.isAncestorOf(fw):
            return False

        # 判断焦点控件是否属于注册控件（或注册控件的子控件，如 QListWidget 视口）
        in_nav = False
        for w in self._widgets:
            if fw is w or (isinstance(w, QListWidget) and w.viewport() is fw):
                in_nav = True
                break
        group_id = None
        if in_nav:
            # 获取实际注册的控件（可能是父控件）
            for w in self._widgets:
                if fw is w or (isinstance(w, QListWidget) and w.viewport() is fw):
                    group_id = self._widget_to_group.get(w)
                    break

        # ── 返回键处理 ──
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Back):
            if isinstance(fw, (QLineEdit, QSpinBox)):
                return False
            if self._on_return:
                self._on_return()
                return True
            return False

        # ── 方向键 ──
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            if not in_nav:
                return False
            # 如果在组内，上下键跳出组
            if group_id is not None:
                if key == Qt.Key.Key_Up:
                    idx = self._group_start(group_id) - 1
                else:
                    idx = self._group_end(group_id) + 1
                if 0 <= idx < len(self._widgets):
                    w = self._widgets[idx]
                    if w.isVisible() and w.isEnabled():
                        w.setFocus()
                        return True
                return True
            else:
                # 普通控件：按顺序上下移动
                # 找到实际注册控件在列表中的索引
                current_widget = None
                for w in self._widgets:
                    if fw is w or (isinstance(w, QListWidget) and w.viewport() is fw):
                        current_widget = w
                        break
                if current_widget is None:
                    return False
                idx = self._widgets.index(current_widget)
                next_w = self._next_focusable(idx, -1 if key == Qt.Key.Key_Up else 1)
                if next_w:
                    next_w.setFocus()
                    return True
                return True

        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if not in_nav:
                return False
            # 左右键仅在行内组中处理，否则放行
            if group_id is not None:
                members = self._groups[group_id]
                # 找到当前控件（或父控件）在组中的位置
                current_member = None
                for w in members:
                    if fw is w or (isinstance(w, QListWidget) and w.viewport() is fw):
                        current_member = w
                        break
                if current_member is None:
                    return False
                try:
                    idx = members.index(current_member)
                except ValueError:
                    return False
                if key == Qt.Key.Key_Left:
                    next_idx = (idx - 1) % len(members)
                else:
                    next_idx = (idx + 1) % len(members)
                members[next_idx].setFocus()
                return True
            return False

        # ── 确认键 ──
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if in_nav:
                if isinstance(fw, QPushButton):
                    fw.click()
                    return True
                if isinstance(fw, QCheckBox):
                    fw.setChecked(not fw.isChecked())
                    return True
                # QComboBox, QSpinBox, QLineEdit, QListWidget 等放行
                return False
            return False

        return False