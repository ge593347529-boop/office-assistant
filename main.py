"""AI 办公助手 — 灵动岛胶囊入口"""

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QLockFile, QTimer


def main():
    # Single instance check
    from pathlib import Path
    lock_path = str(Path(__file__).parent / "ai_office_assistant.lock")
    lock = QLockFile(lock_path)
    lock.setStaleLockTime(30000)  # 30s stale → auto-clean
    if not lock.tryLock(100):
        print("应用已在运行中")
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("AI 办公助手")
    app.setQuitOnLastWindowClosed(False)

    # Load config and memory
    from app.config.settings import load_config
    from app.core.memory import MemoryStore
    config = load_config()
    memory = MemoryStore()

    # Create capsule
    from capsule_window import CapsuleWindow
    capsule = CapsuleWindow()
    capsule.show()

    # Create side panel
    from app.ui.side_panel import SidePanel
    panel = SidePanel(config, memory)
    capsule.set_side_panel(panel)

    # Connect signals
    capsule.toggle_requested.connect(panel.toggle)
    capsule.restart_requested.connect(lambda: restart_app(app))
    capsule.settings_requested.connect(lambda: _open_settings_dialog(panel, config))
    panel.settings_requested.connect(lambda: _open_settings_dialog(panel, config))
    capsule.quit_requested.connect(app.quit)

    # Check API status after startup
    QTimer.singleShot(500, lambda: panel.check_ollama())

    sys.exit(app.exec())


def _open_settings_dialog(panel, config):
    """Open API settings dialog and restart if config changed."""
    from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QPushButton, QVBoxLayout, QLabel, QHBoxLayout
    dlg = QDialog()
    dlg.setWindowTitle("API 设置")
    dlg.setMinimumWidth(460)
    dlg.setStyleSheet("QDialog{background:#161b22;} QLabel{color:#c9d1d9;} QLineEdit{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px;} QPushButton{border-radius:6px;padding:6px 16px;}")

    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel("配置 API 地址和密钥后重启生效"))

    form = QFormLayout()
    url_input = QLineEdit(config.ollama_base_url)
    model_input = QLineEdit(config.ollama_model)
    key_input = QLineEdit(config.api_key)
    key_input.setEchoMode(QLineEdit.Password)
    form.addRow("API 地址:", url_input)
    form.addRow("模型名称:", model_input)
    form.addRow("API Key:", key_input)
    layout.addLayout(form)

    btn_row = QHBoxLayout()
    cancel = QPushButton("取消")
    cancel.clicked.connect(dlg.reject)
    save = QPushButton("保存")
    save.setStyleSheet("QPushButton{background:#238636;color:#fff;}")
    save.clicked.connect(lambda: _save_settings(url_input.text(), model_input.text(), key_input.text(), dlg))
    btn_row.addStretch()
    btn_row.addWidget(cancel)
    btn_row.addWidget(save)
    layout.addLayout(btn_row)

    dlg.exec()

def _save_settings(url, model, key, dlg):
    from pathlib import Path
    env_path = Path(__file__).parent / ".env"
    env_path.write_text(
        f"# AI 办公助手 API 配置\nOA_OLLAMA_URL={url}\nOA_OLLAMA_MODEL={model}\nOA_API_KEY={key}\n",
        encoding="utf-8"
    )
    dlg.accept()

def restart_app(app):
    import subprocess
    app.quit()
    subprocess.Popen([sys.executable, sys.argv[0]])


if __name__ == "__main__":
    main()
