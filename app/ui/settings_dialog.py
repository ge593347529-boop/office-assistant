"""API 设置对话框"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """API 和模型设置对话框"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("API 设置")
        self.setMinimumWidth(480)
        self.setModal(True)
        self.setStyleSheet("QDialog{background-color:#161b22;}")
        self.setMinimumSize(480, 350)

        self._project_root = Path(__file__).resolve().parent.parent.parent
        self._env_path = self._project_root / ".env"

        self._setup_ui()
        self._load_current()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        # 标题
        title = QLabel("AI 模型配置")
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #c9d1d9;")
        layout.addWidget(title)

        desc = QLabel("配置 API 地址和密钥后重启生效")
        desc.setStyleSheet("font-size: 12px; color: #8b949e;")
        layout.addWidget(desc)

        # 表单
        form = QFormLayout()
        form.setSpacing(12)

        input_style = (
            "QLineEdit{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;"
            "border-radius:6px;padding:6px 10px;font-size:13px;}"
            "QLineEdit:focus{border-color:#58a6ff;}"
        )

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://api.deepseek.com/v1")
        self._url_input.setStyleSheet(input_style)
        form.addRow("API 地址:", self._url_input)

        self._model_input = QLineEdit()
        self._model_input.setPlaceholderText("deepseek-chat")
        self._model_input.setStyleSheet(input_style)
        form.addRow("模型名称:", self._model_input)

        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.Password)
        self._key_input.setPlaceholderText("sk-...")
        self._key_input.setStyleSheet(input_style)
        form.addRow("API Key:", self._key_input)

        layout.addLayout(form)

        # 提示
        hint = QLabel(
            "支持所有 OpenAI 兼容 API：DeepSeek、Ollama、vLLM 等\n"
            "配置保存在项目目录的 .env 文件中"
        )
        hint.setStyleSheet("font-size: 11px; color: #484f58;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        test_btn = QPushButton("测试连接")
        test_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#58a6ff;border:1px solid #30363d;"
            "border-radius:6px;padding:6px 14px;font-size:13px;}"
            "QPushButton:hover{background:rgba(88,166,255,0.1);border-color:#58a6ff;}"
        )
        test_btn.clicked.connect(self._on_test)
        btn_layout.addWidget(test_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#8b949e;border:1px solid #30363d;"
            "border-radius:6px;padding:6px 14px;font-size:13px;}"
            "QPushButton:hover{background:#21262d;color:#c9d1d9;}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.setStyleSheet(
            "QPushButton{background:#238636;color:#fff;border:none;"
            "border-radius:6px;padding:6px 18px;font-size:13px;font-weight:500;}"
            "QPushButton:hover{background:#2ea043;}"
        )
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

    def _load_current(self) -> None:
        """从 .env 读取当前配置"""
        config = self._parse_env()
        self._url_input.setText(config.get("OA_OLLAMA_URL", ""))
        self._model_input.setText(config.get("OA_OLLAMA_MODEL", ""))
        self._key_input.setText(config.get("OA_API_KEY", ""))

    def _parse_env(self) -> dict[str, str]:
        """解析 .env 文件"""
        result: dict[str, str] = {}
        if not self._env_path.is_file():
            return result
        for line in self._env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip().strip("\"'")
        return result

    def _on_save(self) -> None:
        """保存到 .env"""
        url = self._url_input.text().strip()
        model = self._model_input.text().strip()
        key = self._key_input.text().strip()

        if not url:
            QMessageBox.warning(self, "缺少参数", "请填写 API 地址")
            return
        if not model:
            QMessageBox.warning(self, "缺少参数", "请填写模型名称")
            return

        # 构建 .env 内容
        lines = [
            "# AI 办公助手 API 配置",
            f"OA_OLLAMA_URL={url}",
            f"OA_OLLAMA_MODEL={model}",
            f"OA_API_KEY={key}",
        ]
        self._env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("配置已保存到 %s", self._env_path)

        QMessageBox.information(
            self, "保存成功",
            "配置已保存。\n请重启 App 使新配置生效。"
        )
        self.accept()

    def _on_test(self) -> None:
        """测试 API 连接"""
        url = self._url_input.text().strip()
        model = self._model_input.text().strip()
        key = self._key_input.text().strip()

        if not url or not model:
            QMessageBox.warning(self, "缺少参数", "请先填写 API 地址和模型名称")
            return

        try:
            import openai
            client = openai.OpenAI(base_url=url, api_key=key or "ollama")
            client.models.list(timeout=5)
            QMessageBox.information(self, "连接成功", f"已成功连接到 {url}")
        except Exception as e:
            QMessageBox.warning(
                self, "连接失败",
                f"无法连接到 API:\n{str(e)[:200]}\n\n请检查地址和密钥是否正确。"
            )
