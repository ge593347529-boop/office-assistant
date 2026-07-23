"""
文件系统操作工具 - 纯 Python 标准库实现
Windows 桌面 AI 办公助手
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 按扩展名映射到中文分类（全部小写）
# ---------------------------------------------------------------------------
EXTENSION_CATEGORY_MAP: dict[str, str] = {
    # 表格
    ".xlsx": "表格",
    ".xls": "表格",
    ".xlsm": "表格",
    ".csv": "表格",
    ".tsv": "表格",
    # 文档
    ".docx": "文档",
    ".doc": "文档",
    ".odt": "文档",
    ".rtf": "文档",
    ".txt": "文档",
    ".md": "文档",
    ".markdown": "文档",
    # PDF
    ".pdf": "PDF",
    # 图片
    ".jpg": "图片",
    ".jpeg": "图片",
    ".png": "图片",
    ".gif": "图片",
    ".bmp": "图片",
    ".tiff": "图片",
    ".tif": "图片",
    ".webp": "图片",
    ".svg": "图片",
    ".ico": "图片",
    # 演示
    ".pptx": "演示",
    ".ppt": "演示",
    # 压缩
    ".zip": "压缩包",
    ".rar": "压缩包",
    ".7z": "压缩包",
    ".tar": "压缩包",
    ".gz": "压缩包",
    ".bz2": "压缩包",
    # 代码
    ".py": "代码",
    ".js": "代码",
    ".ts": "代码",
    ".html": "代码",
    ".css": "代码",
    ".json": "代码",
    ".xml": "代码",
    ".yaml": "代码",
    ".yml": "代码",
    ".toml": "代码",
    ".ini": "代码",
    ".cfg": "代码",
    # 音视频
    ".mp3": "音频",
    ".wav": "音频",
    ".flac": "音频",
    ".aac": "音频",
    ".ogg": "音频",
    ".wma": "音频",
    ".mp4": "视频",
    ".avi": "视频",
    ".mkv": "视频",
    ".mov": "视频",
    ".wmv": "视频",
    ".flv": "视频",
    # 可执行/库
    ".exe": "可执行文件",
    ".dll": "库文件",
    ".msi": "安装包",
}

DEFAULT_CATEGORY = "其他"

# 分块读取大小，用于计算大文件 MD5
HASH_CHUNK_SIZE = 8192  # 8 KB


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class FileInfo:
    """文件基本信息。"""
    name: str
    path: str
    size: int          # bytes
    modified: str      # ISO format datetime, e.g. "2026-07-23T14:30:00"
    extension: str     # 扩展名，小写，含点号，如 ".xlsx"
    is_dir: bool


# ============================================================================
# 工具函数
# ============================================================================

def _resolve(path: str) -> Path:
    """将字符串路径解析为绝对 Path。"""
    return Path(path).expanduser().resolve()


def _scandir(path: Path, pattern: str = "*", recursive: bool = False) -> list[Path]:
    """扫描目录，返回匹配的路径列表。"""
    if recursive:
        return sorted(path.rglob(pattern))
    else:
        return sorted(path.glob(pattern))


def _make_fileinfo(p: Path) -> FileInfo:
    """从 Path 构建 FileInfo。"""
    stat = p.stat()
    return FileInfo(
        name=p.name,
        path=str(p),
        size=stat.st_size if p.is_file() else 0,
        modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        extension=p.suffix.lower(),
        is_dir=p.is_dir(),
    )


def _compute_md5(file_path: Path) -> str:
    """计算文件 MD5 哈希（分块读取，适用于大文件）。"""
    hasher = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(HASH_CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
    except (PermissionError, OSError) as exc:
        logger.warning("无法读取文件 %s: %s", file_path, exc)
        return ""


def _move_file(src: Path, dst: Path, dry_run: bool) -> bool:
    """移动文件（dry_run 模式下仅记录）。返回是否成功。"""
    if dry_run:
        logger.info("[DRY RUN] 将移动: %s -> %s", src, dst)
        return True

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        logger.info("已移动: %s -> %s", src, dst)
        return True
    except (PermissionError, OSError) as exc:
        logger.error("移动失败 %s -> %s: %s", src, dst, exc)
        return False


def _rename_file(src: Path, dst: Path, dry_run: bool) -> bool:
    """重命名文件。返回是否成功。"""
    if dry_run:
        logger.info("[DRY RUN] 将重命名: %s -> %s", src, dst)
        return True

    try:
        # 在 Windows 上，如果 dst 已存在，os.rename 会失败；
        # 使用 shutil.move 更通用，但也可能直接覆盖。先检查。
        if dst.exists():
            logger.warning("目标已存在，跳过重命名: %s", dst)
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(str(src), str(dst))
        logger.info("已重命名: %s -> %s", src, dst)
        return True
    except (PermissionError, OSError) as exc:
        logger.error("重命名失败 %s -> %s: %s", src, dst, exc)
        return False


# ============================================================================
# FileSystemTool
# ============================================================================

class FileSystemTool:
    """文件系统操作工具。纯静态方法，无状态。"""

    # ------------------------------------------------------------------
    # list_files
    # ------------------------------------------------------------------

    @staticmethod
    def list_files(
        directory: str,
        pattern: str = "*",
        recursive: bool = False,
    ) -> list[FileInfo]:
        """
        列出目录中的文件。

        Args:
            directory: 目标目录路径
            pattern:  glob 模式，如 "*.xlsx" 或 "**/*.py"
            recursive: 是否递归子目录

        Returns:
            FileInfo 列表，按路径排序
        """
        root = _resolve(directory)
        if not root.exists():
            raise FileNotFoundError(f"目录不存在: {directory}")
        if not root.is_dir():
            raise NotADirectoryError(f"路径不是目录: {directory}")

        entries = _scandir(root, pattern=pattern, recursive=recursive)
        return [_make_fileinfo(entry) for entry in entries]

    # ------------------------------------------------------------------
    # search_files
    # ------------------------------------------------------------------

    @staticmethod
    def search_files(
        directory: str,
        keyword: str,
        recursive: bool = True,
    ) -> list[str]:
        """
        搜索文件名包含 keyword 的文件。

        Args:
            directory: 搜索根目录
            keyword:  文件名关键词（大小写不敏感，部分匹配）
            recursive: 是否递归子目录

        Returns:
            匹配的文件路径字符串列表
        """
        root = _resolve(directory)
        if not root.is_dir():
            raise NotADirectoryError(f"目录不存在或不是目录: {directory}")

        keyword_lower = keyword.lower()
        results: list[str] = []

        iterator = root.rglob("*") if recursive else root.glob("*")
        for entry in iterator:
            if entry.is_file() and keyword_lower in entry.name.lower():
                results.append(str(entry))

        return sorted(results)

    # ------------------------------------------------------------------
    # batch_rename
    # ------------------------------------------------------------------

    @staticmethod
    def batch_rename(
        directory: str,
        old_pattern: str,
        new_pattern: str,
        dry_run: bool = False,
    ) -> list[tuple[str, str]]:
        """
        批量重命名文件。

        支持两种模式：
          1) 普通字符串替换：如 old_pattern="报告_" new_pattern="周报_"
          2) 正则替换：先尝试将 old_pattern 编译为正则，若成功则使用 re.sub

        Args:
            directory:   目标目录
            old_pattern: 要匹配的字符串或正则表达式
            new_pattern: 替换字符串（正则模式下可使用反向引用如 \\1）
            dry_run:     仅预览，不实际执行

        Returns:
            [(旧路径, 新路径), ...] 按旧路径排序
        """
        root = _resolve(directory)
        if not root.is_dir():
            raise NotADirectoryError(f"目录不存在或不是目录: {directory}")

        # 尝试将 old_pattern 编译为正则，若失败则按普通字符串替换处理
        try:
            regex = re.compile(old_pattern)
            use_regex = True
        except re.error:
            use_regex = False

        renamed: list[tuple[str, str]] = []

        for entry in sorted(root.iterdir()):
            if not entry.is_file():
                continue

            old_name = entry.name
            if use_regex:
                new_name = regex.sub(new_pattern, old_name)
            else:
                new_name = old_name.replace(old_pattern, new_pattern)

            # 名称未变化则跳过
            if new_name == old_name:
                continue

            new_path = entry.parent / new_name
            renamed.append((str(entry), str(new_path)))
            _rename_file(entry, new_path, dry_run=dry_run)

        return renamed

    # ------------------------------------------------------------------
    # organize_by_type
    # ------------------------------------------------------------------

    @staticmethod
    def organize_by_type(
        directory: str,
        dry_run: bool = False,
    ) -> dict[str, list[str]]:
        """
        按文件类型（扩展名）整理到子文件夹。

        例如：.xlsx/.xls -> 表格/  ,  .docx/.doc -> 文档/  ,  .pdf -> PDF/

        Args:
            directory: 要整理的目录
            dry_run:   仅预览，不实际移动

        Returns:
            {"表格": ["a.xlsx", ...], "文档": [...], ...}
        """
        root = _resolve(directory)
        if not root.is_dir():
            raise NotADirectoryError(f"目录不存在或不是目录: {directory}")

        organized: dict[str, list[str]] = defaultdict(list)

        for entry in sorted(root.iterdir()):
            if not entry.is_file():
                continue

            ext = entry.suffix.lower()
            category = EXTENSION_CATEGORY_MAP.get(ext, DEFAULT_CATEGORY)

            dest_dir = root / category
            dest_path = dest_dir / entry.name

            if _move_file(entry, dest_path, dry_run=dry_run):
                organized[category].append(entry.name)
            else:
                logger.warning("整理失败，跳过: %s", entry)

        # defaultdict -> dict
        return dict(organized)

    # ------------------------------------------------------------------
    # organize_by_date
    # ------------------------------------------------------------------

    @staticmethod
    def organize_by_date(
        directory: str,
        dry_run: bool = False,
    ) -> dict[str, list[str]]:
        """
        按修改日期（年-月）整理到子文件夹。

        例如：2026 年 7 月修改的文件 -> 2026-07/

        Args:
            directory: 要整理的目录
            dry_run:   仅预览，不实际移动

        Returns:
            {"2026-07": ["a.docx", ...], "2026-06": [...], ...}
        """
        root = _resolve(directory)
        if not root.is_dir():
            raise NotADirectoryError(f"目录不存在或不是目录: {directory}")

        organized: dict[str, list[str]] = defaultdict(list)

        for entry in sorted(root.iterdir()):
            if not entry.is_file():
                continue

            try:
                mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            except OSError as exc:
                logger.warning("无法获取文件修改时间 %s: %s", entry, exc)
                continue

            date_key = mtime.strftime("%Y-%m")
            dest_dir = root / date_key
            dest_path = dest_dir / entry.name

            if _move_file(entry, dest_path, dry_run=dry_run):
                organized[date_key].append(entry.name)
            else:
                logger.warning("整理失败，跳过: %s", entry)

        return dict(organized)

    # ------------------------------------------------------------------
    # find_duplicates
    # ------------------------------------------------------------------

    @staticmethod
    def find_duplicates(directory: str) -> list[list[str]]:
        """
        基于 MD5 哈希查找重复文件。

        算法：
          1. 先按文件大小分组（大小不同的一定不重复）
          2. 对同一大小组的文件计算 MD5
          3. 同一 MD5 且 >= 2 个的文件即为重复

        Args:
            directory: 搜索根目录（递归）

        Returns:
            重复文件组列表，每组包含 2 个以上文件路径
            例如：[["a.xlsx", "b/copy.xlsx"], ["1.docx", "backup/1.docx"]]
        """
        root = _resolve(directory)
        if not root.is_dir():
            raise NotADirectoryError(f"目录不存在或不是目录: {directory}")

        # --- 第 1 步：按文件大小分组 ---
        size_groups: dict[int, list[Path]] = defaultdict(list)

        for entry in root.rglob("*"):
            if entry.is_file():
                try:
                    fsize = entry.stat().st_size
                except OSError as exc:
                    logger.warning("无法获取文件大小 %s: %s", entry, exc)
                    continue
                size_groups[fsize].append(entry)

        # --- 第 2 步：对每组 >= 2 的大小组计算 MD5 ---
        md5_groups: dict[str, list[Path]] = defaultdict(list)

        for size, files in size_groups.items():
            if len(files) < 2:
                continue
            for f in files:
                md5 = _compute_md5(f)
                if md5:  # 空字符串表示读取失败
                    md5_groups[md5].append(f)

        # --- 第 3 步：收集重复组（>= 2）---
        duplicates: list[list[str]] = []
        for md5, files in md5_groups.items():
            if len(files) >= 2:
                duplicates.append([str(f) for f in sorted(files)])

        return duplicates

    # ------------------------------------------------------------------
    # get_dir_size
    # ------------------------------------------------------------------

    @staticmethod
    def get_dir_size(directory: str) -> int:
        """
        计算目录总大小（bytes）。

        遍历目录树累加所有文件大小。符号链接不跟踪。

        Args:
            directory: 目标目录

        Returns:
            总字节数
        """
        root = _resolve(directory)
        if not root.is_dir():
            raise NotADirectoryError(f"目录不存在或不是目录: {directory}")

        total = 0
        for entry in root.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError as exc:
                    logger.warning("无法获取文件大小 %s: %s", entry, exc)
        return total

    # ------------------------------------------------------------------
    # get_disk_usage
    # ------------------------------------------------------------------

    @staticmethod
    def get_disk_usage(path: str = ".") -> dict[str, int]:
        """
        返回磁盘使用情况。

        Args:
            path: 任意路径（用于确定磁盘分区）

        Returns:
            {"total": int, "used": int, "free": int}  单位为 bytes
        """
        target = _resolve(path)
        # 使用 shutil.disk_usage，返回 (total, used, free)
        usage = shutil.disk_usage(str(target))
        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        }

    # ------------------------------------------------------------------
    # ensure_dir
    # ------------------------------------------------------------------

    @staticmethod
    def ensure_dir(path: str) -> None:
        """
        确保目录存在，不存在则创建（含所有父目录）。

        Args:
            path: 目录路径
        """
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        logger.info("目录已就绪: %s", p)
