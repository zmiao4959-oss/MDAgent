"""
tools/paths.py — 工作区内路径解析与访问控制（read/write/list 共用）
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from ..config import WORKSPACE_DIR

# 单文件读写上限（字节）
MAX_READ_BYTES = 512 * 1024
MAX_WRITE_BYTES = 2 * 1024 * 1024
MAX_LIST_ENTRIES = 500

# 默认 read 行数上限（与 file_tools 一致）
DEFAULT_READ_LIMIT = 2000
MAX_READ_LIMIT = 5000

_TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".css", ".scss", ".sql", ".sh",
    ".bat", ".ps1", ".csv", ".log", ".env", ".lmp", ".in", ".data",
})

_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})


def workspace_root() -> Path:
    return WORKSPACE_DIR.resolve()


def resolve_workspace_path(
    path: str,
    *,
    must_exist: bool = False,
    allow_create: bool = False,
) -> Tuple[Optional[Path], Optional[str]]:
    """
    将相对/绝对路径解析到 WORKSPACE_DIR 下。
    返回 (resolved_path, error_message)。
    """
    if not path or not str(path).strip():
        return None, "Error: path is empty"

    raw = Path(path.strip())
    root = workspace_root()

    try:
        if raw.is_absolute():
            resolved = raw.resolve()
        else:
            resolved = (root / raw).resolve()
        resolved.relative_to(root)
    except ValueError:
        return None, (
            f"Error: path must stay inside workspace ({root}). Got: {path}"
        )
    except OSError as e:
        return None, f"Error: invalid path '{path}': {e}"

    if must_exist and not resolved.exists():
        return None, f"Error: path not found: {path}"

    if not allow_create and not must_exist:
        # 写文件时父目录可以不存在，由 write 创建；读/列目录必须存在或明确 allow_create
        pass

    return resolved, None


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return True
    if path.suffix.lower() in _IMAGE_SUFFIXES:
        return False
    # 无后缀或未知后缀：尝试按文本读
    return path.suffix == "" or len(path.suffix) <= 5


def is_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIXES


def truncate_text_result(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    omitted = len(text) - head - tail
    return (
        f"{text[:head].rstrip()}\n\n"
        f"... [{omitted} characters omitted] ...\n\n"
        f"{text[-tail:].lstrip()}"
    )
