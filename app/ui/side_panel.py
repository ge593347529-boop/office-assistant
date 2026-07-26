"""WorkBuddy/CodeBuddy 风格三栏聊天面板 — 深色毛玻璃 + 完整动效"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PySide6.QtCore import (
    Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QRect,
    QThread, QRectF,
)
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QLinearGradient, QTextCursor,
    QCloseEvent,
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QTextEdit, QPushButton, QLabel, QFrame, QSizePolicy, QProgressBar,
    QApplication, QSplitter,
)

from app.config.settings import AppConfig
from app.core.inference import InferenceEngine, TaskResult
from app.core.executor import TaskExecutor, ExecutionResult
from app.core.memory import MemoryStore
from app.core.conversation import ConversationManager
from app.ui.confirm_card import ConfirmCard

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Design constants — dark glassmorphism
# ═══════════════════════════════════════════════════════════════
_CLR_BG = "#0d1117"
_CLR_GLASS = "rgba(22, 27, 34, 0.94)"
_CLR_SURFACE = "#161b22"
_CLR_BORDER = "rgba(255, 255, 255, 0.08)"
_CLR_BORDER_FOCUS = "rgba(255, 255, 255, 0.15)"
_CLR_TEXT = "#c9d1d9"
_CLR_TEXT_MUTED = "#8b949e"
_CLR_ACCENT = "#6ecbf5"
_CLR_USER_BUBBLE = "#1a2332"
_CLR_AI_BUBBLE = "transparent"
_CLR_SEND_BTN = "#238636"
_CLR_SEND_HOVER = "#2ea043"
_CLR_NAV_BG = "rgba(13, 17, 23, 0.97)"
_CLR_RESULT_BG = "rgba(13, 17, 23, 0.97)"

_NAV_WIDTH = 180
_RESULT_WIDTH = 300
_CURSOR_BLINK_MS = 500
_SLIDE_DURATION = 350
_TYPEWRITER_INTERVAL = 25  # ms per char

FONT_FAMILY = '"Segoe UI", system-ui, -apple-system, sans-serif'


# ═══════════════════════════════════════════════════════════════
# Typewriter thread — non-blocking
# ═══════════════════════════════════════════════════════════════
class TypewriterThread(QThread):
    char_written = Signal(int, str)  # bubble_index, incremental full text

    def __init__(self, full_text: str, bubble_index: int, parent=None):
        super().__init__(parent)
        self._text = full_text
        self._idx = bubble_index
        self._pos = 0

    def run(self) -> None:
        while self._pos < len(self._text):
            self._pos += 1
            self.char_written.emit(self._idx, self._text[:self._pos])
            self.msleep(_TYPEWRITER_INTERVAL)

    def stop(self) -> None:
        self._pos = len(self._text)


# ═══════════════════════════════════════════════════════════════
# Custom cursor — blinking 3px #6ecbf5
# ═══════════════════════════════════════════════════════════════
class _ChatInput(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatInput")
        self.setPlaceholderText("输入你想做的事情... (Enter 发送, Shift+Enter 换行)")
        self.setMaximumHeight(120)
        self.setMinimumHeight(42)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        font = QFont()
        font.setFamilies([FONT_FAMILY])
        font.setPointSize(13)
        self.setFont(font)
        self.document().setDefaultFont(font)

        # Blinking cursor
        self._cursor_visible = True
        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._toggle_cursor)
        self._cursor_timer.start(_CURSOR_BLINK_MS)
        self._custom_cursor_color = QColor(_CLR_ACCENT)

    def _toggle_cursor(self) -> None:
        self._cursor_visible = not self._cursor_visible
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.hasFocus() and self._cursor_visible:
            cursor = self.textCursor()
            if not cursor.hasSelection():
                rect = self.cursorRect()
                if rect.isValid():
                    painter = QPainter(self.viewport())
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(self._custom_cursor_color))
                    painter.drawRect(
                        rect.x(), rect.y(), 3,
                        self.fontMetrics().height()
                    )
                    painter.end()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._cursor_visible = True
        self._cursor_timer.start()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._cursor_timer.stop()
        self.viewport().update()


# ═══════════════════════════════════════════════════════════════
# Message widget — slide-in + fade animation
# ═══════════════════════════════════════════════════════════════
class MessageWidget(QFrame):
    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self._role = role
        self.setObjectName(f"Msg_{role}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.PlainText)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._label)

        self._time_label = QLabel(datetime.now().strftime("%H:%M"))
        layout.addWidget(self._time_label)

        self._setup_style()
        self.setVisible(False)

    def _setup_style(self) -> None:
        if self._role == "user":
            self.setStyleSheet(
                f"QFrame#{self.objectName()} {{"
                f"  background-color: {_CLR_USER_BUBBLE};"
                f"  border: 1px solid {_CLR_BORDER};"
                f"  border-radius: 12px;"
                f"  padding: 10px 14px;"
                f"}}"
            )
            self._label.setStyleSheet(
                f"color: {_CLR_TEXT}; font-size: 14px; background: transparent;"
                f"font-family: {FONT_FAMILY};"
            )
            self._time_label.setStyleSheet(
                f"color: {_CLR_TEXT_MUTED}; font-size: 10px; background: transparent;"
            )
            self._time_label.setAlignment(Qt.AlignRight)
        else:
            self.setStyleSheet(
                f"QFrame#{self.objectName()} {{"
                f"  background-color: transparent;"
                f"  border: none;"
                f"  padding: 8px 12px;"
                f"}}"
            )
            self._label.setStyleSheet(
                f"color: {_CLR_TEXT}; font-size: 14px; background: transparent;"
                f"font-family: {FONT_FAMILY};"
            )
            self._time_label.setStyleSheet(
                f"color: {_CLR_TEXT_MUTED}; font-size: 10px; background: transparent;"
            )

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def play_slide_in(self, from_right: bool = True) -> None:
        self.setVisible(True)
        w = self.width() or 400
        start_x = w if from_right else -w

        # Position animation
        self._pos_anim = QPropertyAnimation(self, b"pos")
        self._pos_anim.setDuration(_SLIDE_DURATION)
        self._pos_anim.setStartValue(QPoint(start_x, self.y()))
        self._pos_anim.setEndValue(QPoint(0, self.y()))
        self._pos_anim.setEasingCurve(QEasingCurve.OutCubic)

        # Opacity via windowOpacity-like effect — use graphics effect
        self._opacity_effect = self._make_fade_in()
        self.setGraphicsEffect(self._opacity_effect)

        self._pos_anim.start()

    def _make_fade_in(self):
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(_SLIDE_DURATION)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        return effect


# ═══════════════════════════════════════════════════════════════
# Left navigation panel — 180px
# ═══════════════════════════════════════════════════════════════
class NavPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavPanel")
        self.setFixedWidth(_NAV_WIDTH)
        self.setStyleSheet(f"""
            #NavPanel {{
                background-color: {_CLR_NAV_BG};
                border-right: 1px solid {_CLR_BORDER};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(8)

        title = QLabel("AI 办公助手")
        title.setStyleSheet(
            f"color: {_CLR_TEXT}; font-size: 14px; font-weight: 700;"
            f"font-family: {FONT_FAMILY}; background: transparent; padding: 4px 0 12px 0;"
        )
        layout.addWidget(title)

        # Menu items
        menu_style = (
            f"QPushButton {{"
            f"  color: {_CLR_TEXT_MUTED}; background: transparent; border: none;"
            f"  border-radius: 6px; padding: 8px 10px; text-align: left;"
            f"  font-size: 13px; font-family: {FONT_FAMILY};"
            f"}}"
            f"QPushButton:hover {{ background: {_CLR_SURFACE}; color: {_CLR_TEXT}; }}"
        )

        self._chat_btn = QPushButton("💬 对话")
        self._chat_btn.setStyleSheet(menu_style)
        layout.addWidget(self._chat_btn)

        self._tasks_btn = QPushButton("📋 任务")
        self._tasks_btn.setStyleSheet(menu_style)
        layout.addWidget(self._tasks_btn)

        self._history_btn = QPushButton("🕐 历史")
        self._history_btn.setStyleSheet(menu_style)
        layout.addWidget(self._history_btn)

        self._settings_btn = QPushButton("⚙️ 设置")
        self._settings_btn.setStyleSheet(menu_style)
        layout.addWidget(self._settings_btn)

        layout.addStretch()

        ver = QLabel("v0.2.0")
        ver.setStyleSheet(
            f"color: {_CLR_TEXT_MUTED}; font-size: 11px; background: transparent;"
        )
        layout.addWidget(ver)


