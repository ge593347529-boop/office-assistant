"""
确认卡片组件 — AI 推理完成后展示 TaskResult 给用户确认。

支持模式:
  - 紧凑模式 (confidence >= 0.9): 一行摘要 + 开始按钮
  - 展开模式 (0.5 <= confidence < 0.9): 展示所有字段，低置信度字段标黄
  - 追问模式 (confidence < 0.5 或 needs_clarification): 输入框让用户补充说明
  - 进度模式: 执行中，带进度条和停止按钮
  - 结果模式: 执行完成，展示成功/失败
"""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QProgressBar,
    QLineEdit,
    QFormLayout,
    QGridLayout,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap

from app.core.inference import TaskResult

logger = logging.getLogger(__name__)

# ── QSS 样式表 ──────────────────────────────────────────────────────────────

CARD_STYLE = """
QFrame#QFrame#ConfirmCard{
    background-color: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 12px;
}

QFrame#QFrame#ConfirmCard#title_label {
    font-size: 15px;
    font-weight: 600;
    color: #1a1a2e;
}

QFrame#ConfirmCard#subtitle_label {
    font-size: 13px;
    color: #555555;
}

QFrame#ConfirmCard#confidence_label {
    font-size: 12px;
    color: #e6a817;
    font-weight: 500;
}

QFrame#ConfirmCard#field_label {
    font-size: 13px;
    color: #666666;
    font-weight: 500;
}

QFrame#ConfirmCard#field_value {
    font-size: 13px;
    color: #1a1a2e;
}

QFrame#ConfirmCard#field_warning {
    background-color: #fff8e1;
    border-radius: 4px;
    padding: 2px 6px;
}

QFrame#ConfirmCard#clarify_title {
    font-size: 15px;
    font-weight: 600;
    color: #d32f2f;
}

QFrame#ConfirmCard#clarify_question {
    font-size: 13px;
    color: #555555;
    font-style: italic;
    background-color: #fafafa;
    border-radius: 6px;
    padding: 8px;
}

QFrame#ConfirmCard#progress_status {
    font-size: 13px;
    color: #555555;
}

QFrame#ConfirmCard#result_icon_label {
    font-size: 18px;
    font-weight: 700;
}

QFrame#ConfirmCard#result_message {
    font-size: 13px;
    color: #333333;
}

QFrame#ConfirmCardQPushButton {
    border-radius: 6px;
    padding: 6px 18px;
    font-size: 13px;
    font-weight: 500;
}

QFrame#ConfirmCardQPushButton#btn_confirm {
    background-color: #1677ff;
    color: #ffffff;
    border: none;
}

QFrame#ConfirmCardQPushButton#btn_confirm:hover {
    background-color: #4096ff;
}

QFrame#ConfirmCardQPushButton#btn_confirm:pressed {
    background-color: #0958d9;
}

QFrame#ConfirmCardQPushButton#btn_modify {
    background-color: #ffffff;
    color: #1677ff;
    border: 1px solid #1677ff;
}

QFrame#ConfirmCardQPushButton#btn_modify:hover {
    background-color: #f0f5ff;
}

QFrame#ConfirmCardQPushButton#btn_cancel {
    background-color: #ffffff;
    color: #999999;
    border: 1px solid #d9d9d9;
}

QFrame#ConfirmCardQPushButton#btn_cancel:hover {
    background-color: #f5f5f5;
    color: #666666;
}

QFrame#ConfirmCardQPushButton#btn_stop {
    background-color: #ff4d4f;
    color: #ffffff;
    border: none;
}

QFrame#ConfirmCardQPushButton#btn_stop:hover {
    background-color: #ff7875;
}

QFrame#ConfirmCardQPushButton#btn_dismiss {
    background-color: #ffffff;
    color: #555555;
    border: 1px solid #d9d9d9;
}

QFrame#ConfirmCardQPushButton#btn_dismiss:hover {
    background-color: #f5f5f5;
}

QFrame#ConfirmCardQPushButton#btn_screenshot {
    background-color: #ffffff;
    color: #1677ff;
    border: 1px solid #1677ff;
}

QFrame#ConfirmCardQPushButton#btn_screenshot:hover {
    background-color: #f0f5ff;
}

QFrame#ConfirmCardQProgressBar {
    border: none;
    border-radius: 6px;
    background-color: #f0f0f0;
    height: 10px;
    text-align: center;
    font-size: 11px;
}

QFrame#ConfirmCardQProgressBar::chunk {
    background-color: #1677ff;
    border-radius: 6px;
}

QFrame#ConfirmCardQLineEdit {
    border: 1px solid #d9d9d9;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    background-color: #fafafa;
}

QFrame#ConfirmCardQLineEdit:focus {
    border-color: #1677ff;
    background-color: #ffffff;
}
"""

