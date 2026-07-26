"""CapsuleWindow — 灵动岛胶囊窗口，吸附屏幕左边缘。

一个小型浮动圆形图标，显示在屏幕左侧边缘，半隐藏。
- 点击展开/隐藏侧边面板
- 拖拽可移动位置，释放后自动吸附回左边缘
- 10 秒无操作后自动变暗
- 系统托盘菜单：显示/隐藏、设置、重启、退出
"""

from __future__ import annotations

import logging
import math

from PySide6.QtCore import (
    Qt,
    QPoint,
    QTimer,
    QPropertyAnimation,
    Signal,
    QEasingCurve,
    QRect,
)
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QBrush,
    QFont,
    QIcon,
    QPixmap,
    QMouseEvent,
    QAction,
    QEnterEvent,
    QLinearGradient,
)
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QMainWindow,
    QSystemTrayIcon,
    QMenu,
    QApplication,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CAPSULE_SIZE = 48          # 胶囊直径 (px)
SNAP_DURATION = 220        # 吸附动画时长 (ms)
IDLE_TIMEOUT = 10_000      # 空闲超时 (ms) — 10 秒
CLICK_THRESHOLD = 3        # 点击判定阈值 (px) — 移动小于此值视为单击
DIM_OPACITY = 0.5          # 变暗时的透明度


# ---------------------------------------------------------------------------
# 工具：绘制托盘图标
# ---------------------------------------------------------------------------

def _make_tray_icon() -> QIcon:
    """用 QPainter 绘制一个绿色圆形 + 白色 "AI" 文字的系统托盘图标。"""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # 绿色圆形背景
    painter.setPen(QPen(QColor("#238636"), 0))
    painter.setBrush(QBrush(QColor("#238636")))
    painter.drawEllipse(4, 4, size - 8, size - 8)

    # 白色 "AI" 文字
    painter.setPen(QPen(QColor("#FFFFFF")))
    font = QFont("Microsoft YaHei", 22, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "AI")

    painter.end()
    return QIcon(pixmap)


# ---------------------------------------------------------------------------
# CapsuleWindow
# ---------------------------------------------------------------------------