# ═══════════════════════════════════════════════════════════════
# Right result panel — 300px
# ═══════════════════════════════════════════════════════════════
class ResultPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ResultPanel")
        self.setFixedWidth(_RESULT_WIDTH)
        self.setStyleSheet(f"""
            #ResultPanel {{
                background-color: {_CLR_RESULT_BG};
                border-left: 1px solid {_CLR_BORDER};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(8)

        title = QLabel("成果")
        title.setStyleSheet(
            f"color: {_CLR_TEXT}; font-size: 14px; font-weight: 700;"
            f"font-family: {FONT_FAMILY}; background: transparent; padding: 4px 0 8px 0;"
        )
        layout.addWidget(title)

        self._content = QLabel("暂无任务结果")
        self._content.setWordWrap(True)
        self._content.setAlignment(Qt.AlignTop)
        self._content.setStyleSheet(
            f"color: {_CLR_TEXT_MUTED}; font-size: 12px; background: transparent;"
            f"font-family: {FONT_FAMILY};"
        )
        layout.addWidget(self._content, stretch=1)

    def set_result(self, text: str) -> None:
        self._content.setText(text)


# ═══════════════════════════════════════════════════════════════
# Main SidePanel — three-panel layout
# ═══════════════════════════════════════════════════════════════
class SidePanel(QMainWindow):
    settings_requested = Signal()
    panel_closed = Signal()

    def __init__(self, config: AppConfig, memory: MemoryStore) -> None:
        super().__init__()
        self.config = config
        self.memory = memory
        self.conv = ConversationManager(max_history=config.max_history)
        self.engine = InferenceEngine(config, memory)
        self.executor = TaskExecutor(config, memory)

        self._current_task: Optional[TaskResult] = None
        self._chrome_connected: bool = False
        self._msg_widgets: list[MessageWidget] = []
        self._typewriter: Optional[TypewriterThread] = None
        self._target_x: int = 0
        self._slide_anim: Optional[QPropertyAnimation] = None
        self._is_sliding: bool = False

        self._setup_window()
        self._setup_ui()
        self._hide_offscreen()

        logger.info("SidePanel (WorkBuddy-style) initialized")

    # ── Window ──────────────────────────────────────────────────
    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        screen = QApplication.primaryScreen()
        screen_geom = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        w = int(screen_geom.width() * 0.42)
        h = int(screen_geom.height() * 0.88)
        x = int(screen_geom.width() * 0.01)
        y = int((screen_geom.height() - h) / 2)

        self._target_x = x
        self.setGeometry(x, y, w, h)
        self.setMinimumWidth(700)
        self.setMinimumHeight(450)

    def _hide_offscreen(self) -> None:
        self.move(-self.width(), self.y())

    # ── Three-panel UI ──────────────────────────────────────────
    def _setup_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("PanelRoot")
        root.setStyleSheet(f"""
            #PanelRoot {{
                background-color: {_CLR_GLASS};
                border: 1px solid {_CLR_BORDER};
                border-radius: 12px;
            }}
        """)
        self.setCentralWidget(root)

        h_layout = QHBoxLayout(root)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        # Left nav
        self.nav = NavPanel(root)
        h_layout.addWidget(self.nav)

        # Center chat
        self.chat_area = self._build_chat_area(root)
        h_layout.addWidget(self.chat_area, stretch=1)

        # Right results
        self.results = ResultPanel(root)
        h_layout.addWidget(self.results)

    def _build_chat_area(self, parent) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title bar
        title_bar = QWidget()
        title_bar.setObjectName("TitleBar")
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet(f"""
            #TitleBar {{
                background-color: {_CLR_SURFACE};
                border-bottom: 1px solid {_CLR_BORDER};
                border-radius: 0 12px 0 0;
            }}
        """)
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(12, 0, 8, 0)

        title_lbl = QLabel("对话")
        title_lbl.setStyleSheet(
            f"color: {_CLR_TEXT}; font-size: 13px; font-weight: 600;"
            f"font-family: {FONT_FAMILY}; background: transparent;"
        )
        tb_layout.addWidget(title_lbl)
        tb_layout.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {_CLR_TEXT_MUTED};"
            f"  border-radius: 4px; font-size: 14px; }}"
            f"QPushButton:hover {{ background: #da3633; color: #fff; }}"
        )
        close_btn.clicked.connect(self.hide_panel)
        tb_layout.addWidget(close_btn)
        layout.addWidget(title_bar)

        # Message scroll area
        self._scroll = QScrollArea()
        self._scroll.setObjectName("ChatScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(f"""
            #ChatScroll {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 5px; margin: 4px 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.12); border-radius: 3px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.2); }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        self._msg_container = QWidget()
        self._msg_container.setObjectName("MsgContainer")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(16, 12, 16, 12)
        self._msg_layout.setSpacing(12)

        self._msg_spacer = QWidget()
        self._msg_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._msg_layout.addWidget(self._msg_spacer)

        self._scroll.setWidget(self._msg_container)
        layout.addWidget(self._scroll, stretch=1)

        # Typing indicator
        self._typing_label = QLabel()
        self._typing_label.setObjectName("TypingLabel")
        self._typing_label.setAlignment(Qt.AlignCenter)
        self._typing_label.setFixedHeight(28)
        self._typing_label.setVisible(False)
        self._typing_label.setStyleSheet(
            f"color: {_CLR_TEXT_MUTED}; font-size: 12px; background: transparent;"
        )
        layout.addWidget(self._typing_label)

        # Input bar
        input_bar = QWidget()
        input_bar.setObjectName("InputBar")
        input_bar.setStyleSheet(f"""
            #InputBar {{
                background-color: {_CLR_SURFACE};
                border-top: 1px solid {_CLR_BORDER};
            }}
        """)
        ib_layout = QHBoxLayout(input_bar)
        ib_layout.setContentsMargins(12, 8, 12, 8)
        ib_layout.setSpacing(8)

        self._input = _ChatInput()
        ib_layout.addWidget(self._input, stretch=1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setObjectName("SendBtn")
        self._send_btn.setFixedSize(68, 38)
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setStyleSheet(f"""
            QPushButton#SendBtn {{
                background-color: {_CLR_SEND_BTN};
                color: #ffffff; border: none; border-radius: 6px;
                font-size: 13px; font-weight: 600;
                font-family: {FONT_FAMILY};
            }}
            QPushButton#SendBtn:hover {{ background-color: {_CLR_SEND_HOVER}; }}
            QPushButton#SendBtn:pressed {{ background-color: #196c2e; }}
            QPushButton#SendBtn:disabled {{
                background-color: #21262d; color: #484f58;
            }}
        """)
        self._send_btn.clicked.connect(self._on_send)
        ib_layout.addWidget(self._send_btn)
        layout.addWidget(input_bar)

        # Enter to send
        self._input.installEventFilter(self)

        return widget

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        if obj is self._input and event.type() == QEvent.KeyPress:
            ke = event
            if ke.key() in (Qt.Key_Return, Qt.Key_Enter):
                if not (ke.modifiers() & Qt.ShiftModifier):
                    self._on_send()
                    return True
        return super().eventFilter(obj, event)

    # ── Send + pulse animation ──────────────────────────────────
    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()
        self._pulse_send_button()
        self._handle_user_input(text)

    def _pulse_send_button(self) -> None:
        btn = self._send_btn
        orig = btn.size()
        # Shrink
        shrink = QPropertyAnimation(btn, b"geometry")
        shrink.setDuration(100)
        r = btn.geometry()
        shrink.setStartValue(r)
        cx, cy = r.center().x(), r.center().y()
        sw, sh = int(r.width() * 0.85), int(r.height() * 0.85)
        shrink.setEndValue(QRect(cx - sw // 2, cy - sh // 2, sw, sh))
        shrink.setEasingCurve(QEasingCurve.OutQuad)
        # Restore
        restore = QPropertyAnimation(btn, b"geometry")
        restore.setDuration(150)
        restore.setStartValue(QRect(cx - sw // 2, cy - sh // 2, sw, sh))
        restore.setEndValue(r)
        restore.setEasingCurve(QEasingCurve.OutBack)
        shrink.finished.connect(restore.start)
        shrink.start()

    # ── AI Pipeline ─────────────────────────────────────────────
    def _handle_user_input(self, text: str) -> None:
        self._show_typing(True)
        self._add_user_message(text)

        try:
            result = self.engine.infer(text, user_chrome_connected=self._chrome_connected)
        except Exception:
            self._show_typing(False)
            self._add_ai_message("抱歉，处理请求时出错，请重试。")
            return

        self._show_typing(False)

        if result.task_type == "general_chat":
            reply = result.clarification_question or result.raw_response or "收到。"
            self._add_ai_message(reply)
        elif result.needs_clarification:
            q = result.clarification_question or "请进一步描述。"
            self._add_ai_message(q)
        else:
            self._show_confirm_card(result)

    def _add_user_message(self, text: str) -> None:
        msg = MessageWidget("user", self._msg_container)
        msg.set_text(text)

        wrapper = QWidget(self._msg_container)
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addStretch()
        msg.setMaximumWidth(int(self.chat_area.width() * 0.75))
        msg.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        wl.addWidget(msg)
        self._insert_msg(wrapper)

        msg.play_slide_in(from_right=True)

    def _add_ai_message(self, text: str) -> None:
        msg = MessageWidget("assistant", self._msg_container)

        wrapper = QWidget(self._msg_container)
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        msg.setMaximumWidth(int(self.chat_area.width() * 0.85))
        wl.addWidget(msg)
        wl.addStretch()
        self._insert_msg(wrapper)

        msg.set_text("")
        msg.play_slide_in(from_right=False)

        idx = len(self._msg_widgets)
        self._msg_widgets.append(msg)

        # Typewriter
        self._typewriter = TypewriterThread(text, idx, self)
        self._typewriter.char_written.connect(self._on_char_written)
        self._typewriter.start()

    def _on_char_written(self, idx: int, partial: str) -> None:
        if idx < len(self._msg_widgets):
            self._msg_widgets[idx].set_text(partial)
            self._scroll_to_bottom()

    def _show_typing(self, show: bool) -> None:
        self._typing_label.setVisible(show)
        if show:
            self._typing_label.setText("● ○ ○")
            self._typing_dots = 0
            if not hasattr(self, '_typing_timer'):
                self._typing_timer = QTimer(self)
                self._typing_timer.timeout.connect(self._animate_typing)
            self._typing_timer.start(400)
        else:
            if hasattr(self, '_typing_timer'):
                self._typing_timer.stop()
            self._typing_label.setText("")

    def _animate_typing(self) -> None:
        dots = [
            "● ○ ○", "○ ● ○", "○ ○ ●"
        ]
        self._typing_label.setText(dots[self._typing_dots % 3])
        self._typing_dots = getattr(self, '_typing_dots', 0) + 1

    def _insert_msg(self, widget: QWidget) -> None:
        count = self._msg_layout.count()
        self._msg_layout.insertWidget(count - 1, widget)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(30, lambda: (
            self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            )
        ))

    # ── Confirm card ────────────────────────────────────────────
    def _show_confirm_card(self, task: TaskResult) -> None:
        self._current_task = task
        card = ConfirmCard()
        card.confirmed.connect(lambda p: self._handle_execute(card, p))
        card.cancelled.connect(lambda: self._on_cancelled(card))
        card.show_task(task)
        self._insert_msg(card)
        self.results.set_result(f"任务: {task.task_type}\n系统: {task.system_name}")

    def _handle_execute(self, card: ConfirmCard, adjusted_params: dict) -> None:
        if not self._current_task:
            return
        self._current_task.params.update(adjusted_params)

        card.show_progress("执行中...", 0.0)
        result = self.executor.execute(
            self._current_task, on_progress=card.show_progress
        )
        card.show_result(result)
        self.conv.add_assistant_message(result.message)

        # Save memory
        task = self._current_task
        files_used = []
        if task.params.get("data_source"):
            files_used.append(task.params["data_source"])
        self.memory.record_task(
            user_input=getattr(task, 'user_input', '') or '',
            task_type=task.task_type,
            system_name=task.system_name,
            params=task.params,
            files_used=files_used,
        )
        self.results.set_result(f"完成: {result.message}")

    def _on_cancelled(self, card: ConfirmCard) -> None:
        card.clear()
        self._current_task = None

    # ── Panel visibility ────────────────────────────────────────
    def toggle(self) -> None:
        if self.isVisible() and not self._is_sliding:
            self.hide_panel()
        elif not self.isVisible():
            self.show_panel()

    def show_panel(self) -> None:
        if self._is_sliding:
            return
        self._is_sliding = True
        self.setVisible(True)
        self._slide_anim = QPropertyAnimation(self, b"pos")
        self._slide_anim.setDuration(300)
        self._slide_anim.setStartValue(QPoint(-self.width(), self.y()))
        self._slide_anim.setEndValue(QPoint(self._target_x, self.y()))
        self._slide_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._slide_anim.finished.connect(lambda: setattr(self, '_is_sliding', False))
        self._slide_anim.start()

    def hide_panel(self) -> None:
        if self._is_sliding:
            return
        self._is_sliding = True
        self._slide_anim = QPropertyAnimation(self, b"pos")
        self._slide_anim.setDuration(300)
        self._slide_anim.setStartValue(self.pos())
        self._slide_anim.setEndValue(QPoint(-self.width(), self.y()))
        self._slide_anim.setEasingCurve(QEasingCurve.InCubic)
        self._slide_anim.finished.connect(self._on_hide_done)
        self._slide_anim.start()

    def _on_hide_done(self) -> None:
        self._is_sliding = False
        self.setVisible(False)
        self._hide_offscreen()
        self.panel_closed.emit()

    def show_settings(self) -> None:
        self.settings_requested.emit()

    def check_ollama(self) -> bool:
        available = self.engine.check_ollama_available()
        status = "已连接 AI 服务 ✅" if available else "⚠️ 未连接 AI 服务"
        self.results.set_result(status)
        return available

    def set_chrome_connected(self, connected: bool) -> None:
        self._chrome_connected = connected

    def focus_input(self) -> None:
        self._input.setFocus()

    def _add_system_message(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"color: {_CLR_TEXT_MUTED}; font-size: 12px; background: transparent;"
            f"font-family: {FONT_FAMILY}; padding: 6px 0;"
        )
        self._insert_msg(lbl)
