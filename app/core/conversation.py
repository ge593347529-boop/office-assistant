"""对话管理器——管理聊天历史、上下文窗口。"""

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ConversationManager:
    """管理对话历史和上下文。"""

    def __init__(self, max_history: int = 20) -> None:
        """max_history: 最多保留多少条消息"""
        if max_history < 1:
            raise ValueError("max_history must be at least 1")
        self._max_history = max_history
        self._messages: list[dict[str, Any]] = []
        logger.debug("ConversationManager initialized, max_history=%d", max_history)

    # ------------------------------------------------------------------
    # 添加消息
    # ------------------------------------------------------------------

    def add_user_message(self, text: str) -> None:
        """添加用户消息到历史"""
        self._append({
            "role": "user",
            "content": text,
            "timestamp": self._now(),
            "task_result": None,
        })
        logger.debug("User message added: %r", text)

    def add_assistant_message(self, text: str, task_result: Any | None = None) -> None:
        """添加助手回复，可选关联 TaskResult"""
        tr_dict: dict | None = None
        if task_result is not None:
            tr_dict = task_result if isinstance(task_result, dict) else task_result.__dict__
        self._append({
            "role": "assistant",
            "content": text,
            "timestamp": self._now(),
            "task_result": tr_dict,
        })
        logger.debug("Assistant message added (has task_result=%s)", task_result is not None)

    def add_system_message(self, text: str) -> None:
        """添加系统消息（如错误提示、状态更新）"""
        self._append({
            "role": "system",
            "content": text,
            "timestamp": self._now(),
            "task_result": None,
        })
        logger.debug("System message added: %r", text)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_history(self, limit: int | None = None) -> list[dict]:
        """
        返回对话历史。
        每条消息格式:
        {
            "role": "user" | "assistant" | "system",
            "content": str,
            "timestamp": "ISO datetime",
            "task_result": dict | None
        }
        limit=None 返回全部，否则返回最近 limit 条。
        """
        if limit is None or limit <= 0:
            return list(self._messages)
        return self._messages[-limit:]

    def get_last_task(self) -> dict | None:
        """返回最近一次的任务结果（用于上下文连续对话），无则返回 None"""
        for msg in reversed(self._messages):
            if msg["role"] == "assistant" and msg.get("task_result") is not None:
                return msg["task_result"]
        return None

    def get_context_for_llm(self) -> str:
        """将最近几轮对话格式化为 LLM 可用的上下文字符串"""
        recent = self._get_recent_turns(max_turns=5)
        if not recent:
            return ""
        lines: list[str] = []
        for msg in recent:
            role_label = {"user": "用户", "assistant": "助手", "system": "系统"}[msg["role"]]
            lines.append(f"{role_label}: {msg['content']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 管理
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """清空对话历史"""
        self._messages.clear()
        logger.debug("Conversation history cleared")

    def __len__(self) -> int:
        """返回当前消息数"""
        return len(self._messages)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _append(self, msg: dict[str, Any]) -> None:
        self._messages.append(msg)
        self._trim()

    def _trim(self) -> None:
        while len(self._messages) > self._max_history:
            removed = self._messages.pop(0)
            logger.debug("Old message trimmed (role=%s)", removed["role"])

    def _get_recent_turns(self, max_turns: int) -> list[dict]:
        """
        取最近 N 轮对话，一轮 = user + assistant（system 消息过滤掉）。
        倒序遍历，收集 user+assistant 对，直到凑满 max_turns 轮。
        """
        turns: list[dict] = []
        turn: list[dict] = []
        assistant_seen = False

        for msg in reversed(self._messages):
            if msg["role"] == "system":
                continue
            if msg["role"] == "assistant":
                if assistant_seen:
                    # 上一轮没有对应的 user 消息，丢弃不完整轮次
                    if turn:
                        turn = []
                turn.append(msg)
                assistant_seen = True
            elif msg["role"] == "user":
                if assistant_seen and turn:
                    turn.append(msg)
                    turns = turn + turns  # 还原顺序
                    turn = []
                    assistant_seen = False
                    if len(turns) >= max_turns * 2:
                        break
                else:
                    # 孤立的 user 消息也收入
                    turns.insert(0, msg)
                    if len(turns) >= max_turns * 2:
                        break

        # 如果还有未收尾的 assistant 消息，也收入
        if turn:
            turns = turn + turns

        return turns

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
