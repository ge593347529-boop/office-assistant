"""
System tools layer — clipboard read/write, command execution, window info, file/folder opening.

Deterministic utility scripts. No AI calls.
"""

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_line_endings(text: str) -> str:
    """Ensure consistent line endings; clipboard on Windows prefers CRLF."""
    if not text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def _pwsh_clipboard_get() -> str | None:
    """Read clipboard text via PowerShell.  Returns None on failure."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Clipboard -Format Text",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        logger.debug("PowerShell Get-Clipboard failed", exc_info=True)
    return None


def _pwsh_clipboard_set(text: str) -> bool:
    """Set clipboard text via PowerShell.  Returns True on success."""
    try:
        cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "[System.Windows.Forms.Clipboard]::SetText($Input); "
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            input=text,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        logger.debug("PowerShell clipboard set failed", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# SystemTool
# ---------------------------------------------------------------------------

class SystemTool:
    """Stateless system utilities — clipboard, commands, shell, env."""

    # ---- clipboard ---------------------------------------------------------

    @staticmethod
    def get_clipboard() -> str:
        """Read text from the Windows clipboard.

        Tries pywin32 first, falls back to PowerShell.
        Returns an empty string on any error.
        """
        try:
            import win32clipboard

            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(
                    win32clipboard.CF_UNICODETEXT
                ):
                    data = win32clipboard.GetClipboardData(
                        win32clipboard.CF_UNICODETEXT
                    )
                    return data if data else ""
                return ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            logger.debug("pywin32 clipboard read failed, falling back", exc_info=True)
            try:
                result = _pwsh_clipboard_get()
                if result is not None:
                    return result
            except Exception:
                logger.debug("PowerShell clipboard fallback also failed", exc_info=True)
            return ""

    @staticmethod
    def set_clipboard(text: str) -> None:
        """Write *text* to the Windows clipboard.

        Tries pywin32 first, falls back to PowerShell.
        Silently ignores errors (logs a warning).
        """
        if not text:
            return  # nothing to do
        try:
            import win32clipboard

            normalized = _normalize_line_endings(text)
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(normalized, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            logger.debug("Clipboard set via pywin32")
            return
        except Exception:
            logger.debug("pywin32 clipboard set failed, falling back", exc_info=True)

        if not _pwsh_clipboard_set(text):
            logger.warning("Failed to set clipboard via PowerShell fallback")

    # ---- command execution -------------------------------------------------

    @staticmethod
    def run_command(
        cmd: str,
        timeout: int = 30,
        cwd: str | None = None,
    ) -> tuple[int, str, str]:
        """Execute a system command via the default shell.

        Returns ``(returncode, stdout, stderr)``.
        On timeout the process is killed and ``returncode`` is -1.
        """
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                shell=True,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            # TimeoutExpired objects carry partial output in 3.11+,
            # but we keep it simple for broad compat.
            logger.warning("Command timed out after %ds: %s", timeout, cmd)
            return -1, "", f"Command timed out after {timeout}s"
        except Exception as exc:
            logger.error("Command failed: %s — %s", cmd, exc)
            return -1, "", str(exc)

    # ---- window info -------------------------------------------------------

    @staticmethod
    def get_active_window_title() -> str:
        """Return the title of the currently focused window.

        Uses pywin32.  Returns ``""`` if pywin32 is unavailable or on error.
        """
        try:
            import win32gui

            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            return title if title else ""
        except Exception:
            logger.debug("get_active_window_title failed", exc_info=True)
            return ""

    # ---- file / folder opening --------------------------------------------

    @staticmethod
    def open_file(filepath: str) -> bool:
        """Open *filepath* with the OS default handler.

        Uses ``os.startfile`` (Windows-only, most reliable).
        Returns ``True`` on success.
        """
        try:
            os.startfile(os.path.normpath(filepath))
            logger.debug("Opened file: %s", filepath)
            return True
        except Exception:
            logger.warning("Failed to open file: %s", filepath, exc_info=True)
            return False

    @staticmethod
    def open_folder(path: str) -> bool:
        """Open *path* in Windows Explorer.

        Delegates to :meth:`open_file` — ``os.startfile`` works for
        folders as well.
        """
        return SystemTool.open_file(path)

    # ---- environment ------------------------------------------------------

    @staticmethod
    def get_env_var(name: str) -> str | None:
        """Return the value of environment variable *name*, or ``None``."""
        try:
            return os.environ.get(name)
        except Exception:
            logger.debug("get_env_var('%s') failed", name, exc_info=True)
            return None

    @staticmethod
    def get_username() -> str:
        """Return the current Windows username.

        Tries ``os.getlogin()`` first, then ``USERNAME`` env var.
        Returns ``""`` on failure.
        """
        try:
            return os.getlogin()
        except Exception:
            logger.debug("os.getlogin() failed, trying USERNAME", exc_info=True)
        try:
            return os.environ.get("USERNAME", "")
        except Exception:
            logger.warning("Cannot determine username", exc_info=True)
            return ""

    @staticmethod
    def get_computer_name() -> str:
        """Return the Windows computer name.

        Tries ``COMPUTERNAME`` env var first, then ``os.environ['COMPUTERNAME']``.
        Returns ``""`` on failure.
        """
        try:
            return os.environ.get(
                "COMPUTERNAME",
                os.environ.get("COMPUTER_NAME", ""),
            )
        except Exception:
            logger.warning("Cannot determine computer name", exc_info=True)
            return ""
