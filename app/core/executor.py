"""Task executor -- deterministic execution engine.

Receives a TaskResult from the inference layer, routes it to the
appropriate deterministic handler, and returns an ExecutionResult.
No AI calls -- pure code execution.
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config.settings import AppConfig
from app.core.inference import TaskResult
from app.core.memory import MemoryStore
from app.tools.browser import BrowserTool, LoginResult
from app.tools.excel import ExcelTool
from app.tools.filesystem import FileSystemTool
from app.tools.system import SystemTool
from app.tools.word import WordTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ExecutionResult:
    """Result produced by the deterministic execution layer."""

    success: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    screenshot: bytes | None = None


# ---------------------------------------------------------------------------
# TaskExecutor
# ---------------------------------------------------------------------------


class TaskExecutor:
    """Deterministic execution engine.

    Receives a structured TaskResult from the inference pipeline and
    dispatches it to the correct handler based on ``task_type``.
    """

    # Route table: task_type -> handler method name
    _ROUTE: dict[str, str] = {
        "form_filling": "_execute_form_filling",
        "data_extraction": "_execute_data_extraction",
        "file_organize": "_execute_file_organize",
        "batch_rename": "_execute_batch_rename",
        "excel_report": "_execute_excel_report",
        "web_monitor": "_execute_web_monitor",
    }

    def __init__(self, config: AppConfig, memory: MemoryStore) -> None:
        self._config = config
        self._memory = memory

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def check_user_chrome_available(self) -> bool:
        """Check whether the user's Chrome debugging port is reachable.

        Attempts a lightweight CDP version query on the configured port.
        Returns ``True`` if Chrome responded, ``False`` otherwise.
        """
        import urllib.request
        import json as _json

        url = f"http://localhost:{self._config.chrome_debug_port}/json/version"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                _json.loads(resp.read().decode("utf-8"))
            return True
        except Exception:
            logger.debug("Chrome CDP not reachable on port %d", self._config.chrome_debug_port)
            return False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def execute(
        self,
        task: TaskResult,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> ExecutionResult:
        """Execute a task and return a structured result.

        Parameters
        ----------
        task : TaskResult
            Structured task from the inference engine.
        on_progress : Callable[[str, float], None] | None
            Optional progress callback receiving ``(status_message, progress_float)``.
            ``progress_float`` ranges from 0.0 to 1.0.

        Returns
        -------
        ExecutionResult
            Always returns a result -- exceptions are caught internally.
        """
        # ---- General chat: short-circuit ----
        if task.task_type == "general_chat":
            return ExecutionResult(
                success=True,
                message=task.clarification_question or "收到您的消息。",
            )

        # ---- Unknown: return clarification ----
        if task.task_type == "unknown":
            return ExecutionResult(
                success=False,
                message=task.clarification_question or "抱歉，无法理解您的请求。",
            )

        # ---- Dispatch to handler ----
        handler_name = self._ROUTE.get(task.task_type)
        if handler_name is None:
            logger.warning("No handler for task_type=%r", task.task_type)
            return ExecutionResult(
                success=False,
                message=f"不支持的任务类型: {task.task_type}",
            )

        handler = getattr(self, handler_name)

        try:
            return handler(task, on_progress)
        except Exception as exc:
            logger.exception(
                "Handler %s failed for task_type=%r system=%r",
                handler_name, task.task_type, task.system_name,
            )
            return ExecutionResult(
                success=False,
                message=f"执行失败: {exc}",
                details={"traceback": traceback.format_exc()},
            )

    # ------------------------------------------------------------------
    # Browser helper
    # ------------------------------------------------------------------

    def _get_browser(self, task: TaskResult) -> BrowserTool:
        """Create a BrowserTool and launch or connect based on task mode."""
        browser = BrowserTool(self._config)
        if task.mode == "B":
            browser.connect()
        else:
            browser.launch()
        return browser

    def _resolve_url(self, task: TaskResult) -> str:
        """Resolve the target URL from system profiles or params."""
        # Try memory store system profiles first
        if task.system_name:
            systems = self._memory.get_all_systems()
            for sys_info in systems:
                if sys_info.get("name", "").lower() == task.system_name.lower():
                    if sys_info.get("url"):
                        logger.debug("Resolved URL from system profile: %s", sys_info["url"])
                        return sys_info["url"]
        # Fall back to url_hint in params
        url = task.params.get("url_hint", "")
        if not url:
            url = task.params.get("url", "")
        if not url:
            raise ValueError(
                f"无法确定 '{task.system_name}' 的 URL，"
                f"请先在系统配置中保存 URL 或在请求中提供 url_hint。"
            )
        return url

    def _handle_browser_login(
        self,
        browser: BrowserTool,
        task: TaskResult,
        url: str,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> ExecutionResult | None:
        """Navigate to URL and handle login. Returns an ExecutionResult
        if manual login is needed, or None if login succeeded."""
        _report(on_progress, "正在导航到目标页面...", 0.15)
        browser.navigate(url)

        _report(on_progress, "正在检查登录状态...", 0.25)
        login_result = browser.handle_login(task.system_name)

        if login_result.needs_manual:
            logger.info("Login requires manual intervention for %s", task.system_name)
            return ExecutionResult(
                success=False,
                message=f"需要手动登录: {login_result.message}",
                details={
                    "needs_login": True,
                    "system_name": task.system_name,
                    "login_message": login_result.message,
                },
                screenshot=login_result.screenshot,
            )

        # Success -- login_result.success is True
        logger.info("Login OK for %s: %s", task.system_name, login_result.message)
        return None

    # ------------------------------------------------------------------
    # Handler: form_filling
    # ------------------------------------------------------------------

    def _execute_form_filling(
        self,
        task: TaskResult,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> ExecutionResult:
        """Read Excel data and fill web forms.

        Expected ``task.params`` keys:
        - ``data_source``: path to the Excel file
        - ``field_mapping``: dict mapping Excel column -> HTML field selector
        - ``url_hint`` (optional): fallback URL
        """
        _report(on_progress, "正在读取数据源...", 0.05)

        data_source = task.params.get("data_source", "")
        if not data_source:
            return ExecutionResult(success=False, message="缺少数据源路径 (data_source)")

        field_mapping = task.params.get("field_mapping", {})
        if not field_mapping:
            return ExecutionResult(success=False, message="缺少字段映射 (field_mapping)")

        # Step 1: Read Excel
        data = ExcelTool.read(data_source)
        if not data:
            return ExecutionResult(success=False, message=f"数据源为空或无法读取: {data_source}")

        # Step 2: Launch/connect browser
        _report(on_progress, "正在启动浏览器...", 0.10)
        browser = self._get_browser(task)

        try:
            # Step 3: Resolve URL
            url = self._resolve_url(task)

            # Step 4: Navigate + login
            login_block = self._handle_browser_login(browser, task, url, on_progress)
            if login_block is not None:
                return login_block

            # Step 5: Fill forms row by row
            total = len(data)
            field_map = field_mapping
            for idx, row in enumerate(data):
                row_num = idx + 1
                pct = 0.30 + (0.55 * (idx / max(total, 1)))
                _report(on_progress, f"正在填写第 {row_num}/{total} 条记录...", pct)

                for excel_col, css_selector in field_map.items():
                    value = str(row.get(excel_col, ""))
                    if value:
                        try:
                            browser.fill(css_selector, value)
                        except Exception as exc:
                            logger.warning(
                                "填充字段失败 (row=%d, col=%s, selector=%s): %s",
                                row_num, excel_col, css_selector, exc,
                            )

                # If there is a submit button configured, click it
                submit_sel = task.params.get("submit_selector", "")
                if submit_sel:
                    try:
                        browser.click(submit_sel)
                    except Exception as exc:
                        logger.warning("点击提交按钮失败 (row=%d): %s", row_num, exc)

            # Step 6: Screenshot
            _report(on_progress, "正在截图...", 0.90)
            screenshot = browser.screenshot()

            # Step 7: Record in memory
            _report(on_progress, "正在记录任务...", 0.95)
            self._memory.record_task(
                user_input=task.system_name,
                task_type=task.task_type,
                system_name=task.system_name,
                params=task.params,
                files_used=[data_source],
            )

            _report(on_progress, "完成", 1.0)
            return ExecutionResult(
                success=True,
                message=f"已填写 {total} 条记录",
                details={"record_count": total, "data_source": data_source},
                screenshot=screenshot,
            )

        finally:
            browser.close()

    # ------------------------------------------------------------------
    # Handler: data_extraction
    # ------------------------------------------------------------------

    def _execute_data_extraction(
        self,
        task: TaskResult,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> ExecutionResult:
        """Extract data from a web page and write to Excel.

        Expected ``task.params`` keys:
        - ``table_selector`` (optional): CSS selector for a <table>
        - ``output_path``: where to save the extracted Excel file
        - ``url_hint`` (optional): fallback URL
        - ``extract_mode``: "table" (default) or "text"
        """
        _report(on_progress, "正在启动浏览器...", 0.05)

        browser = self._get_browser(task)

        try:
            # Navigate + login
            url = self._resolve_url(task)
            login_block = self._handle_browser_login(browser, task, url, on_progress)
            if login_block is not None:
                return login_block

            extract_mode = task.params.get("extract_mode", "table")
            output_path = task.params.get("output_path", "")
            if not output_path:
                output_path = f"data/extracted_{task.system_name}_{_timestamp()}.xlsx"

            if extract_mode == "table":
                _report(on_progress, "正在提取表格数据...", 0.50)
                table_selector = task.params.get("table_selector", "table")
                rows = browser.get_table_data(table_selector)

                if not rows:
                    return ExecutionResult(
                        success=False,
                        message=f"未找到表格数据 (selector={table_selector})",
                        screenshot=browser.screenshot(),
                    )

                _report(on_progress, f"正在写入 {len(rows)} 行数据到 Excel...", 0.80)
                ExcelTool.write(output_path, rows)

                _report(on_progress, "正在记录任务...", 0.95)
                self._memory.record_task(
                    user_input=task.system_name,
                    task_type=task.task_type,
                    system_name=task.system_name,
                    params=task.params,
                    files_used=[output_path],
                )

                _report(on_progress, "完成", 1.0)
                return ExecutionResult(
                    success=True,
                    message=f"已提取 {len(rows)} 行数据到 {output_path}",
                    details={"row_count": len(rows), "output_path": output_path},
                    screenshot=browser.screenshot(),
                )

            else:  # text mode
                _report(on_progress, "正在提取页面文本...", 0.50)
                text = browser.get_text("body")

                rows = [{"content": text}]
                _report(on_progress, f"正在保存文本到 Excel...", 0.80)
                ExcelTool.write(output_path, rows)

                _report(on_progress, "正在记录任务...", 0.95)
                self._memory.record_task(
                    user_input=task.system_name,
                    task_type=task.task_type,
                    system_name=task.system_name,
                    params=task.params,
                    files_used=[output_path],
                )

                _report(on_progress, "完成", 1.0)
                return ExecutionResult(
                    success=True,
                    message=f"页面文本已保存到 {output_path}",
                    details={"text_length": len(text), "output_path": output_path},
                    screenshot=browser.screenshot(),
                )

        finally:
            browser.close()

    # ------------------------------------------------------------------
    # Handler: file_organize
    # ------------------------------------------------------------------

    def _execute_file_organize(
        self,
        task: TaskResult,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> ExecutionResult:
        """Organize files by type in a directory.

        Expected ``task.params`` keys:
        - ``directory``: target directory path
        - ``dry_run`` (optional): preview only, default False
        """
        _report(on_progress, "正在分析文件...", 0.10)
        directory = task.params.get("directory", "")
        if not directory:
            return ExecutionResult(success=False, message="缺少目标目录路径 (directory)")

        dry_run = task.params.get("dry_run", False)

        _report(on_progress, "正在整理文件...", 0.40)
        try:
            organized = FileSystemTool.organize_by_type(directory, dry_run=dry_run)
        except (FileNotFoundError, NotADirectoryError) as exc:
            return ExecutionResult(success=False, message=str(exc))

        file_count = sum(len(files) for files in organized.values())

        _report(on_progress, "正在记录任务...", 0.90)
        self._memory.record_task(
            user_input=task.system_name,
            task_type=task.task_type,
            system_name=task.system_name,
            params=task.params,
            files_used=[directory],
        )

        _report(on_progress, "完成", 1.0)
        return ExecutionResult(
            success=True,
            message=f"已整理 {file_count} 个文件到 {len(organized)} 个分类",
            details={
                "directory": directory,
                "file_count": file_count,
                "categories": organized,
                "dry_run": dry_run,
            },
        )

    # ------------------------------------------------------------------
    # Handler: batch_rename
    # ------------------------------------------------------------------

    def _execute_batch_rename(
        self,
        task: TaskResult,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> ExecutionResult:
        """Batch rename files in a directory.

        Expected ``task.params`` keys:
        - ``directory``: target directory path
        - ``old_pattern``: pattern to match (string or regex)
        - ``new_pattern``: replacement string
        - ``dry_run`` (optional): preview only, default False
        """
        _report(on_progress, "正在扫描文件...", 0.10)
        directory = task.params.get("directory", "")
        old_pattern = task.params.get("old_pattern", "")
        new_pattern = task.params.get("new_pattern", "")

        if not directory:
            return ExecutionResult(success=False, message="缺少目标目录路径 (directory)")
        if not old_pattern:
            return ExecutionResult(success=False, message="缺少匹配模式 (old_pattern)")

        dry_run = task.params.get("dry_run", False)

        _report(on_progress, "正在批量重命名...", 0.40)
        try:
            renamed = FileSystemTool.batch_rename(
                directory, old_pattern, new_pattern, dry_run=dry_run
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            return ExecutionResult(success=False, message=str(exc))

        _report(on_progress, "正在记录任务...", 0.90)
        self._memory.record_task(
            user_input=task.system_name,
            task_type=task.task_type,
            system_name=task.system_name,
            params=task.params,
            files_used=[directory],
        )

        _report(on_progress, "完成", 1.0)
        return ExecutionResult(
            success=True,
            message=f"已重命名 {len(renamed)} 个文件",
            details={
                "directory": directory,
                "renamed_count": len(renamed),
                "renamed_files": renamed,
                "dry_run": dry_run,
            },
        )

    # ------------------------------------------------------------------
    # Handler: excel_report
    # ------------------------------------------------------------------

    def _execute_excel_report(
        self,
        task: TaskResult,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> ExecutionResult:
        """Generate or manipulate Excel reports.

        Expected ``task.params`` keys:
        - ``action``: "read" | "create" | "get_info"
        - ``filepath``: target Excel file path
        - ``data`` (for create): list[dict] of data to write
        - ``sheet_name`` (optional): sheet name
        - ``open_in_wps`` (optional): open file after operation
        """
        action = task.params.get("action", "read")
        filepath = task.params.get("filepath", "")
        sheet_name = task.params.get("sheet_name")

        if not filepath:
            return ExecutionResult(success=False, message="缺少文件路径 (filepath)")

        try:
            if action == "read":
                _report(on_progress, "正在读取 Excel 文件...", 0.30)
                data = ExcelTool.read(filepath, sheet_name=sheet_name)
                info = ExcelTool.get_info(filepath)

                _report(on_progress, "正在记录任务...", 0.90)
                self._memory.record_task(
                    user_input=task.system_name,
                    task_type=task.task_type,
                    system_name=task.system_name,
                    params=task.params,
                    files_used=[filepath],
                )

                _report(on_progress, "完成", 1.0)
                return ExecutionResult(
                    success=True,
                    message=f"已读取 {len(data)} 行数据",
                    details={
                        "filepath": filepath,
                        "row_count": len(data),
                        "info": info,
                        "preview": data[:20],  # first 20 rows as preview
                    },
                )

            elif action == "create":
                _report(on_progress, "正在创建 Excel 文件...", 0.30)
                data = task.params.get("data", [])
                if not data:
                    return ExecutionResult(success=False, message="缺少要写入的数据 (data)")

                ExcelTool.write(filepath, data, sheet_name=sheet_name or "Sheet1")

                _report(on_progress, "正在记录任务...", 0.90)
                self._memory.record_task(
                    user_input=task.system_name,
                    task_type=task.task_type,
                    system_name=task.system_name,
                    params=task.params,
                    files_used=[filepath],
                )

                # Optionally open in WPS
                if task.params.get("open_in_wps"):
                    ExcelTool.open_in_wps(filepath)

                _report(on_progress, "完成", 1.0)
                return ExecutionResult(
                    success=True,
                    message=f"已创建报表: {filepath} ({len(data)} 行)",
                    details={"filepath": filepath, "row_count": len(data)},
                )

            elif action == "get_info":
                _report(on_progress, "正在获取文件信息...", 0.50)
                info = ExcelTool.get_info(filepath)

                _report(on_progress, "完成", 1.0)
                return ExecutionResult(
                    success=True,
                    message=f"文件信息: {filepath}",
                    details={"filepath": filepath, "info": info},
                )

            else:
                return ExecutionResult(
                    success=False,
                    message=f"不支持的 Excel 操作: {action}",
                )

        except FileNotFoundError as exc:
            return ExecutionResult(success=False, message=str(exc))
        except Exception as exc:
            logger.exception("Excel operation failed: action=%s filepath=%s", action, filepath)
            return ExecutionResult(
                success=False,
                message=f"Excel 操作失败: {exc}",
                details={"traceback": traceback.format_exc()},
            )

    # ------------------------------------------------------------------
    # Handler: web_monitor
    # ------------------------------------------------------------------

    def _execute_web_monitor(
        self,
        task: TaskResult,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> ExecutionResult:
        """Take a screenshot and extract text from a web page.

        Expected ``task.params`` keys:
        - ``url_hint`` (optional): fallback URL
        - ``selector`` (optional): CSS selector for specific text area
        """
        _report(on_progress, "正在启动浏览器...", 0.05)
        browser = self._get_browser(task)

        try:
            # Navigate + login
            url = self._resolve_url(task)
            login_block = self._handle_browser_login(browser, task, url, on_progress)
            if login_block is not None:
                return login_block

            _report(on_progress, "正在截图...", 0.50)
            screenshot = browser.screenshot()

            _report(on_progress, "正在提取文本...", 0.70)
            selector = task.params.get("selector", "body")
            text = browser.get_text(selector)

            _report(on_progress, "正在记录任务...", 0.90)
            self._memory.record_task(
                user_input=task.system_name,
                task_type=task.task_type,
                system_name=task.system_name,
                params=task.params,
                files_used=[],
            )

            _report(on_progress, "完成", 1.0)
            return ExecutionResult(
                success=True,
                message=f"页面监控完成: {url}",
                details={
                    "url": url,
                    "text_length": len(text),
                    "text_preview": text[:500],
                },
                screenshot=screenshot,
            )

        finally:
            browser.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _report(
    on_progress: Callable[[str, float], None] | None,
    msg: str,
    pct: float,
) -> None:
    """Fire the on_progress callback if provided."""
    if on_progress is not None:
        try:
            on_progress(msg, pct)
        except Exception:
            logger.debug("on_progress callback failed for msg=%r", msg, exc_info=True)


def _timestamp() -> str:
    """Return a compact UTC timestamp string for file naming."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
