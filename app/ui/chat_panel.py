"""
聊天面板：对话式交互界面。
- 历史消息展示（用户/助手/系统 三种气泡样式）
- 输入框 + 发送按钮
- 支持嵌入自定义 widget（如 ConfirmCard）
- Enter 发送，Shift+Enter 换行
"""

import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QScrollArea,
    QTextEdit,
    QLineEdit,
    QPushButton,
    QLabel,
    QFrame,
    QSizePolicy,
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor

logger = logging.getLogger(__name__)


# ── 消息气泡组件 ──────────────────────────────────────────────

class _MessageBubble(QFrame):
    """单条消息气泡（内部使用）"""

    def __init__(self, role: str, content: str, timestamp: str | None = None, parent=None):
        super().__init__(parent)
        self._role = role
        self._content = content
        self._timestamp = timestamp or datetime.now().strftime("%H:%M")

        self._setup_ui()
        self._apply_role_style()

    # ── UI 构建 ─────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setObjectName("MessageBubble")

        # 主布局
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # 角色标签
        role_label_map = {
            "user": "👤 你",
            "assistant": "🤖 助手",
            "system": "📋 系统",
        }
        role_text = role_label_map.get(self._role, f"• {self._role}")

        self._role_label = QLabel(role_text)
        self._role_label.setObjectName("BubbleRole")
        root.addWidget(self._role_label)

        # 内容标签
        self._content_label = QLabel(self._content)
        self._content_label.setObjectName("BubbleContent")
        self._content_label.setWordWrap(True)
        self._content_label.setTextFormat(Qt.PlainText)
        self._content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self._content_label)

        # 时间标签
        self._time_label = QLabel(self._timestamp)
        self._time_label.setObjectName("BubbleTime")
        root.addWidget(self._time_label)

    # ── 样式 ───────────────────────────────────────────────────

    def _apply_role_style(self) -> None:
        role_styles = {
            "user": {
                "bubble": {
                    "background-color": "#E3F2FD",
                    "border": "1px solid #BBDEFB",
                    "border-radius": "12px",
                    "padding": "10px 14px",
                },
                "align": Qt.AlignRight,
                "role_visible": True,
            },
            "assistant": {
                "bubble": {
                    "background-color": "#F5F5F5",
                    "border": "1px solid #E0E0E0",
                    "border-radius": "12px",
                    "padding": "10px 14px",
                },
                "align": Qt.AlignLeft,
                "role_visible": True,
            },
            "system": {
                "bubble": {
                    "background-color": "transparent",
                    "border": "none",
                    "border-radius": "0px",
                    "padding": "6px 10px",
                },
                "align": Qt.AlignCenter,
                "role_visible": False,
            },
        }

        style = role_styles.get(self._role, role_styles["assistant"])
        bubble = style["bubble"]

        # 组装 QSS
        qss_parts = []
        for prop, value in bubble.items():
            qss_parts.append(f"  {prop}: {value};")
        bubble_qss = "QFrame#MessageBubble {\n" + "\n".join(qss_parts) + "\n}"

        self.setStyleSheet(bubble_qss)

        # 对齐
        self._role_label.setAlignment(style["align"])
        self._content_label.setAlignment(style["align"])
        self._time_label.setAlignment(style["align"])

        # 角色标签可见性
        self._role_label.setVisible(style["role_visible"])

        # 系统消息特殊处理：灰色小字
        if self._role == "system":
            self._content_label.setStyleSheet(
                "color: #9E9E9E; font-size: 12px; font-style: italic;"
            )
            self._time_label.setStyleSheet("color: #BDBDBD; font-size: 11px;")
        else:
            self._content_label.setStyleSheet("color: #212121; font-size: 14px;")
            self._time_label.setStyleSheet("color: #9E9E9E; font-size: 11px;")

    # ── 尺寸策略 ───────────────────────────────────────────────

    def sizeHint(self):
        return self.minimumSizeHint()


# ── 输入区域组件 ──────────────────────────────────────────────

