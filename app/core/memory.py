"""Memory system backed by a local SQLite database.

Three-layer memory:
  1. System configuration profiles (URLs, login, session paths)
  2. Behavioural task patterns (data sources, field mappings per task)
  3. Conversation history summaries

Short-term memory + long-term patterns = smarter over time.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class MemoryStore:
    """Thread-safe SQLite-backed memory store with three-layer memory."""

    def __init__(self, db_path: str = "data/memory.db") -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS system_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    url TEXT DEFAULT '',
                    login_url TEXT DEFAULT '',
                    last_used TEXT DEFAULT '',
                    use_count INTEGER DEFAULT 0,
                    session_path TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS task_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    system_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    default_source TEXT DEFAULT '',
                    field_mapping_hist TEXT DEFAULT '[]',
                    last_mapping TEXT DEFAULT '{}',
                    use_count INTEGER DEFAULT 0,
                    UNIQUE(system_name, task_type)
                );

                CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_input TEXT DEFAULT '',
                    task_summary TEXT DEFAULT '',
                    files_used TEXT DEFAULT '[]',
                    UNIQUE(timestamp, task_summary)
                );

                CREATE TABLE IF NOT EXISTS shortcut_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    system_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    alias TEXT NOT NULL UNIQUE,
                    created_at TEXT DEFAULT '',
                    FOREIGN KEY (system_name, task_type) REFERENCES task_patterns(system_name, task_type)
                );
                """
            )

    @staticmethod
    def _parse_json(col: str, default: Any = None) -> Any:
        """Safely parse a JSON column."""
        if default is None:
            default = []
        try:
            return json.loads(col) if col else default
        except (json.JSONDecodeError, TypeError):
            return default

    # ------------------------------------------------------------------
    # Pinyin initial map for common Chinese characters
    # Used for pinyin-first-letter shortcut matching (e.g. "bx" -> "报销")
    # ------------------------------------------------------------------
    _PINYIN_INITIAL_MAP: dict[str, str] = {
        '安': 'a', '按': 'a', '案': 'a',
        '办': 'b', '报': 'b', '表': 'b', '部': 'b', '备': 'b', '版': 'b', '编': 'b',
        '本': 'b', '保': 'b', '变': 'b', '不': 'b', '步': 'b', '布': 'b', '标': 'b',
        '采': 'c', '财': 'c', '出': 'c', '查': 'c', '存': 'c', '储': 'c', '测': 'c',
        '成': 'c', '程': 'c', '产': 'c', '处': 'c', '传': 'c', '创': 'c', '词': 'c',
        '单': 'd', '订': 'd', '动': 'd', '代': 'd', '调': 'd', '档': 'd', '登': 'd',
        '导': 'd', '读': 'd', '队': 'd', '对': 'd', '定': 'd', '打': 'd', '地': 'd',
        '发': 'f', '付': 'f', '服': 'f', '负': 'f', '分': 'f', '方': 'f', '法': 'f',
        '管': 'g', '购': 'g', '工': 'g', '供': 'g', '告': 'g', '公': 'g', '更': 'g',
        '关': 'g', '规': 'g', '个': 'g', '格': 'g', '改': 'g', '果': 'g', '高': 'g',
        '合': 'h', '货': 'h', '换': 'h', '核': 'h', '会': 'h', '化': 'h', '户': 'h',
        '号': 'h', '后': 'h', '回': 'h', '活': 'h', '环': 'h', '获': 'h', '行': 'h',
        '计': 'j', '加': 'j', '解': 'j', '经': 'j', '件': 'j', '据': 'j', '监': 'j',
        '建': 'j', '接': 'j', '结': 'j', '间': 'j', '进': 'j', '交': 'j', '决': 'j',
        '库': 'k', '开': 'k', '客': 'k', '款': 'k', '考': 'k', '控': 'k', '看': 'k',
        '理': 'l', '利': 'l', '流': 'l', '录': 'l', '联': 'l', '量': 'l', '类': 'l',
        '密': 'm', '目': 'm', '模': 'm', '码': 'm', '名': 'm', '面': 'm', '明': 'm',
        '能': 'n', '内': 'n', '年': 'n', '内': 'n',
        '配': 'p', '批': 'p', '票': 'p', '凭': 'p', '平': 'p',
        '请': 'q', '器': 'q', '勤': 'q', '全': 'q', '取': 'q', '确': 'q', '签': 'q',
        '人': 'r', '入': 'r', '任': 'r', '日': 'r', '润': 'r', '认': 'r',
        '审': 's', '收': 's', '售': 's', '商': 's', '数': 's', '算': 's', '设': 's',
        '生': 's', '损': 's', '试': 's', '申': 's', '实': 's', '时': 's', '使': 's',
        '退': 't', '同': 't', '统': 't', '通': 't', '提': 't', '条': 't', '台': 't',
        '维': 'w', '务': 'w', '文': 'w', '网': 'w', '忘': 'w', '完': 'w', '位': 'w',
        '销': 'x', '系': 'x', '行': 'x', '项': 'x', '消': 'x', '息': 'x', '需': 'x',
        '学': 'x', '习': 'x', '型': 'x', '析': 'x', '修': 'x', '显': 'x', '限': 'x',
        '应': 'y', '预': 'y', '益': 'y', '邮': 'y', '议': 'y', '运': 'y', '用': 'y',
        '云': 'y', '员': 'y', '业': 'y', '验': 'y', '原': 'y', '页': 'y',
        '资': 'z', '证': 'z', '政': 'z', '债': 'z', '志': 'z', '总': 'z', '置': 'z',
        '装': 'z', '知': 'z', '自': 'z', '智': 'z', '助': 'z', '执': 'z', '展': 'z',
        '账': 'z', '找': 'z', '转': 'z', '组': 'z', '作': 'z', '中': 'z', '主': 'z',
    }

    @staticmethod
    def _to_pinyin_initials(text: str) -> str:
        """Convert Chinese text to pinyin initials using the lookup map.

        Non-Chinese ASCII letters are kept as-is (lowercased).
        Characters not in the map are skipped.
        """
        result: list[str] = []
        for ch in text:
            if ch in MemoryStore._PINYIN_INITIAL_MAP:
                result.append(MemoryStore._PINYIN_INITIAL_MAP[ch])
            elif ch.isascii() and ch.isalpha():
                result.append(ch.lower())
        return ''.join(result)

    @staticmethod
    def _fuzzy_match(user_input: str, target: str) -> bool:
        """Simple fuzzy match between user input and a target string.

        Tokenizes both strings into character-level and word-level tokens
        (for Chinese: each character is a token; for ASCII: space-separated words).
        Returns True if >= 50% of user tokens appear in the target tokens.
        """
        def _tokenize(s: str) -> set[str]:
            tokens: set[str] = set()
            # Normalize: replace underscores with spaces, lowercase
            s = s.lower().replace('_', ' ')
            # Split by whitespace for word-level tokens
            for word in s.split():
                tokens.add(word)
                # Also add individual Chinese characters
                for ch in word:
                    if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿':
                        tokens.add(ch)
            return tokens

        user_tokens = _tokenize(user_input)
        target_tokens = _tokenize(target)

        if not user_tokens:
            return False

        overlap = len(user_tokens & target_tokens)
        ratio = overlap / len(user_tokens)
        logger.debug(
            "Fuzzy match: %r vs %r -> overlap=%d/%d (%.0f%%)",
            user_input, target, overlap, len(user_tokens), ratio * 100,
        )
        return ratio >= 0.5

    @staticmethod
    def _is_pinyin_query(text: str) -> bool:
        """Return True if text looks like a pinyin initial query.

        Criteria: all lowercase ASCII letters, length between 1 and 4.
        """
        return bool(re.fullmatch(r'[a-z]{1,4}', text.strip()))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_context(self, user_input: str) -> dict[str, Any]:
        """Return combined memory context for intent inference.

        Returns a dict with keys:
          shortcut_match   -- dict | None (high-confidence pre-fill)
          recent_tasks     -- last 5 conversation-history rows
          behavior_patterns -- matched task_pattern rows
          system_profiles   -- all registered systems
        """
        ctx: dict[str, Any] = {
            "shortcut_match": None,
            "recent_tasks": [],
            "behavior_patterns": [],
            "system_profiles": [],
        }

        with self._connect() as conn:
            # ----------------------------------------------------------
            # Step 1 – Shortcut detection: high-frequency patterns
            # Only patterns with use_count >= 3 are candidates.
            # ----------------------------------------------------------
            rows = conn.execute(
                "SELECT system_name, task_type, last_mapping, use_count "
                "FROM task_patterns WHERE use_count >= 3"
            ).fetchall()

            # 1a – Check shortcut_aliases table first (exact match)
            alias_row = conn.execute(
                "SELECT system_name, task_type FROM shortcut_aliases WHERE alias = ?",
                (user_input.strip().lower(),),
            ).fetchone()

            if alias_row:
                alias_sys, alias_task = alias_row
                # Find the matching task_pattern row for this alias
                for sys_name, task_type, last_mapping, _ in rows:
                    if sys_name == alias_sys and task_type == alias_task:
                        ctx["shortcut_match"] = {
                            "system_name": sys_name,
                            "task_type": task_type,
                            "last_mapping": self._parse_json(last_mapping, {}),
                            "prefill": True,
                        }
                        logger.debug(
                            "Shortcut matched via alias: %r -> %s/%s",
                            user_input, sys_name, task_type,
                        )
                        break

            # 1b – If no alias match, try fuzzy + pinyin matching
            if ctx["shortcut_match"] is None:
                is_pinyin = self._is_pinyin_query(user_input)
                for sys_name, task_type, last_mapping, _ in rows:
                    matched = False

                    # Try fuzzy match on system_name and task_type
                    if self._fuzzy_match(user_input, sys_name):
                        matched = True
                    elif self._fuzzy_match(user_input, task_type):
                        matched = True
                    elif self._fuzzy_match(user_input, f"{sys_name} {task_type}"):
                        matched = True

                    # Try pinyin initial matching for short alpha queries
                    if not matched and is_pinyin:
                        pinyin_input = user_input.strip().lower()
                        sys_pinyin = self._to_pinyin_initials(sys_name)
                        task_pinyin = self._to_pinyin_initials(task_type)
                        if sys_pinyin and pinyin_input in sys_pinyin:
                            matched = True
                        elif task_pinyin and pinyin_input in task_pinyin:
                            matched = True

                    if matched:
                        ctx["shortcut_match"] = {
                            "system_name": sys_name,
                            "task_type": task_type,
                            "last_mapping": self._parse_json(last_mapping, {}),
                            "prefill": True,
                        }
                        logger.debug(
                            "Shortcut matched via fuzzy/pinyin: %r -> %s/%s",
                            user_input, sys_name, task_type,
                        )
                        break  # first high-confidence match wins

            # ----------------------------------------------------------
            # Step 2 – Recent conversation history (last 5)
            # ----------------------------------------------------------
            rows = conn.execute(
                "SELECT timestamp, user_input, task_summary, files_used "
                "FROM conversation_history "
                "ORDER BY id DESC LIMIT 5"
            ).fetchall()
            ctx["recent_tasks"] = [
                {
                    "timestamp": r[0],
                    "user_input": r[1],
                    "task_summary": r[2],
                    "files_used": self._parse_json(r[3]),
                }
                for r in rows
            ]

            # ----------------------------------------------------------
            # Step 3 – Matching behaviour patterns
            # ----------------------------------------------------------
            rows = conn.execute(
                "SELECT system_name, task_type, default_source, "
                "       field_mapping_hist, last_mapping, use_count "
                "FROM task_patterns"
            ).fetchall()
            ctx["behavior_patterns"] = [
                {
                    "system_name": r[0],
                    "task_type": r[1],
                    "default_source": r[2],
                    "field_mapping_hist": self._parse_json(r[3]),
                    "last_mapping": self._parse_json(r[4], {}),
                    "use_count": r[5],
                }
                for r in rows
            ]

            # ----------------------------------------------------------
            # System profiles (always included for reference)
            # ----------------------------------------------------------
            rows = conn.execute("SELECT * FROM system_profiles").fetchall()
            ctx["system_profiles"] = [
                {
                    "id": r[0],
                    "name": r[1],
                    "url": r[2],
                    "login_url": r[3],
                    "last_used": r[4],
                    "use_count": r[5],
                    "session_path": r[6],
                }
                for r in rows
            ]

        return ctx

    def record_task(
        self,
        user_input: str,
        task_type: str,
        system_name: str,
        params: dict[str, Any],
        files_used: list[str],
    ) -> None:
        """Persist a completed task across all three memory layers."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        task_summary = f"{system_name}/{task_type}"
        field_mapping = params.get("field_mapping", params)

        with self._lock:
            with self._connect() as conn:
                # --- system_profiles ---
                conn.execute(
                    "INSERT INTO system_profiles (name, url, use_count, last_used) "
                    "VALUES (?, '', 1, ?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "  use_count = use_count + 1, "
                    "  last_used = excluded.last_used",
                    (system_name, now),
                )

                # --- task_patterns ---
                existing = conn.execute(
                    "SELECT field_mapping_hist, use_count FROM task_patterns "
                    "WHERE system_name = ? AND task_type = ?",
                    (system_name, task_type),
                ).fetchone()

                if existing:
                    hist_raw, old_count = existing
                    hist = self._parse_json(hist_raw)
                    if not isinstance(hist, list):
                        hist = []
                    hist.append(field_mapping)
                    conn.execute(
                        "UPDATE task_patterns SET "
                        "  field_mapping_hist = ?, "
                        "  last_mapping = ?, "
                        "  use_count = ? "
                        "WHERE system_name = ? AND task_type = ?",
                        (
                            json.dumps(hist, ensure_ascii=False),
                            json.dumps(field_mapping, ensure_ascii=False),
                            old_count + 1,
                            system_name,
                            task_type,
                        ),
                    )
                else:
                    conn.execute(
                        "INSERT INTO task_patterns (system_name, task_type, "
                        "  field_mapping_hist, last_mapping, use_count) "
                        "VALUES (?, ?, ?, ?, 1)",
                        (
                            system_name,
                            task_type,
                            json.dumps([field_mapping], ensure_ascii=False),
                            json.dumps(field_mapping, ensure_ascii=False),
                        ),
                    )

                # --- conversation_history ---
                conn.execute(
                    "INSERT OR IGNORE INTO conversation_history "
                    "(timestamp, user_input, task_summary, files_used) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        now,
                        user_input,
                        task_summary,
                        json.dumps(files_used, ensure_ascii=False),
                    ),
                )

            # Record alias outside the locked block (record_alias uses its own connect)
            alias_text = user_input.strip()[:20]
            if alias_text:
                self.record_alias(system_name, task_type, alias_text)

    def get_all_systems(self) -> list[dict[str, Any]]:
        """Return every registered system profile."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM system_profiles").fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "url": r[2],
                "login_url": r[3],
                "last_used": r[4],
                "use_count": r[5],
                "session_path": r[6],
            }
            for r in rows
        ]

    def upsert_system(self, name: str, url: str, login_url: str = "") -> int:
        """Insert or update a system profile. Returns the row id."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO system_profiles (name, url, login_url, last_used, use_count) "
                    "VALUES (?, ?, ?, ?, 1) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "  url = excluded.url, "
                    "  login_url = excluded.login_url, "
                    "  last_used = excluded.last_used",
                    (name, url, login_url, now),
                )
                row = conn.execute(
                    "SELECT id FROM system_profiles WHERE name = ?", (name,)
                ).fetchone()
        return row[0] if row else -1

    def suggest_shortcuts(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return the most-used shortcuts for UI suggestions.

        Only returns task patterns with use_count >= 3, ordered by
        descending use_count.

        Returns:
            list of dicts with keys: trigger, task_type, system_name, use_count
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT system_name, task_type, use_count "
                "FROM task_patterns WHERE use_count >= 3 "
                "ORDER BY use_count DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [
            {
                "trigger": f"{r[0]}/{r[1]}",
                "system_name": r[0],
                "task_type": r[1],
                "use_count": r[2],
            }
            for r in rows
        ]

    def record_alias(self, system_name: str, task_type: str, alias: str) -> None:
        """Record a variant phrasing the user used for a task.

        The alias is stored in shortcut_aliases for future exact-match
        shortcut detection. Duplicate aliases are silently ignored.
        """
        alias_clean = alias.strip().lower()
        if not alias_clean:
            return

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with self._lock:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO shortcut_aliases "
                        "(system_name, task_type, alias, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (system_name, task_type, alias_clean, now),
                    )
            logger.debug(
                "Recorded alias %r for %s/%s", alias_clean, system_name, task_type,
            )
        except Exception:
            logger.exception(
                "Failed to record alias %r for %s/%s",
                alias_clean, system_name, task_type,
            )

    def close(self) -> None:
        """Explicit close (no-op for sqlite3; kept for interface symmetry)."""
        # Connections are short-lived and closed via context-manager.
        # This method exists for caller convenience.
        pass
