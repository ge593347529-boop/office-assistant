"""系统托盘图标 + 右键菜单"""

import logging

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QPen, QBrush, QFont
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

logger = logging.getLogger(__name__)


def _make_tray_icon() -> QIcon:
    """用 QPainter 代码绘制一个托盘图标——蓝底白色 'AI' 文字圆形图标。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # 蓝底圆形
    painter.setPen(QPen(QColor("#2563EB"), 0))
    painter.setBrush(QBrush(QColor("#2563EB")))
    painter.drawEllipse(4, 4, 56, 56)

    # 白色 "AI" 文字
    painter.setPen(QPen(QColor("#FFFFFF")))
    font = QFont("Microsoft YaHei", 22, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "AI")

    painter.end()
    return QIcon(pixmap)


class SystemTray(QSystemTrayIcon):
    """系统托盘图标 + 右键菜单"""

    show_window = Signal()
    quit_app = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        icon = _make_tray_icon()
        self.setIcon(icon)
        self.setToolTip("AI 办公助手")

        # 右键菜单
        self._menu = QMenu()

        show_action = QAction("显示主窗口", self._menu)
        show_action.triggered.connect(self.show_window.emit)
        self._menu.addAction(show_action)

        self._menu.addSeparator()

        settings_action = QAction("设置", self._menu)
        settings_action.triggered.connect(self._on_settings)
        self._menu.addAction(settings_action)

        history_action = QAction("任务历史", self._menu)
        history_action.triggered.connect(self._on_history)
        self._menu.addAction(history_action)

        self._menu.addSeparator()

        quit_action = QAction("退出", self._menu)
        quit_action.triggered.connect(self.quit_app.emit)
        self._menu.addAction(quit_action)

        self.setContextMenu(self._menu)

        # 左键双击显示主窗口
        self.activated.connect(self._on_activated)

        logger.info("系统托盘初始化完成")
        self.show()

    def _on_settings(self):
        """设置菜单 — 发射信号由 main_window 打开设置对话框。"""
        self.settings_requested.emit()

    def _on_history(self):
        """任务历史菜单（占位）。"""
        self.show_notification("任务历史", "任务历史功能即将推出")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            logger.debug("托盘双击，触发 show_window")
            self.show_window.emit()

    # ── 公开方法 ─────────────────────────────────────────────

    def show_notification(self, title: str, message: str, duration: int = 3000) -> None:
        """弹出气泡通知。"""
        if self.supportsMessages():
            self.showMessage(title, message, QSystemTrayIcon.Information, duration)
            logger.info("气泡通知: %s – %s", title, message)
        else:
            logger.warning("当前系统不支持气泡通知")

    def set_task_status(self, text: str) -> None:
        """更新托盘提示文字。"""
        tip = f"AI 办公助手 - {text}" if text else "AI 办公助手"
        self.setToolTip(tip)
        logger.debug("托盘提示更新: %s", tip)