class _InputBar(QWidget):
    """底部输入栏：输入框 + 发送按钮"""

    send_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InputBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        # 输入框 —— 用 QTextEdit 支持 Shift+Enter 换行
        self._text_edit = QTextEdit()
        self._text_edit.setObjectName("ChatInput")
        self._text_edit.setPlaceholderText("输入你想做的事情...")
        self._text_edit.setMaximumHeight(120)
        self._text_edit.setMinimumHeight(38)
        self._text_edit.setAcceptRichText(False)
        self._text_edit.setTabChangesFocus(True)

        # 字体
        font = QFont()
        font.setPointSize(13)
        self._text_edit.setFont(font)
        self._text_edit.document().setDefaultFont(font)

        layout.addWidget(self._text_edit, stretch=1)

        # 发送按钮
        self._send_btn = QPushButton("发送")
        self._send_btn.setObjectName("SendButton")
        self._send_btn.setFixedSize(72, 38)
        self._send_btn.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self._send_btn)

        # 信号
        self._send_btn.clicked.connect(self._on_send)
        self._text_edit.installEventFilter(self)

    # ── 事件过滤：Enter 发送 / Shift+Enter 换行 ─────────────────

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        if obj is self._text_edit and event.type() == QEvent.KeyPress:
            key_event = event
            if key_event.key() == Qt.Key_Return or key_event.key() == Qt.Key_Enter:
                if not (key_event.modifiers() & Qt.ShiftModifier):
                    self._on_send()
                    return True
        return super().eventFilter(obj, event)

    # ── 发送 ───────────────────────────────────────────────────

    def _on_send(self) -> None:
        text = self._text_edit.toPlainText().strip()
        if text:
            self.send_clicked.emit(text)
            self._text_edit.clear()

    # ── 公开方法 ───────────────────────────────────────────────

    def text(self) -> str:
        return self._text_edit.toPlainText().strip()

    def set_placeholder(self, text: str) -> None:
        self._text_edit.setPlaceholderText(text)

    def set_processing(self, is_processing: bool) -> None:
        self._text_edit.setReadOnly(is_processing)
        self._send_btn.setEnabled(not is_processing)
        if is_processing:
            self._text_edit.setStyleSheet(
                "#ChatInput { background-color: #F5F5F5; color: #BDBDBD; }"
            )
        else:
            self._text_edit.setStyleSheet("")

    def set_focus(self) -> None:
        self._text_edit.setFocus()


# ── 聊天面板 ──────────────────────────────────────────────────

