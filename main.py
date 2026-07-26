"""AI 办公助手 — 聊天窗口入口"""

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QLockFile, QTimer


def main():
    lock = QLockFile("ai_office_assistant.lock")
    lock.setStaleLockTime(30000)
    if not lock.tryLock(100):
        print("应用已在运行中")
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("AI 办公助手")
    app.setQuitOnLastWindowClosed(True)

    from app.config.settings import load_config
    from app.core.memory import MemoryStore
    from app.ui.side_panel import SidePanel

    config = load_config()
    memory = MemoryStore()
    window = SidePanel(config, memory)

    # Resize to a reasonable default
    window.resize(520, 700)
    window.setMinimumSize(380, 500)
    window.show()

    QTimer.singleShot(500, lambda: window.check_ollama())

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