class CapsuleWindow(QMainWindow):
    """灵动岛胶囊窗口 — 吸附屏幕左边缘。

    信号
    ----
    toggle_requested  : 单击胶囊时发射，用于切换侧边面板
    restart_requested : 系统托盘「重启」菜单
    settings_requested: 系统托盘「设置」菜单
    quit_requested    : 系统托盘「退出」菜单
    """

    toggle_requested = Signal()
    restart_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__()

        # ── 状态 ──────────────────────────────────────────────────
        self.auto_hidden = True          # 初始为变暗状态
        self._drag_pos: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._side_panel = None          # SidePanel 引用（可选）
        self._capsule_visible = True     # 胶囊可见性（托盘菜单「隐藏」切换用）

        # ── 窗口 & 托盘 ──────────────────────────────────────────
        self._setup_window()
        self._setup_idle_timer()
        self._setup_system_tray()

        # ── 初始吸附到左边缘 ─────────────────────────────────────
        self.snap_to_half_hidden()

        logger.info("CapsuleWindow 初始化完成")

    # ═══════════════════════════════════════════════════════════════
    # 窗口设置
    # ═══════════════════════════════════════════════════════════════

    def _setup_window(self) -> None:
        """配置无边框、透明、置顶、无任务栏图标窗口。"""
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(CAPSULE_SIZE, CAPSULE_SIZE)

    # ═══════════════════════════════════════════════════════════════
    # 绘制
    # ═══════════════════════════════════════════════════════════════

    def paintEvent(self, event) -> None:
        """绘制绿色渐变圆形胶囊 + 白色 "AI" 文字。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        opacity = 0.5 if self.auto_hidden else 1.0
        painter.setOpacity(opacity)

        w = self.width()
        h = self.height()
        margin = 1
        rect = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)

        # 绿色渐变（#3fb950 → #238636）
        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0.0, QColor("#3fb950"))
        gradient.setColorAt(1.0, QColor("#238636"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(rect)

        # 白色 "AI" 文字（带轻微阴影）
        shadow = QColor(0, 0, 0, 60)
        painter.setPen(QPen(shadow))
        font = QFont("Microsoft YaHei", 15, QFont.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(1, 1, w, h), Qt.AlignCenter, "AI")
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, "AI")

        painter.end()

    # ═══════════════════════════════════════════════════════════════
    # 鼠标事件 — 拖拽 & 点击
    # ═══════════════════════════════════════════════════════════════

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """记录拖拽偏移和按下位置，重置空闲计时器及变暗状态。"""
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            self._press_pos = self.pos()
            self._reset_idle()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """拖拽移动胶囊。"""
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self._press_pos + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """释放鼠标：移动距离小于阈值则视为单击，否则吸附回左边缘。"""
        if event.button() == Qt.LeftButton:
            if self._drag_pos is not None:
                total_delta = event.globalPosition().toPoint() - self._drag_pos
                distance = math.hypot(total_delta.x(), total_delta.y())

                if distance < CLICK_THRESHOLD:
                    logger.debug("单击胶囊 → 发射 toggle_requested")
                    self.toggle_requested.emit()
                    # 单击也让胶囊亮起
                    if self.auto_hidden:
                        self.auto_hidden = False
                        self.update()
                        self._idle_timer.start()

                # 无论拖拽还是点击，释放后吸附到左边缘
                self.snap_to_half_hidden()

            self._drag_pos = None
            self._press_pos = None

        super().mouseReleaseEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        """鼠标进入胶囊区域 → 变亮 + 重置空闲计时器。"""
        if self.auto_hidden:
            self.auto_hidden = False
            self.update()
        self._reset_idle()
        super().enterEvent(event)

    # ═══════════════════════════════════════════════════════════════
    # 吸附动画
    # ═══════════════════════════════════════════════════════════════

    def snap_to_half_hidden(self) -> None:
        """以动画方式吸附到屏幕左边缘，仅露出右半部分。

        目标位置：x = 屏幕左边缘 - 胶囊宽度/2，y = 屏幕垂直居中。
        """
        if not self._capsule_visible:
            return

        screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        if screen is None:
            return

        geom = screen.availableGeometry()
        target_x = geom.left() - CAPSULE_SIZE // 2
        target_y = geom.center().y() - CAPSULE_SIZE // 2
        target = QPoint(target_x, target_y)

        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(SNAP_DURATION)
        anim.setStartValue(self.pos())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()

        logger.debug("吸附动画 → (%d, %d)", target_x, target_y)

    # ═══════════════════════════════════════════════════════════════
    # 空闲计时器
    # ═══════════════════════════════════════════════════════════════

    def _setup_idle_timer(self) -> None:
        """10 秒无交互后变暗，鼠标进入或点击时重新亮起。"""
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(IDLE_TIMEOUT)
        self._idle_timer.timeout.connect(self._on_idle_timeout)
        self._idle_timer.start()

    def _on_idle_timeout(self) -> None:
        """空闲超时：胶囊变暗。"""
        if not self.auto_hidden:
            self.auto_hidden = True
            self.update()
            logger.debug("空闲超时 → 胶囊变暗")

    def _reset_idle(self) -> None:
        """重置空闲计时器，同时取消变暗状态。"""
        self._idle_timer.start()
        if self.auto_hidden:
            self.auto_hidden = False
            self.update()

    # ═══════════════════════════════════════════════════════════════
    # 系统托盘
    # ═══════════════════════════════════════════════════════════════

    def _setup_system_tray(self) -> None:
        """创建系统托盘图标 + 右键菜单。"""
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_make_tray_icon())
        self._tray.setToolTip("AI 办公助手")

        # ── 右键菜单 ──────────────────────────────────────────────
        menu = QMenu()

        show_action = QAction("显示胶囊", menu)
        show_action.triggered.connect(self._on_tray_show)
        menu.addAction(show_action)

        hide_action = QAction("隐藏", menu)
        hide_action.triggered.connect(self._on_tray_hide)
        menu.addAction(hide_action)

        menu.addSeparator()

        settings_action = QAction("设置", menu)
        settings_action.triggered.connect(self.settings_requested.emit)
        menu.addAction(settings_action)

        restart_action = QAction("重启", menu)
        restart_action.triggered.connect(self.restart_requested.emit)
        menu.addAction(restart_action)

        menu.addSeparator()

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)

        # 双击托盘图标 → 切换胶囊
        self._tray.activated.connect(self._on_tray_activated)

        self._tray.show()
        logger.info("系统托盘初始化完成")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """双击托盘图标 → 切换胶囊可见性。"""
        if reason == QSystemTrayIcon.DoubleClick:
            if self._capsule_visible:
                self._on_tray_hide()
            else:
                self._on_tray_show()

    def _on_tray_show(self) -> None:
        """托盘菜单「显示胶囊」。"""
        if not self._capsule_visible:
            self._capsule_visible = True
            self.show()
            self.snap_to_half_hidden()
            logger.info("托盘菜单 → 显示胶囊")

    def _on_tray_hide(self) -> None:
        """托盘菜单「隐藏」。"""
        if self._capsule_visible:
            self._capsule_visible = False
            self.hide()
            logger.info("托盘菜单 → 隐藏胶囊")

    # ═══════════════════════════════════════════════════════════════
    # 公开方法
    # ═══════════════════════════════════════════════════════════════

    def set_side_panel(self, panel) -> None:
        """关联 SidePanel 引用，供 toggle 逻辑使用。"""
        self._side_panel = panel
        logger.debug("已关联 SidePanel: %s", type(panel).__name__)

    def show_notification(self, title: str, msg: str, duration: int = 3000) -> None:
        """通过系统托盘弹出气泡通知。"""
        if self._tray.supportsMessages():
            self._tray.showMessage(
                title, msg, QSystemTrayIcon.Information, duration
            )
            logger.info("气泡通知: %s – %s", title, msg)
        else:
            logger.warning("当前系统不支持气泡通知")

    def hide_capsule(self) -> None:
        """隐藏胶囊（不退出托盘）。"""
        self._on_tray_hide()

    def show_capsule(self) -> None:
        """显示胶囊。"""
        self._on_tray_show()

    def cleanup(self) -> None:
        """退出前清理：隐藏托盘和胶囊。"""
        self._tray.hide()
        self.hide()
        logger.info("CapsuleWindow 已清理")