class ChatPanel(QWidget):
    """聊天面板：历史消息展示 + 输入框 + 发送按钮"""

    user_input_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatPanel")

        logger.info("初始化 ChatPanel")

        self._setup_ui()
        self._apply_global_style()

    # ── UI 构建 ─────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 滚动消息区域 ───────────────────────────────────────
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("ChatScrollArea")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setFrameShape(QFrame.NoFrame)

        # 消息容器
        self._msg_container = QWidget()
        self._msg_container.setObjectName("MessageContainer")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(16, 12, 16, 12)
        self._msg_layout.setSpacing(10)

        # 底部弹性空间：将消息推到顶部（消息少时）
        self._msg_spacer = QWidget()
        self._msg_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._msg_layout.addWidget(self._msg_spacer)

        self._scroll_area.setWidget(self._msg_container)
        root.addWidget(self._scroll_area, stretch=1)

        # ── 处理中指示条 ───────────────────────────────────────
        self._processing_label = QLabel("AI 思考中...")
        self._processing_label.setObjectName("ProcessingLabel")
        self._processing_label.setAlignment(Qt.AlignCenter)
        self._processing_label.setVisible(False)
        root.addWidget(self._processing_label)

        # ── 底部输入栏 ─────────────────────────────────────────
        self._input_bar = _InputBar()
        self._input_bar.send_clicked.connect(self._on_user_input)
        root.addWidget(self._input_bar)

    # ── 全局样式 ───────────────────────────────────────────────

    def _apply_global_style(self) -> None:
        self.setStyleSheet("""
            #ChatPanel {
                background-color: #FFFFFF;
            }

            #ChatScrollArea {
                background-color: #FFFFFF;
                border: none;
            }

            #MessageContainer {
                background-color: #FFFFFF;
            }

            #InputBar {
                background-color: #FAFAFA;
                border-top: 1px solid #E0E0E0;
            }

            #ChatInput {
                border: 1px solid #E0E0E0;
                border-radius: 8px;
                padding: 6px 10px;
                background-color: #FFFFFF;
                color: #212121;
            }

            #ChatInput:focus {
                border: 1px solid #1976D2;
            }

            #SendButton {
                background-color: #1976D2;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }

            #SendButton:hover {
                background-color: #1565C0;
            }

            #SendButton:pressed {
                background-color: #0D47A1;
            }

            #SendButton:disabled {
                background-color: #BDBDBD;
            }

            #ProcessingLabel {
                background-color: #FFF9C4;
                color: #F57F17;
                font-size: 13px;
                padding: 6px 0px;
                border-top: 1px solid #FFF176;
            }

            #BubbleRole {
                font-size: 12px;
                font-weight: bold;
                color: #757575;
                background: transparent;
                border: none;
            }

            #BubbleTime {
                font-size: 11px;
                color: #BDBDBD;
                background: transparent;
                border: none;
                margin-top: 2px;
            }
        """)

    # ── 公共方法 ───────────────────────────────────────────────

    def add_message(self, role: str, content: str, timestamp: str | None = None) -> None:
        """添加一条消息到聊天区域"""
        logger.debug(f"添加消息: role={role}, content={content[:50]}...")

        bubble = _MessageBubble(role, content, timestamp, parent=self._msg_container)

        # 根据角色决定是否需要对齐 wrapper
        if role in ("user", "assistant"):
            wrapper = QWidget(self._msg_container)
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(0, 0, 0, 0)

            if role == "user":
                wrapper_layout.setContentsMargins(40, 0, 0, 0)  # 左侧留白
                wrapper_layout.addWidget(bubble, alignment=Qt.AlignRight)
            else:
                wrapper_layout.setContentsMargins(0, 0, 40, 0)  # 右侧留白
                wrapper_layout.addWidget(bubble, alignment=Qt.AlignLeft)

            # 插入 wrapper 到弹性空间之前
            self._msg_layout.insertWidget(self._msg_layout.count() - 1, wrapper)
        else:
            # 系统消息：直接插入，自带居中样式
            self._msg_layout.insertWidget(self._msg_layout.count() - 1, bubble)

        # 自动滚动到底部
        self._scroll_to_bottom()

    def add_widget(self, widget: QWidget) -> None:
        """嵌入自定义 widget（如 ConfirmCard）到消息流中"""
        logger.debug(f"嵌入 widget: {type(widget).__name__}")

        widget.setParent(self._msg_container)
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, widget)

        self._scroll_to_bottom()

    def set_processing(self, is_processing: bool) -> None:
        """设置处理中状态"""
        logger.debug(f"set_processing: {is_processing}")
        self._input_bar.set_processing(is_processing)
        self._processing_label.setVisible(is_processing)

        if is_processing:
            self._input_bar.set_placeholder("AI 思考中...")
        else:
            self._input_bar.set_placeholder("输入你想做的事情...")

    def clear(self) -> None:
        """清空聊天区域"""
        logger.info("清空聊天区域")

        # 保留 spacer，移除其他所有 widget
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def set_placeholder(self, text: str) -> None:
        """设置输入框占位文字"""
        self._input_bar.set_placeholder(text)

    # ── 私有方法 ───────────────────────────────────────────────

    def _on_user_input(self, text: str) -> None:
        logger.debug(f"用户输入: {text[:50]}...")
        self.user_input_submitted.emit(text)

    def _scroll_to_bottom(self) -> None:
        """滚动到消息区域底部"""
        # 使用 QTimer.singleShot 确保在布局更新后滚动
        QTimer.singleShot(30, self._do_scroll)

    def _do_scroll(self) -> None:
        scrollbar = self._scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
