"""MainWindow – 组装 ChatPanel + ConfirmCard + SystemTray，管理对话→推理→确认→执行→反馈流程。"""

from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent

from app.config.settings import AppConfig, load_config
from app.core.inference import InferenceEngine, TaskResult
from app.core.executor import TaskExecutor, ExecutionResult
from app.core.memory import MemoryStore
from app.core.conversation import ConversationManager
from app.ui.chat_panel import ChatPanel
from app.ui.confirm_card import ConfirmCard
from app.ui.system_tray import SystemTray


class MainWindow(QMainWindow):
    """AI 办公助手主窗口。

    流程：
        用户输入 → inference → needs_clarification → 追问
                            → 否则 → ConfirmCard.show_task → 用户确认
                                  → executor.execute → ConfirmCard 显示进度 + 结果
    """

    def __init__(self) -> None:
        super().__init__()

        # ── 核心模块 ──────────────────────────────────────────────
        self.config: AppConfig = load_config()
        self.memory: MemoryStore = MemoryStore()
        self.conv: ConversationManager = ConversationManager(
            max_history=self.config.max_history
        )
        self.engine: InferenceEngine = InferenceEngine(self.config, self.memory)
        self.executor: TaskExecutor = TaskExecutor(self.config, self.memory)

        # ── Chrome 连接状态 ──────────────────────────────────────
        self._chrome_connected: bool = False

        # ── 当前任务引用（供确认/修改后重新执行） ─────────────────
        self._current_task: TaskResult | None = None

        # ── UI ───────────────────────────────────────────────────
        self.chat_panel = ChatPanel()
        self.tray = SystemTray()
        self.tray.show()

        self._setup_window()
        self._setup_central_widget()
        self._connect_signals()
        self._check_ollama_status()

    # ═══════════════════════════════════════════════════════════════
    # 窗口设置
    # ═══════════════════════════════════════════════════════════════

    def _setup_window(self) -> None:
        """配置窗口属性。"""
        self.setWindowTitle("AI 办公助手")
        self.resize(800, 600)
        self.setMinimumSize(500, 400)

    def _setup_central_widget(self) -> None:
        """ChatPanel 全屏作为中央控件。"""
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.chat_panel)
        self.setCentralWidget(central)

    # ═══════════════════════════════════════════════════════════════
    # 信号连接
    # ═══════════════════════════════════════════════════════════════

    def _connect_signals(self) -> None:
        """连接 ChatPanel / ConfirmCard / SystemTray 信号。"""
        # 用户输入
        self.chat_panel.user_input_submitted.connect(self._handle_user_input)

        # 系统托盘
        self.tray.show_window.connect(self.show_and_activate)
        self.tray.quit_app.connect(self._quit_app)

    def _connect_confirm_card(self, card: ConfirmCard) -> None:
        """连接动态创建的 ConfirmCard 信号。"""
        card.confirmed.connect(lambda params: self._handle_execute(card, params))
        card.cancelled.connect(lambda: self._on_task_cancelled(card))
        card.modified.connect(lambda params: self._handle_execute(card, params))

    # ═══════════════════════════════════════════════════════════════
    # Ollama 状态
    # ═══════════════════════════════════════════════════════════════

    def _check_ollama_status(self) -> None:
        """检查 Ollama 连接状态并在聊天面板中提示。"""
        if self.engine.check_ollama_available():
            self.chat_panel.add_message(
                "system", "已连接 Ollama，随时可以开始对话。"
            )
        else:
            self.chat_panel.add_message(
                "system", "⚠️ Ollama 未连接，请确认服务已启动。"
            )

    # ═══════════════════════════════════════════════════════════════
    # 用户输入处理
    # ═══════════════════════════════════════════════════════════════

    def _handle_user_input(self, text: str) -> None:
        """处理用户输入：对话记录 → 推理 → 追问或展示确认卡片。"""
        # 1. 进入处理状态
        self.chat_panel.set_processing(True)
        self.tray.set_task_status("思考中…")

        # 2. 记录对话
        self.conv.add_user_message(text)
        self.chat_panel.add_message("user", text)

        # 3. 推理
        result: TaskResult = self.engine.infer(
            text, user_chrome_connected=self._chrome_connected
        )

        # 4. 退出处理状态
        self.chat_panel.set_processing(False)

        # 5. 需要澄清 → 追问
        if result.needs_clarification:
            question = result.clarification_question or "请进一步描述你的需求。"
            self.chat_panel.add_message("assistant", question)
            self.conv.add_assistant_message(question)
            self.tray.set_task_status("等待澄清")
            return

        # 6. 正常任务 → 嵌入确认卡片
        self._show_confirm_card(result)

    def _show_confirm_card(self, task: TaskResult) -> None:
        """在聊天流中嵌入确认卡片。"""
        self._current_task = task

        card = ConfirmCard()
        self._connect_confirm_card(card)
        card.show_task(task)

        self.chat_panel.add_widget(card)
        self.tray.set_task_status("等待确认")

    # ═══════════════════════════════════════════════════════════════
    # 任务执行
    # ═══════════════════════════════════════════════════════════════

    def _handle_execute(self, card: ConfirmCard, adjusted_params: dict) -> None:
        """用户确认后执行任务，并在 ConfirmCard 中展示进度和结果。"""
        if self._current_task is None:
            return

        # 合并用户调整后的参数
        self._current_task.params.update(adjusted_params)

        self.tray.set_task_status("执行中…")
        card.show_progress("starting", 0)

        exec_result: ExecutionResult = self.executor.execute(
            self._current_task,
            on_progress=card.show_progress,
        )

        # 展示结果
        card.show_result(exec_result)

        # 记录到历史
        task = self._current_task
        files_used = []
        if task.params.get("data_source"):
            files_used.append(task.params["data_source"])
        if task.params.get("target_file"):
            files_used.append(task.params["target_file"])
        self.memory.record_task(
            user_input=getattr(task, 'user_input', '') or '',
            task_type=task.task_type,
            system_name=task.system_name,
            params=task.params,
            files_used=files_used,
        )
        self.conv.add_assistant_message(
            f"任务完成：{exec_result.message}"
        )

        # 系统托盘通知
        status = "任务执行成功" if exec_result.success else "任务执行失败"
        self.tray.show_notification("AI 办公助手", status)
        self.tray.set_task_status("就绪")

    def _on_task_cancelled(self, card: ConfirmCard) -> None:
        """用户取消任务。"""
        card.clear()
        self.chat_panel.add_message("system", "已取消任务。")
        self.tray.set_task_status("就绪")
        self._current_task = None

    # ═══════════════════════════════════════════════════════════════
    # 窗口行为
    # ═══════════════════════════════════════════════════════════════

    def show_and_activate(self) -> None:
        """从托盘恢复窗口。"""
        self.show()
        self.activateWindow()
        self.raise_()

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭窗口时最小化到系统托盘，而非退出。"""
        event.ignore()
        self.hide()
        self.tray.show_notification("AI 办公助手", "已最小化到系统托盘")

    def _quit_app(self) -> None:
        """真正退出应用。"""
        self.tray.hide()
        from PySide6.QtWidgets import QApplication

        QApplication.instance().quit()