# ── 帮助函数 ────────────────────────────────────────────────────────────────

_TASK_TYPE_ICONS: dict[str, str] = {
    "form_filling": "📝",
    "data_extraction": "📊",
    "file_organize": "📁",
    "batch_rename": "🏷️",
    "excel_report": "📈",
    "web_monitor": "🔍",
    "general_chat": "💬",
    "unknown": "❓",
    "default": "📋",
}


def _icon_for_task_type(task_type: str) -> str:
    return _TASK_TYPE_ICONS.get(task_type, _TASK_TYPE_ICONS["default"])


def _friendly_task_type(task_type: str) -> str:
    mapping = {
        "form_filling": "表单填写",
        "data_extraction": "数据提取",
        "file_organize": "文件整理",
        "batch_rename": "批量重命名",
        "excel_report": "Excel报表",
        "web_monitor": "网页监控",
        "general_chat": "普通对话",
        "unknown": "未知任务",
    }
    return mapping.get(task_type, task_type)


# ── QFrame#ConfirmCard─────────────────────────────────────────────────────────────

class ConfirmCard(QFrame):
    """展示 TaskResult 的确认卡片，用户可确认、修改、取消"""

    confirmed = Signal(dict)   # 用户点"开始"→ 发送调整后的参数 dict
    cancelled = Signal()       # 用户点"取消"
    modified = Signal(dict)    # 用户修改参数后点"开始"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConfirmCard")
        self.setStyleSheet(CARD_STYLE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(0)
        self.setVisible(False)

        # 当前 TaskResult 和参数
        self._task: Optional[TaskResult] = None
        self._params: dict = {}
        self._progress_bar: Optional[QProgressBar] = None
        self._status_label: Optional[QLabel] = None
        self._stop_requested: bool = False

        # 根布局
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(20, 16, 20, 16)
        self._root_layout.setSpacing(10)

        # 当前模式
        self._mode: str = "hidden"  # hidden | compact | expanded | clarify | progress | result

        # 占位标签，初始隐藏
        self._placeholder = QLabel()
        self._placeholder.setVisible(False)
        self._root_layout.addWidget(self._placeholder)

        logger.debug("QFrame#ConfirmCard初始化完成")

    # ── 公共接口 ──────────────────────────────────────────────────────────

    def show_task(self, task: TaskResult) -> None:
        """展示任务确认卡片。根据 confidence 决定呈现方式。"""
        self._task = task
        self._params = dict(task.params) if task.params else {}
        self._stop_requested = False

        self._clear_layout()

        confidence = task.confidence

        if task.needs_clarification or confidence < 0.5:
            logger.info("进入追问模式 (confidence=%.2f, needs_clarification=%s)",
                        confidence, task.needs_clarification)
            self._mode = "clarify"
            self._build_clarify_layout(task)
        elif confidence < 0.9:
            logger.info("进入展开模式 (confidence=%.2f)", confidence)
            self._mode = "expanded"
            self._build_expanded_layout(task, confidence)
        else:
            logger.info("进入紧凑模式 (confidence=%.2f)", confidence)
            self._mode = "compact"
            self._build_compact_layout(task)

        self.setVisible(True)
        self.updateGeometry()

    def show_progress(self, status: str, progress: float) -> None:
        """更新进度（执行阶段）。progress 0.0-1.0

        首次调用时自动切换到进度布局；后续调用复用已有控件。
        """
        self._stop_requested = False

        if self._mode != "progress":
            self._clear_layout()
            self._mode = "progress"
            self._build_progress_layout(status, progress)
            self.setVisible(True)
            self.updateGeometry()
            return

        # 已处于进度模式，只更新已有控件
        if self._status_label is not None:
            self._status_label.setText(status)
        if self._progress_bar is not None:
            self._progress_bar.setValue(int(progress * 100))
        logger.debug("进度更新: %.0f%% — %s", progress * 100, status)

    def show_result(self, result) -> None:
        """展示执行结果（ExecutionResult: success, message, details, screenshot）。"""
        self._clear_layout()
        self._mode = "result"
        self._exec_result = result  # 保存 ExecutionResult 供截图查看
        self._build_result_layout(result)
        self.setVisible(True)
        self.updateGeometry()

    def clear(self) -> None:
        """清空卡片，回到隐藏状态"""
        self._clear_layout()
        self._task = None
        self._exec_result = None
        self._params = {}
        self._progress_bar = None
        self._status_label = None
        self._stop_requested = False
        self._mode = "hidden"
        self.setVisible(False)
        logger.debug("QFrame#ConfirmCard已清空并隐藏")

    # ── 布局构建 ──────────────────────────────────────────────────────────

    def _clear_layout(self) -> None:
        """移除根布局中所有子控件"""
        while self._root_layout.count():
            item = self._root_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                # 是子布局
                sub_layout = item.layout()
                if sub_layout is not None:
                    self._clear_sub_layout(sub_layout)
        self._progress_bar = None
        self._status_label = None

    def _clear_sub_layout(self, layout) -> None:
        """递归清空子布局"""
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    self._clear_sub_layout(sub)

    # ── 紧凑模式 ──────────────────────────────────────────────────────────

    def _build_compact_layout(self, task: TaskResult) -> None:
        icon = _icon_for_task_type(task.task_type)
        friendly = _friendly_task_type(task.task_type)
        system = task.system_name or ""
        source = self._params.get("data_source", self._params.get("source_file", self._params.get("file", "")))

        title_text = f"{icon} {friendly}"
        if system:
            title_text += f" - {system}"

        # 标题行 + 开始按钮
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        title_label = QLabel(title_text)
        title_label.setObjectName("title_label")
        title_row.addWidget(title_label)

        title_row.addStretch()

        btn_start = QPushButton("▶ 开始")  # ▶ 开始
        btn_start.setObjectName("btn_confirm")
        btn_start.setCursor(Qt.PointingHandCursor)
        btn_start.clicked.connect(self._on_confirm_clicked)
        title_row.addWidget(btn_start)

        self._root_layout.addLayout(title_row)

        # 副标题行：数据源 + 模式
        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.setSpacing(16)

        details_parts = []
        if source:
            details_parts.append(f"数据: {source}")  # 数据:
        if task.mode:
            mode_text = f"模式: {task.mode}"  # 模式:
            details_parts.append(mode_text)

        if details_parts:
            subtitle = QLabel("  •  ".join(details_parts))  # bullet separator
            subtitle.setObjectName("subtitle_label")
            sub_row.addWidget(subtitle)

        sub_row.addStretch()
        self._root_layout.addLayout(sub_row)

    # ── 展开模式 ──────────────────────────────────────────────────────────

    def _build_expanded_layout(self, task: TaskResult, confidence: float) -> None:
        icon = _icon_for_task_type(task.task_type)
        friendly = _friendly_task_type(task.task_type)
        system = task.system_name or ""

        # 标题行
        title_label = QLabel(f"{icon} {friendly} - {system}" if system else f"{icon} {friendly}")
        title_label.setObjectName("title_label")
        self._root_layout.addWidget(title_label)

        # 置信度
        conf_label = QLabel(f"⚠️ 置信度: {int(confidence * 100)}%")  # ⚠️ 置信度:
        conf_label.setObjectName("confidence_label")
        self._root_layout.addWidget(conf_label)

        # 字段网格
        grid = QGridLayout()
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setVerticalSpacing(6)
        grid.setHorizontalSpacing(16)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)

        row = 0
        if system:
            self._add_grid_field(grid, row, "系统", system, False)  # 系统
            row += 1

        source = self._params.get("data_source", self._params.get("source_file", self._params.get("file", "")))
        if source:
            self._add_grid_field(grid, row, "数据源", source, False)  # 数据源
            row += 1

        # 字段映射
        field_mapping = self._params.get("field_mapping", None)
        if field_mapping and isinstance(field_mapping, dict):
            for field_name, field_info in field_mapping.items():
                display_text = ""
                is_low_conf = False
                if isinstance(field_info, dict):
                    column = field_info.get("column", "")
                    display_text = column if column else str(field_info)
                    is_low_conf = field_info.get("low_confidence", False)
                else:
                    display_text = str(field_info)

                label_key = f"← {field_name}"  # ← field_name
                self._add_grid_field(grid, row, label_key, display_text, is_low_conf)
                row += 1
        else:
            # 没有 field_mapping 时展示 params 中的 key-value
            for key, val in self._params.items():
                if key in ("source_file", "file", "field_mapping"):
                    continue
                self._add_grid_field(grid, row, key, str(val), False)
                row += 1

        self._root_layout.addLayout(grid)

        # 底部按钮行
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)

        btn_modify = QPushButton("✏️ 修改")  # ✏️ 修改
        btn_modify.setObjectName("btn_modify")
        btn_modify.setCursor(Qt.PointingHandCursor)
        btn_modify.clicked.connect(self._on_modify_clicked)
        btn_row.addWidget(btn_modify)

        btn_row.addStretch()

        btn_start = QPushButton("▶ 开始")  # ▶ 开始
        btn_start.setObjectName("btn_confirm")
        btn_start.setCursor(Qt.PointingHandCursor)
        btn_start.clicked.connect(self._on_confirm_clicked)
        btn_row.addWidget(btn_start)

        self._root_layout.addLayout(btn_row)

    def _add_grid_field(self, grid: QGridLayout, row: int, label: str, value: str,
                        warning: bool = False) -> None:
        label_w = QLabel(label)
        label_w.setObjectName("field_label")
        label_w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(label_w, row, 0)

        val_text = value
        if warning:
            val_text = f"{value}  ⚠️"  # ⚠️
        val_w = QLabel(val_text)
        val_w.setObjectName("field_value")
        if warning:
            val_w.setProperty("warning", True)
            val_w.setStyleSheet("background-color: #fff8e1; border-radius: 4px; padding: 2px 6px;")
        grid.addWidget(val_w, row, 1)

    # ── 追问模式 ──────────────────────────────────────────────────────────

    def _build_clarify_layout(self, task: TaskResult) -> None:
        # 标题
        title = QLabel("❓ 不太确定您的意图")  # ❓ 不太确定您的意图
        title.setObjectName("clarify_title")
        self._root_layout.addWidget(title)

        # 追问问题
        question_text = task.clarification_question or "请问您要在哪个系统操作？"
        question_label = QLabel(f'"{question_text}"')
        question_label.setObjectName("clarify_question")
        question_label.setWordWrap(True)
        self._root_layout.addWidget(question_label)

        # 输入行
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 4, 0, 0)

        self._clarify_input = QLineEdit()
        self._clarify_input.setPlaceholderText("输入补充说明...")  # 输入补充说明...
        self._clarify_input.setMinimumHeight(34)
        input_row.addWidget(self._clarify_input, 1)

        btn_confirm = QPushButton("确认")  # 确认
        btn_confirm.setObjectName("btn_confirm")
        btn_confirm.setCursor(Qt.PointingHandCursor)
        btn_confirm.clicked.connect(self._on_clarify_confirm_clicked)
        input_row.addWidget(btn_confirm)

        self._root_layout.addLayout(input_row)

    # ── 进度模式 ──────────────────────────────────────────────────────────

    def _build_progress_layout(self, status: str, progress: float) -> None:
        # 标题
        title = QLabel("\U0001F504 正在执行...")  # 🔄 正在执行...
        title.setObjectName("title_label")
        self._root_layout.addWidget(title)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(int(progress * 100))
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%p%")
        self._root_layout.addWidget(self._progress_bar)

        # 状态文字
        self._status_label = QLabel(status)
        self._status_label.setObjectName("progress_status")
        self._root_layout.addWidget(self._status_label)

        # 停止按钮
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.addStretch()

        btn_stop = QPushButton("⏹ 停止")  # ⏹ 停止
        btn_stop.setObjectName("btn_stop")
        btn_stop.setCursor(Qt.PointingHandCursor)
        btn_stop.clicked.connect(self._on_stop_clicked)
        btn_row.addWidget(btn_stop)

        self._root_layout.addLayout(btn_row)

    # ── 结果模式 ──────────────────────────────────────────────────────────

    def _build_result_layout(self, result) -> None:
        success = getattr(result, "success", False)
        message = getattr(result, "message", "")
        has_screenshot = hasattr(result, "screenshot") and result.screenshot is not None

        # 图标 + 状态
        if success:
            title_text = "✅ 已完成"  # ✅ 已完成
        else:
            title_text = "❌ 执行失败"  # ❌ 执行失败

        title = QLabel(title_text)
        title.setObjectName("result_icon_label")
        self._root_layout.addWidget(title)

        # 消息
        msg_label = QLabel(message or ("操作已成功完成" if success else "操作未能完成"))
        msg_label.setObjectName("result_message")
        msg_label.setWordWrap(True)
        self._root_layout.addWidget(msg_label)

        # 详情（可选）
        details = getattr(result, "details", None)
        if details:
            details_str = str(details)
            if len(details_str) < 200:
                detail_label = QLabel(details_str)
                detail_label.setObjectName("subtitle_label")
                detail_label.setWordWrap(True)
                self._root_layout.addWidget(detail_label)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)

        if has_screenshot:
            btn_screenshot = QPushButton("\U0001F4F7 查看截图")  # 📷 查看截图
            btn_screenshot.setObjectName("btn_screenshot")
            btn_screenshot.setCursor(Qt.PointingHandCursor)
            btn_screenshot.clicked.connect(self._on_screenshot_clicked)
            btn_row.addWidget(btn_screenshot)

        btn_row.addStretch()

        btn_dismiss = QPushButton("知道了")  # 知道了
        btn_dismiss.setObjectName("btn_dismiss")
        btn_dismiss.setCursor(Qt.PointingHandCursor)
        btn_dismiss.clicked.connect(self._on_dismiss_clicked)
        btn_row.addWidget(btn_dismiss)

        self._root_layout.addLayout(btn_row)

    # ── 槽函数 ────────────────────────────────────────────────────────────

    def _on_confirm_clicked(self) -> None:
        """紧凑/展开模式的 '开始' 按钮"""
        logger.info("用户点击确认（开始）")
        self.confirmed.emit(dict(self._params))

    def _on_modify_clicked(self) -> None:
        """展开模式的 '修改' 按钮"""
        logger.info("用户点击修改，当前参数: %s", self._params)
        # 发出 modified 信号，参数供外部编辑器使用
        self.modified.emit(dict(self._params))

    def _on_clarify_confirm_clicked(self) -> None:
        """追问模式的 '确认' 按钮"""
        user_input = self._clarify_input.text().strip() if hasattr(self, "_clarify_input") else ""
        logger.info("用户补充说明: '%s'", user_input)
        params = dict(self._params) if self._params else {}
        params["clarification"] = user_input
        self.confirmed.emit(params)

    def _on_stop_clicked(self) -> None:
        """进度模式的 '停止' 按钮"""
        logger.info("用户请求停止执行")
        self._stop_requested = True
        self.cancelled.emit()

    def _on_dismiss_clicked(self) -> None:
        """结果模式的 '知道了' 按钮"""
        logger.info("用户关闭结果卡片")
        self.clear()

    def _on_screenshot_clicked(self) -> None:
        """结果模式的 '查看截图' 按钮"""
        if self._exec_result is None:
            return
        try:
            screenshot = getattr(self._exec_result, "screenshot", None)
            if screenshot is None:
                return
            from PySide6.QtWidgets import QDialog

            dlg = QDialog(self)
            dlg.setWindowTitle("截图预览")
            dlg.setMinimumSize(400, 300)
            layout = QVBoxLayout(dlg)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            img_label = QLabel()
            if isinstance(screenshot, QPixmap):
                pixmap = screenshot
            elif isinstance(screenshot, bytes):
                pixmap = QPixmap()
                pixmap.loadFromData(screenshot)
            elif isinstance(screenshot, str):
                pixmap = QPixmap(screenshot)
            else:
                logger.warning("截图类型无法处理: %s", type(screenshot))
                dlg.deleteLater()
                return
            img_label.setPixmap(pixmap.scaled(780, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            scroll.setWidget(img_label)
            layout.addWidget(scroll)
            dlg.exec()
            dlg.deleteLater()
        except Exception:
            logger.exception("查看截图失败")

    # ── 属性 ──────────────────────────────────────────────────────────────

    @property
    def is_stop_requested(self) -> bool:
        """外部轮询此标志以决定是否中断执行"""
        return self._stop_requested

    @property
    def current_params(self) -> dict:
        """当前参数快照"""
        return dict(self._params) if self._params else {}
