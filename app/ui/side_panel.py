# -*- coding: utf-8 -*-
"""Side panel window -- frameless, semi-transparent chat panel that slides in from the left edge.

Architecture
------------
- **SidePanel** (QMainWindow): top-level frameless window with slide animation.
- **Internal widgets**:
  - ``_MessageBubble``: user / assistant / system message bubbles.
  - ``_TypingIndicator``: animated three-dot indicator.
  - **Title bar**: settings (gear) and close (X) buttons.
  - **Chat area**: scrollable message list with auto-scroll.
  - **Input area**: QTextEdit + send button, Enter-to-send / Shift+Enter-newline.
- **Integration**:
  - Reuses ``ConfirmCard`` from ``app.ui.confirm_card`` for task confirmation.
  - Wires up ``InferenceEngine``, ``TaskExecutor``, ``ConversationManager``, ``MemoryStore``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QScrollArea, QTextEdit, QPushButton, QLabel, QFrame,
    QSizePolicy, QApplication, QProgressBar,
)
from PySide6.QtCore import (
    Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QEvent,
)
from PySide6.QtGui import (
    QColor, QPalette, QFont, QTextCursor, QKeyEvent, QMouseEvent, QPainter,
)

from app.config.settings import AppConfig
from app.core.inference import InferenceEngine, TaskResult
from app.core.executor import TaskExecutor, ExecutionResult
from app.core.memory import MemoryStore
from app.core.conversation import ConversationManager
from app.ui.confirm_card import ConfirmCard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PANEL_WIDTH_RATIO = 0.25        # 25 % of screen width
_PANEL_HEIGHT_RATIO = 0.90       # 90 % of screen height
_LEFT_MARGIN_RATIO = 0.01        # 1 % margin from left edge
_SLIDE_DURATION_MS = 300         # slide animation duration
_TYPING_INTERVAL_MS = 400        # typing dot cycle interval

_TITLE_BAR_HEIGHT = 40
_INPUT_MAX_HEIGHT = 120
_INPUT_MIN_HEIGHT = 38

# Codex dark color palette
_CLR_BG = "#0d1117"
_CLR_BG_GLASS = "rgba(18, 22, 28, 0.92)"
_CLR_SURFACE = "#161b22"
_CLR_BORDER = "rgba(255, 255, 255, 0.12)"
_CLR_BORDER_LIGHT = "rgba(255, 255, 255, 0.06)"
_CLR_TEXT_PRIMARY = "#e6edf3"
_CLR_TEXT_SECONDARY = "#8b949e"
_CLR_TEXT_MUTED = "#484f58"
_CLR_USER_BUBBLE = "#1f6feb"
_CLR_USER_BUBBLE_BORDER = "#388bfd"
_CLR_ASSISTANT_BUBBLE = "rgba(255, 255, 255, 0.06)"
_CLR_ASSISTANT_BORDER = "rgba(255, 255, 255, 0.1)"
_CLR_BTN_SEND = "#238636"
_CLR_BTN_SEND_HOVER = "#2ea043"
_CLR_BTN_SEND_PRESSED = "#196c2e"
_CLR_ACCENT = "#58a6ff"


# ---------------------------------------------------------------------------
# Message Bubble
# ---------------------------------------------------------------------------

class _MessageBubble(QFrame):
    """A single chat message bubble.

    Parameters
    ----------
    role : str
        One of ``"user"``, ``"assistant"``, ``"system"``.
    content : str
        The message text.
    parent : QWidget | None
        Parent widget.
    """

    def __init__(
        self,
        role: str,
        content: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SideBubble")

        self._role = role
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Content label
        self._label = QLabel(content)
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.PlainText)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._label)

        # Timestamp
        ts = datetime.now().strftime("%H:%M")
        self._time_label = QLabel(ts)
        layout.addWidget(self._time_label)

        self._apply_role_style()

    # ── Style per role ──────────────────────────────────────────────────

    def _apply_role_style(self) -> None:
        """Set bubble background, alignment, and text colors by role."""
        if self._role == "user":
            self.setStyleSheet(self._bubble_qss(
                bg=_CLR_USER_BUBBLE,
                border=_CLR_USER_BUBBLE_BORDER,
                radius="12px 12px 4px 12px",
            ))
            align = Qt.AlignRight
            self._label.setStyleSheet(
                "color: #ffffff; font-size: 14px; background: transparent; border: none;"
            )
            self._time_label.setStyleSheet(
                "color: rgba(255,255,255,0.5); font-size: 10px; background: transparent;"
            )
        elif self._role == "assistant":
            self.setStyleSheet(self._bubble_qss(
                bg=_CLR_ASSISTANT_BUBBLE,
                border=_CLR_ASSISTANT_BORDER,
                radius="12px 12px 12px 4px",
            ))
            align = Qt.AlignLeft
            self._label.setStyleSheet(
                "color: #c9d1d9; font-size: 14px; background: transparent; border: none;"
            )
            self._time_label.setStyleSheet(
                "color: #484f58; font-size: 10px; background: transparent;"
            )
        else:  # system
            self.setStyleSheet(self._bubble_qss(
                bg="transparent",
                border="none",
                radius="0px",
                padding="6px 10px",
            ))
            align = Qt.AlignCenter
            self._label.setStyleSheet(
                "color: #484f58; font-size: 12px; background: transparent; border: none;"
            )
            self._time_label.setStyleSheet(
                "color: #30363d; font-size: 10px; background: transparent;"
            )

        self._label.setAlignment(align | Qt.AlignVCenter)
        self._time_label.setAlignment(align)

    @staticmethod
    def _bubble_qss(
        bg: str,
        border: str,
        radius: str,
        padding: str = "10px 14px",
    ) -> str:
        """Compose a QSS block for the bubble frame."""
        return (
            f"QFrame#SideBubble {{\n"
            f"  background-color: {bg};\n"
            f"  border: 1px solid {border};\n"
            f"  border-radius: {radius};\n"
            f"  padding: {padding};\n"
            f"}}"
        )


# ---------------------------------------------------------------------------
# Typing Indicator
# ---------------------------------------------------------------------------

class _TypingIndicator(QWidget):
    """Animated three-dot typing indicator (● ○ ○ → ○ ● ○ → ○ ○ ●)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TypingIndicator")
        self.setFixedHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 4, 16, 4)
        layout.setSpacing(0)

        # Left-align the label inside a row
        self._label = QLabel("● ○ ○")
        self._label.setObjectName("TypingLabel")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._label)
        row.addStretch()
        layout.addLayout(row)

        self._step = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ── Public ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin the dot animation."""
        self._step = 0
        self._label.setText("● ○ ○")
        self._timer.start(_TYPING_INTERVAL_MS)
        self.setVisible(True)

    def stop(self) -> None:
        """Stop the animation and hide the indicator."""
        self._timer.stop()
        self.setVisible(False)

    # ── Internal ────────────────────────────────────────────────────────

    def _tick(self) -> None:
        """Cycle through the three dot states."""
        patterns = [
            "● ○ ○",
            "○ ● ○",
            "○ ○ ●",
        ]
        self._label.setText(patterns[self._step % 3])
        self._step += 1


# ---------------------------------------------------------------------------
# Side Panel
# ---------------------------------------------------------------------------

class SidePanel(QMainWindow):
    """Frameless, semi-transparent side panel that slides in from the left edge.

    Contains the full AI office assistant chat interface: message bubbles,
    typing indicator, task confirmation cards, and input bar.

    Signals
    -------
    settings_requested :
        Emitted when the user clicks the settings (gear) button.
    panel_closed :
        Emitted when the panel finishes hiding (close button or hide_panel).
    """

    settings_requested = Signal()
    panel_closed = Signal()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        config: AppConfig,
        memory: MemoryStore,
    ) -> None:
        super().__init__()
        self.config = config
        self.memory = memory
        self.conv = ConversationManager(max_history=config.max_history)
        self.engine = InferenceEngine(config, memory)
        self.executor = TaskExecutor(config, memory)

        self._current_task: Optional[TaskResult] = None
        self._chrome_connected: bool = False
        self._slide_anim: Optional[QPropertyAnimation] = None
        self._is_sliding: bool = False
        self._target_x: int = 0

        self._setup_window()
        self._setup_ui()
        self._apply_global_style()

        logger.info("SidePanel initialized")

    # ------------------------------------------------------------------
    # Window properties
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        """Configure frameless, semi-transparent window geometry."""
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)

        screen = QApplication.primaryScreen()
        if screen is None:
            screen_geom = QRect(0, 0, 1920, 1080)
        else:
            screen_geom = screen.availableGeometry()

        w = int(screen_geom.width() * _PANEL_WIDTH_RATIO)
        h = int(screen_geom.height() * _PANEL_HEIGHT_RATIO)
        x = int(screen_geom.width() * _LEFT_MARGIN_RATIO)
        y = int((screen_geom.height() - h) / 2)

        self._target_x = x
        self.setGeometry(x, y, w, h)
        self.setMinimumWidth(280)
        self.setMinimumHeight(400)

        # Start off-screen for slide-in
        self._hide_offscreen()

    def _hide_offscreen(self) -> None:
        """Move the window off-screen to the left (hidden state)."""
        self.move(-self.width(), self.y())
        self.setVisible(False)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the panel: title bar, chat area, typing indicator, input bar."""
        # Root container widget (rounded corners + border)
        root = QWidget()
        root.setObjectName("PanelRoot")
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Title bar ──────────────────────────────────────────────────
        self._title_bar = self._build_title_bar()
        main_layout.addWidget(self._title_bar)

        # ── Chat scroll area ───────────────────────────────────────────
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("ChatScrollArea")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setFrameShape(QFrame.NoFrame)

        self._msg_container = QWidget()
        self._msg_container.setObjectName("MessageContainer")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(12, 8, 12, 8)
        self._msg_layout.setSpacing(8)

        # Bottom spacer keeps messages at the top when few
        self._msg_spacer = QWidget()
        self._msg_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._msg_layout.addWidget(self._msg_spacer)

        self._scroll_area.setWidget(self._msg_container)
        main_layout.addWidget(self._scroll_area, stretch=1)

        # ── Typing indicator ───────────────────────────────────────────
        self._typing_indicator = _TypingIndicator()
        self._typing_indicator.setVisible(False)
        main_layout.addWidget(self._typing_indicator)

        # ── Input bar ──────────────────────────────────────────────────
        self._input_area = self._build_input_bar()
        main_layout.addWidget(self._input_area)

    # ── Title bar ──────────────────────────────────────────────────────

    def _build_title_bar(self) -> QWidget:
        """Create the custom title bar: title + settings + close buttons."""
        bar = QWidget()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(_TITLE_BAR_HEIGHT)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(4)

        # Drag handle area (the title text)
        self._title_label = QLabel("AI 办公助手")
        self._title_label.setObjectName("TitleLabel")
        layout.addWidget(self._title_label)

        layout.addStretch()

        # Settings (gear) button
        self._btn_settings = QPushButton("⚙")  # ⚙
        self._btn_settings.setObjectName("TitleBtn")
        self._btn_settings.setFixedSize(28, 28)
        self._btn_settings.setCursor(Qt.PointingHandCursor)
        self._btn_settings.setToolTip("设置")
        self._btn_settings.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self._btn_settings)

        # Spacing between gear and X
        layout.addSpacing(4)

        # Close button
        self._btn_close = QPushButton("✕")  # ✕
        self._btn_close.setObjectName("TitleBtnClose")
        self._btn_close.setFixedSize(28, 28)
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.setToolTip("关闭面板")
        self._btn_close.clicked.connect(self.hide_panel)
        layout.addWidget(self._btn_close)

        return bar

    # ── Input bar ──────────────────────────────────────────────────────

    def _build_input_bar(self) -> QWidget:
        """Create the bottom input area: QTextEdit + send button."""
        bar = QWidget()
        bar.setObjectName("InputBar")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # Text input (single-line style, max 120px)
        self._text_edit = QTextEdit()
        self._text_edit.setObjectName("ChatInput")
        self._text_edit.setPlaceholderText("输入你想做的事情...")
        self._text_edit.setMaximumHeight(_INPUT_MAX_HEIGHT)
        self._text_edit.setMinimumHeight(_INPUT_MIN_HEIGHT)
        self._text_edit.setAcceptRichText(False)
        self._text_edit.setTabChangesFocus(True)
        self._text_edit.installEventFilter(self)

        font = QFont()
        font.setPointSize(13)
        self._text_edit.setFont(font)
        self._text_edit.document().setDefaultFont(font)

        layout.addWidget(self._text_edit, stretch=1)

        # Send button
        self._btn_send = QPushButton("发送")
        self._btn_send.setObjectName("SendButton")
        self._btn_send.setFixedHeight(36)
        self._btn_send.setMinimumWidth(64)
        self._btn_send.setCursor(Qt.PointingHandCursor)
        self._btn_send.clicked.connect(self._on_send_clicked)
        layout.addWidget(self._btn_send)

        return bar

    # ── Global stylesheet ──────────────────────────────────────────────

    def _apply_global_style(self) -> None:
        """Apply dark Codex + Python-island glass styles via QSS."""
        self.setStyleSheet(f"""
            QMainWindow {{
                background: transparent;
            }}

            #PanelRoot {{
                background-color: {_CLR_BG_GLASS};
                border: 1px solid {_CLR_BORDER};
                border-left: none;
                border-radius: 0 16px 16px 0;
            }}

            #TitleBar {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 rgba(255,255,255,0.03), stop:1 rgba(255,255,255,0.06));
                border-bottom: 1px solid {_CLR_BORDER_LIGHT};
                border-radius: 0 16px 0 0;
            }}

            #TitleLabel {{
                color: {_CLR_TEXT_PRIMARY};
                font-size: 13px;
                font-weight: 600;
                background: transparent;
                border: none;
                padding-left: 2px;
            }}

            #TitleBtn, #TitleBtnClose {{
                background: transparent;
                border: none;
                border-radius: 4px;
                color: {_CLR_TEXT_SECONDARY};
                font-size: 14px;
            }}
            #TitleBtn:hover, #TitleBtnClose:hover {{
                background-color: {_CLR_BORDER_LIGHT};
                color: {_CLR_TEXT_PRIMARY};
            }}
            #TitleBtnClose:hover {{
                background-color: #da3633;
                color: #ffffff;
            }}

            #ChatScrollArea {{
                background-color: transparent;
                border: none;
            }}

            #MessageContainer {{
                background-color: transparent;
            }}

            #TypingIndicator {{
                background-color: {_CLR_SURFACE};
                border-top: 1px solid {_CLR_BORDER_LIGHT};
            }}
            #TypingLabel {{
                color: {_CLR_TEXT_SECONDARY};
                font-size: 13px;
                background: transparent;
                border: none;
            }}

            #InputBar {{
                background-color: {_CLR_SURFACE};
                border-top: 1px solid {_CLR_BORDER_LIGHT};
            }}

            #ChatInput {{
                border: 1px solid {_CLR_BORDER};
                border-radius: 8px;
                padding: 8px 12px;
                background-color: {_CLR_BG};
                color: {_CLR_TEXT_PRIMARY};
                font-size: 14px;
                selection-background-color: {_CLR_ACCENT};
            }}
            #ChatInput:focus {{
                border: 1px solid {_CLR_ACCENT};
            }}

            #SendButton {{
                background-color: {_CLR_BTN_SEND};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 600;
                padding: 0 14px;
            }}
            #SendButton:hover {{
                background-color: {_CLR_BTN_SEND_HOVER};
            }}
            #SendButton:pressed {{
                background-color: {_CLR_BTN_SEND_PRESSED};
            }}
            #SendButton:disabled {{
                background-color: {_CLR_BORDER_LIGHT};
                color: {_CLR_TEXT_MUTED};
            }}

            QScrollBar:vertical {{
                background: rgba(255,255,255,0.02);
                width: 5px;
                margin: 4px 2px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.15);
                border-radius: 3px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255,255,255,0.25);
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

    # ------------------------------------------------------------------
    # Event filter -- Enter / Shift+Enter handling
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        """Intercept key presses in the input QTextEdit.

        Enter without Shift → send.
        Shift+Enter → insert newline.
        """
        if obj is self._text_edit and event.type() == QEvent.KeyPress:
            key_event = event
            if key_event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if not (key_event.modifiers() & Qt.ShiftModifier):
                    self._on_send_clicked()
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Send logic
    # ------------------------------------------------------------------

    def _on_send_clicked(self) -> None:
        """Extract text from the input and submit."""
        text = self._text_edit.toPlainText().strip()
        if not text:
            return
        self._text_edit.clear()
        self._handle_user_input(text)

    # ------------------------------------------------------------------
    # User input → AI pipeline
    # ------------------------------------------------------------------

    def _handle_user_input(self, text: str) -> None:
        """Process user message through the full AI pipeline.

        Flow
        ----
        1. Add user bubble to chat
        2. Show typing indicator
        3. Call ``engine.infer(text)``
        4. On result:
           - ``general_chat`` → display AI reply directly
           - ``needs_clarification`` → show clarification message
           - Task (other) → show ``ConfirmCard`` inline
        5. Hide typing indicator
        """
        # 1. Echo user message
        self._add_bubble("user", text)
        self.conv.add_user_message(text)

        # 2. Show typing
        self._set_processing(True)

        # 3. Run inference (use QTimer to keep UI responsive)
        self._run_inference(text)

    def _run_inference(self, text: str) -> None:
        """Call the inference engine and dispatch the result."""
        try:
            result: TaskResult = self.engine.infer(
                text,
                user_chrome_connected=self._chrome_connected,
            )
        except Exception:
            logger.exception("Inference failed for input: %r", text[:100])
            self._set_processing(False)
            self._add_bubble("assistant", "抱歉，处理您的请求时出现错误，请重试。")
            self.conv.add_assistant_message("抱歉，处理您的请求时出现错误，请重试。")
            return

        # 4. Turn off typing indicator
        self._set_processing(False)

        # 5. Route by result type
        if result.needs_clarification:
            question = result.clarification_question or "请进一步描述你的需求。"
            self._add_bubble("assistant", question)
            self.conv.add_assistant_message(question)

        elif result.task_type == "general_chat":
            reply = result.clarification_question or result.raw_response or "收到您的消息。"
            self._add_bubble("assistant", reply)
            self.conv.add_assistant_message(reply)

        else:
            # Task → show confirm card
            self._show_confirm_card(result)

    # ------------------------------------------------------------------
    # Confirm card
    # ------------------------------------------------------------------

    def _show_confirm_card(self, task: TaskResult) -> None:
        """Create a ``ConfirmCard``, wire signals, and embed in chat."""
        self._current_task = task

        card = ConfirmCard()
        card.confirmed.connect(lambda params: self._handle_execute(card, params))
        card.cancelled.connect(lambda: self._on_task_cancelled(card))
        card.modified.connect(lambda params: self._handle_execute(card, params))

        card.show_task(task)
        self._add_widget(card)

    # ------------------------------------------------------------------
    # Execute task
    # ------------------------------------------------------------------

    def _handle_execute(self, card: ConfirmCard, adjusted_params: dict) -> None:
        """Execute a confirmed task, streaming progress into the card."""
        if self._current_task is None:
            return

        self._current_task.params.update(adjusted_params)
        task = self._current_task

        card.show_progress("正在准备执行...", 0.0)

        try:
            exec_result: ExecutionResult = self.executor.execute(
                task,
                on_progress=card.show_progress,
            )
        except Exception:
            logger.exception("Execution failed")
            exec_result = ExecutionResult(
                success=False,
                message="执行过程中发生异常",
            )

        # Show result in the card
        card.show_result(exec_result)

        # Record to memory
        user_input = getattr(task, 'user_input', '') or ''
        files_used: list[str] = []
        if task.params.get("data_source"):
            files_used.append(str(task.params["data_source"]))
        if task.params.get("filepath"):
            files_used.append(str(task.params["filepath"]))
        if task.params.get("directory"):
            files_used.append(str(task.params["directory"]))

        self.memory.record_task(
            user_input=user_input,
            task_type=task.task_type,
            system_name=task.system_name,
            params=task.params,
            files_used=files_used,
        )
        self.conv.add_assistant_message(
            f"任务完成：{exec_result.message}"
        )

        self._add_bubble(
            "system",
            f"{'[OK]' if exec_result.success else '[X]'} {exec_result.message}"
        )

    def _on_task_cancelled(self, card: ConfirmCard) -> None:
        """Handle user cancelling a task confirmation."""
        card.clear()
        self._add_bubble("system", "已取消任务。")
        self._current_task = None

    # ------------------------------------------------------------------
    # Chat display helpers
    # ------------------------------------------------------------------

    def _add_bubble(self, role: str, content: str) -> None:
        """Add a message bubble to the chat scroll area."""
        bubble = _MessageBubble(role, content, parent=self._msg_container)

        if role in ("user", "assistant"):
            wrapper = QWidget(self._msg_container)
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(0, 0, 0, 0)

            # Bubble width: ~75% of panel width
            panel_w = self.width()
            bubble_w = max(int(panel_w * 0.75), 200)
            bubble.setMaximumWidth(bubble_w)
            bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

            if role == "user":
                wrapper_layout.addStretch()
                wrapper_layout.addWidget(bubble)
            else:
                wrapper_layout.addWidget(bubble)
                wrapper_layout.addStretch()

            self._insert_before_spacer(wrapper)
        else:
            self._insert_before_spacer(bubble)

        self._scroll_to_bottom()

    def _add_widget(self, widget: QWidget) -> None:
        """Embed a custom widget (e.g. ConfirmCard) into the chat flow."""
        widget.setParent(self._msg_container)
        self._insert_before_spacer(widget)
        self._scroll_to_bottom()

    def _insert_before_spacer(self, widget: QWidget) -> None:
        """Insert a widget just before the bottom spacer in the message layout."""
        idx = self._msg_layout.count() - 1  # spacer is last
        self._msg_layout.insertWidget(idx, widget)

    def clear_chat(self) -> None:
        """Remove all messages from the chat area."""
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.conv.clear()

    # ------------------------------------------------------------------
    # Processing state
    # ------------------------------------------------------------------

    def _set_processing(self, active: bool) -> None:
        """Toggle the typing indicator and disable input during inference."""
        if active:
            self._typing_indicator.start()
            self._text_edit.setReadOnly(True)
            self._btn_send.setEnabled(False)
            self._text_edit.setPlaceholderText("AI 思考中...")
        else:
            self._typing_indicator.stop()
            self._text_edit.setReadOnly(False)
            self._btn_send.setEnabled(True)
            self._text_edit.setPlaceholderText("输入你想做的事情...")

    # ------------------------------------------------------------------
    # Scroll to bottom
    # ------------------------------------------------------------------

    def _scroll_to_bottom(self) -> None:
        """Auto-scroll the chat area to the latest message."""
        QTimer.singleShot(20, self._do_scroll)

    def _do_scroll(self) -> None:
        scrollbar = self._scroll_area.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------
    # Slide animation -- show / hide
    # ------------------------------------------------------------------

    def show_panel(self) -> None:
        """Animate the panel sliding in from the left edge."""
        if self._is_sliding:
            return
        self._is_sliding = True

        self.setVisible(True)
        start_x = -self.width()
        end_x = self._target_x

        self.move(start_x, self.y())

        self._slide_anim = QPropertyAnimation(self, b"pos")
        self._slide_anim.setDuration(_SLIDE_DURATION_MS)
        self._slide_anim.setStartValue(QPoint(start_x, self.y()))
        self._slide_anim.setEndValue(QPoint(end_x, self.y()))
        self._slide_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._slide_anim.finished.connect(self._on_slide_in_finished)
        self._slide_anim.start()

    def hide_panel(self) -> None:
        """Animate the panel sliding out to the left, then hide."""
        if self._is_sliding:
            return
        self._is_sliding = True

        start_x = self.x()
        end_x = -self.width()

        self._slide_anim = QPropertyAnimation(self, b"pos")
        self._slide_anim.setDuration(_SLIDE_DURATION_MS)
        self._slide_anim.setStartValue(QPoint(start_x, self.y()))
        self._slide_anim.setEndValue(QPoint(end_x, self.y()))
        self._slide_anim.setEasingCurve(QEasingCurve.InCubic)
        self._slide_anim.finished.connect(self._on_slide_out_finished)
        self._slide_anim.start()

    def toggle(self) -> None:
        """Toggle panel visibility (called from capsule click)."""
        if self.isVisible() and not self._is_sliding:
            self.hide_panel()
        elif not self.isVisible():
            self.show_panel()

    def _on_slide_in_finished(self) -> None:
        """Cleanup after slide-in completes."""
        self._is_sliding = False
        self._slide_anim = None

    def _on_slide_out_finished(self) -> None:
        """Cleanup after slide-out completes."""
        self._is_sliding = False
        self._slide_anim = None
        self.setVisible(False)
        self._hide_offscreen()
        self.panel_closed.emit()

    # ------------------------------------------------------------------
    # Window event overrides
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        """Override to support direct show() with animation."""
        # When shown via the framework (not show_panel), still animate in.
        # But we ignore this because show_panel() handles animation explicitly.
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        """Close hides the panel; does NOT quit the application."""
        event.ignore()
        self.hide_panel()

    # ------------------------------------------------------------------
    # Mouse drag support -- drag the title bar to reposition
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Record the drag origin when pressing the title bar area."""
        if event.button() == Qt.LeftButton:
            local = self.centralWidget().mapFrom(self, event.pos())
            title_rect = self._title_bar.geometry()
            if title_rect.contains(local):
                self._drag_origin = event.globalPosition().toPoint()
                self._drag_active = True
                return
        self._drag_active = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Drag the panel if the title bar is being dragged."""
        if getattr(self, '_drag_active', False):
            delta = event.globalPosition().toPoint() - self._drag_origin
            self.move(self.pos() + delta)
            self._drag_origin = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End the drag."""
        self._drag_active = False
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def set_chrome_connected(self, connected: bool) -> None:
        """Update the Chrome debugging connection state for the inference engine."""
        self._chrome_connected = connected
        logger.debug("Chrome connected state set to: %s", connected)

    def check_ollama(self) -> bool:
        """Check if the Ollama API is reachable; add a system message with status."""
        available = self.engine.check_ollama_available()
        if available:
            self._add_bubble("system", "已连接 AI 服务，随时可以开始对话。")
        else:
            self._add_bubble(
                "system",
                "未连接到 AI 服务\n\n"
                "请确认 API 配置正确：\n"
                "1. 点击标题栏齿轮按钮 → 设置\n"
                "2. 或编辑项目目录的 .env 文件\n\n"
                "配置完成后重启应用。"
            )
        return available

    def focus_input(self) -> None:
        """Set keyboard focus to the chat input field."""
        self._text_edit.setFocus()


# ---------------------------------------------------------------------------
# Standalone test entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    app = QApplication(sys.argv)
    app.setApplicationName("AI 办公助手 - SidePanel Test")

    cfg = AppConfig()
    mem = MemoryStore(db_path="data/test_sidepanel.db")

    panel = SidePanel(cfg, mem)
    panel.check_ollama()
    panel.show_panel()

    sys.exit(app.exec())
