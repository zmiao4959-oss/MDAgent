"""工作区文件快照与 diff（用于检测工具执行后的新文件）"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set, Tuple

from ..config import WORKSPACE_DIR
from .constants import SKIP_DIR_NAMES

FileSnap = Dict[str, Tuple[int, int]]  # abs path -> (mtime_ns, size)


def _should_skip(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in SKIP_DIR_NAMES for part in rel.parts)


def snapshot_workspace(root: Path | None = None) -> FileSnap:
    root = (root or WORKSPACE_DIR).resolve()
    snap: FileSnap = {}
    if not root.is_dir():
        return snap
    for p in root.rglob("*"):
        if not p.is_file() or _should_skip(p, root):
            continue
        try:
            st = p.stat()
            snap[str(p.resolve())] = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
    return snap


def diff_snapshots(before: FileSnap, after: FileSnap) -> List[Path]:
    """返回新增或内容变更的文件（绝对路径）。"""
    changed: List[Path] = []
    for path, meta in after.items():
        if path not in before or before[path] != meta:
            changed.append(Path(path))
    return changed
