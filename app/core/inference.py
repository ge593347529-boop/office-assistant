"""AI inference engine -- translates natural language to structured TaskResult.

Design principle: AI acts only as a translator (one call per request); all
deterministic execution is handled by the executor layer.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import openai

from app.config.prompt_templates import (
    TASK_OUTPUT_SCHEMA,
    TOOL_DESCRIPTIONS,
    build_system_prompt,
    build_user_prompt,
)
from app.config.settings import AppConfig
from app.core.memory import MemoryStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_TASK_TYPES: frozenset[str] = frozenset({
    "form_filling",
    "data_extraction",
    "file_organize",
    "batch_rename",
    "excel_report",
    "web_monitor",
    "unknown",
    "general_chat",
})

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TaskResult:
    """Structured result produced by the inference engine.

    When ``needs_clarification`` is True, the executor / UI layer should
    present ``clarification_question`` to the user instead of attempting
    execution.
    """

    task_type: str                             # e.g. "data_entry", "unknown"
    system_name: str                           # e.g. "OA", "CRM", ""
    mode: str                                  # "A" | "B" | ""
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0                    # 0.0 -- 1.0
    needs_clarification: bool = False
    clarification_question: str = ""
    raw_response: str = ""
    user_input: str = ""  # 原始用户输入，用于记忆记录


# ---------------------------------------------------------------------------
# Inference engine
# ---------------------------------------------------------------------------


class InferenceEngine:
    """Wraps an Ollama-hosted Qwen model behind an OpenAI-compatible API.

    Responsibilities
    ----------------
    - Fast-path shortcut matches from the memory store (no LLM call).
    - Construct prompts and call the local model when no shortcut exists.
    - Robust JSON parsing with multiple fallback strategies.
    - Graceful degradation: any failure surface yields a ``needs_clarification``
      result rather than crashing the application.
    """

    def __init__(self, config: AppConfig, memory: MemoryStore) -> None:
        self._config = config
        self._memory = memory
        self._logger = logging.getLogger(__name__)

        self._client = openai.OpenAI(
            base_url=config.ollama_base_url,
            api_key=config.api_key,
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def check_ollama_available(self) -> bool:
        """Ping the Ollama API to verify the service is reachable.

        Returns
        -------
        bool
            ``True`` if Ollama responded within 5 seconds, ``False`` otherwise.
        """
        try:
            self._client.models.list(timeout=5)
            return True
        except Exception:
            self._logger.warning(
                "Ollama health check failed -- is the service running at %s?",
                self._config.ollama_base_url,
            )
            return False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def infer(
        self,
        user_input: str,
        user_chrome_connected: bool = False,
    ) -> TaskResult:
        """Translate a natural-language request into a structured TaskResult.

        Parameters
        ----------
        user_input : str
            Raw text from the chat panel.
        user_chrome_connected : bool
            Whether a Chrome debugging session is active (influences ``mode``).

        Returns
        -------
        TaskResult
            Always returns a result -- exceptions are caught internally and
            surfaced as ``needs_clarification``.
        """
        try:
            # ---- Step A: memory context ----
            context = self._memory.get_context(user_input)

            # ---- Step B: shortcut (memory hit) fast path ----
            # Only use shortcut for real tasks (not general_chat/unknown, not empty system)
            shortcut = context.get("shortcut_match")
            if shortcut:
                task_type = shortcut.get("task_type", "")
                system_name = shortcut.get("system_name", "")
                if task_type not in ("general_chat", "unknown") and system_name:
                    return self._build_shortcut_result(shortcut, user_chrome_connected)

            # ---- Step C: build prompts and call LLM ----
            # Inject browser state into the memory context for prompt building
            context["chrome_connected"] = user_chrome_connected
            system_prompt = build_system_prompt(
                TASK_OUTPUT_SCHEMA, TOOL_DESCRIPTIONS, context
            )
            user_prompt = build_user_prompt(user_input, context, user_chrome_connected)

            messages: list[dict[str, str]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            raw_response = self._call_llm(messages)

            # ---- Step D: parse ----
            result = self._parse_response(raw_response, user_input)
            # If parsing failed and needs_clarification, use LLM's raw text as chat reply
            if result.needs_clarification and raw_response.strip():
                return TaskResult(
                    task_type="general_chat",
                    system_name="",
                    mode="",
                    params={},
                    confidence=0.5,
                    needs_clarification=False,
                    raw_response=raw_response,
                    clarification_question=raw_response.strip(),
                    user_input=user_input,
                )
            return result

        except Exception:
            self._logger.exception(
                "infer() failed for input: %r", user_input[:200]
            )
            return TaskResult(
                task_type="unknown",
                system_name="",
                mode="",
                params={},
                confidence=0.0,
                needs_clarification=True,
                clarification_question="抱歉，我暂时无法理解您的请求，请换个说法试试？",
                raw_response="",
                user_input=user_input,
            )

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _build_shortcut_result(
        self,
        shortcut: dict[str, Any],
        chrome_connected: bool,
    ) -> TaskResult:
        """Build a high-confidence ``TaskResult`` from a memory shortcut match.

        This path avoids an LLM call entirely -- it reuses a known-good
        pattern from the memory store.
        """
        task_type = shortcut.get("task_type", "unknown")
        system_name = shortcut.get("system_name", "")
        params = shortcut.get("last_mapping", {})
        if not isinstance(params, dict):
            params = {}

        mode = "B" if chrome_connected else "A"

        self._logger.debug(
            "Shortcut match: task=%s system=%s mode=%s",
            task_type, system_name, mode,
        )

        return TaskResult(
            task_type=task_type,
            system_name=system_name,
            mode=mode,
            params=params,
            confidence=0.98,
            needs_clarification=False,
            clarification_question="",
            raw_response=json.dumps(shortcut, ensure_ascii=False),
            user_input="",
        )

    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Send messages to Ollama and return the raw content string.

        One automatic retry on timeout (max 1 retry).

        Raises
        ------
        Exception
            Propagates on repeated failure so ``infer()`` can catch it.
        """
        last_exc: Exception | None = None

        for attempt in range(2):  # initial + 1 retry
            try:
                self._logger.debug("LLM call attempt %d", attempt + 1)
                response = self._client.chat.completions.create(
                    model=self._config.ollama_model,
                    messages=messages,
                    temperature=0.1,
                    timeout=30,
                )
                content = response.choices[0].message.content or ""
                self._logger.debug("LLM response length: %d chars", len(content))
                return content

            except Exception as exc:
                last_exc = exc
                self._logger.warning(
                    "LLM call attempt %d failed: %s", attempt + 1, exc
                )
                if attempt == 0:
                    continue  # one retry

        raise RuntimeError(
            f"LLM call failed after 2 attempts"
        ) from last_exc

    def _parse_response(
        self,
        raw_response: str,
        user_input: str,
    ) -> TaskResult:
        """Parse the LLM raw output into a validated ``TaskResult``.

        Applies three JSON-extraction strategies in order:
        1. Direct ``json.loads``.
        2. Extract from markdown code fences (`` ```json ... ``` ``).
        3. Find the first balanced ``{ ... }`` brace block.
        """
        parsed: dict[str, Any] | None = None

        # Strategy 1: direct parse
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            self._logger.debug("Direct JSON parse failed, trying alternatives")

        # Strategy 2: extract from markdown code block
        if parsed is None:
            parsed = self._extract_json_from_markdown(raw_response)

        # Strategy 3: find first { ... } block
        if parsed is None:
            parsed = self._extract_json_brace_block(raw_response)

        # ---- All strategies exhausted ----
        if parsed is None:
            return TaskResult(
                task_type="unknown",
                system_name="",
                mode="",
                params={},
                confidence=0.0,
                needs_clarification=True,
                clarification_question="我收到了回复但无法解析，请再描述一下您想做什么？",
                raw_response=raw_response,
                user_input=user_input,
            )

        # ---- Validate & build result ----
        task_type = str(parsed.get("task_type", "unknown")).strip()
        system_name = str(parsed.get("system_name", "")).strip()
        mode = str(parsed.get("mode", "")).strip()
        params = parsed.get("params", {})
        if not isinstance(params, dict):
            params = {}

        # Normalise task_type
        if task_type not in VALID_TASK_TYPES:
            self._logger.debug("Unknown task_type %r, marking as unknown", task_type)
            task_type = "unknown"

        # Normalise mode
        if mode not in ("A", "B"):
            mode = ""

        # Confidence scoring
        if task_type != "unknown" and system_name:
            confidence = 0.9
        elif task_type != "unknown" and not system_name:
            confidence = 0.7
        else:
            confidence = 0.3

        return TaskResult(
            task_type=task_type,
            system_name=system_name,
            mode=mode,
            params=params,
            confidence=confidence,
            needs_clarification=False,
            clarification_question="",
            raw_response=raw_response,
            user_input=user_input,
        )

    # ------------------------------------------------------------------
    # JSON extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json_from_markdown(text: str) -> dict[str, Any] | None:
        """Try to pull a JSON object from a markdown code fence.

        Handles both ```json ... ``` and ``` ... ``` variants.
        """
        pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_json_brace_block(text: str) -> dict[str, Any] | None:
        """Extract the substring between the first ``{`` and last ``}``,
        then attempt to parse it as JSON.
        """
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
