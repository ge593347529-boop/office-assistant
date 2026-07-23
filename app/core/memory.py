"""Memory system backed by a local SQLite database.

Three-layer memory:
  1. System configuration profiles (URLs, login, session paths)
  2. Behavioural task patterns (data sources, field mappings per task)
  3. Conversation history summaries

Short-term memory + long-term patterns = smarter over time.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


class MemoryStore:
    """Thread-safe SQLite-backed memory store with three-layer memory."""

    def __init__(self, db_path: str = "data/memory.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
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

            keywords = user_input.lower()
            for sys_name, task_type, last_mapping, _ in rows:
                # Simple keyword match – both system and task names
                # contribute to the shortcut heuristic.
                if sys_name.lower() in keywords or task_type.lower().replace("_", " ") in keywords:
                    ctx["shortcut_match"] = {
                        "system_name": sys_name,
                        "task_type": task_type,
                        "last_mapping": self._parse_json(last_mapping, {}),
                        "prefill": True,
                    }
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

    def close(self) -> None:
        """Explicit close (no-op for sqlite3; kept for interface symmetry)."""
        # Connections are short-lived and closed via context-manager.
        # This method exists for caller convenience.
        pass
