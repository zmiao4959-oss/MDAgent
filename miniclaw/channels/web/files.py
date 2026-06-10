"""WebChat 文件浏览与静态资源 API 辅助"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ...tools.paths import resolve_workspace_path, workspace_root
from ...viz.constants import SKIP_DIR_NAMES, VISUAL_EXT


def is_visual_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VISUAL_EXT


def list_workspace_files(work_dir: str = "") -> Dict[str, Any]:
    resolved, err = resolve_workspace_path(work_dir or ".", must_exist=True)
    if err:
        raise HTTPException(status_code=404, detail=err.replace("Error: ", ""))
    base = resolved
    root = workspace_root()

    entries: List[Dict[str, Any]] = []
    for item in sorted(base.iterdir(), key=lambda p: (not p.is_file(), p.name.lower())):
        if item.is_dir() and item.name in SKIP_DIR_NAMES:
            continue
        if item.name.startswith(".") and item.is_dir():
            continue
        try:
            rel = item.relative_to(root).as_posix()
        except ValueError:
            continue
        entries.append({
            "name": item.name,
            "path": str(item.resolve()),
            "rel_path": rel,
            "is_dir": item.is_dir(),
            "ext": item.suffix.lower() if item.is_file() else "",
            "visual": is_visual_file(item),
        })

    parent_dir = None
    if base != root:
        parent_dir = str(base.parent.resolve())

    rel_path = "." if base == root else base.relative_to(root).as_posix()
    return {
        "work_dir": str(base),
        "rel_path": rel_path,
        "parent_dir": parent_dir,
        "workspace_root": str(root),
        "entries": entries,
    }


def get_workspace_asset(path: str) -> FileResponse:
    resolved, err = resolve_workspace_path(path, must_exist=True)
    if err:
        raise HTTPException(status_code=403 if "stay inside workspace" in err else 404, detail=err)
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="不是文件")
    return FileResponse(resolved)
