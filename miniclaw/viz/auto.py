"""工具执行后的自动可视化（webchat 渠道）"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from ..logger import get_logger
from ..memory.session import Session
from .constants import MEDIA_IMAGE_EXT, STRUCTURE_EXT
from .ovito_render import render_ovito_subprocess
from .snapshot import FileSnap, diff_snapshots

logger = get_logger(__name__)

VizEventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


def _viz_done_set(session: Session) -> Set[str]:
    raw = session.metadata.get("viz_done")
    if isinstance(raw, list):
        return set(str(x) for x in raw)
    return set()


def _mark_viz_done(session: Session, path: str) -> None:
    done = _viz_done_set(session)
    done.add(path)
    session.metadata["viz_done"] = sorted(done)


def _classify_new_file(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext in STRUCTURE_EXT:
        return "structure"
    if ext == ".csv":
        return "csv"
    if ext in MEDIA_IMAGE_EXT:
        return "media"
    return None


class AutoVisualizer:
    """检测工作区新文件并调度后台可视化。"""

    def __init__(self, session: Session, on_viz: Optional[VizEventCallback], pending: List[asyncio.Task]):
        self.session = session
        self.on_viz = on_viz
        self.pending = pending

    async def after_tool_round(self, before: FileSnap, after: FileSnap) -> None:
        if not self.on_viz:
            return

        done = _viz_done_set(self.session)
        dirty = False
        for path in diff_snapshots(before, after):
            kind = _classify_new_file(path)
            if not kind:
                continue
            key = str(path.resolve())

            # 文件被覆盖（同路径，mtime/size 变化）：允许重新推送
            if key in done and key in before and before[key] != after.get(key):
                done.discard(key)
                dirty = True

            if key in done:
                continue

            if kind == "structure":
                done.add(key)
                dirty = True
                task = asyncio.create_task(self._render_structure(path))
                self.pending.append(task)
            elif kind == "csv":
                done.add(key)
                dirty = True
                await self._emit_media("csv", key)
            elif kind == "media":
                done.add(key)
                dirty = True
                media_type = "gif" if path.suffix.lower() == ".gif" else "image"
                await self._emit_media(media_type, key)

        if dirty:
            self.session.metadata["viz_done"] = sorted(done)

    async def _emit(self, payload: Dict[str, Any]) -> None:
        if self.on_viz:
            await self.on_viz({"type": "viz", **payload})

    async def _emit_media(self, media_type: str, path: str) -> None:
        await self._emit({"event": "media", "media_type": media_type, "path": path})

    async def _render_structure(self, struct_path: Path) -> None:
        await self._emit({
            "event": "status",
            "text": f"OVITO 渲染中: {struct_path.name}",
            "path": str(struct_path),
        })
        gif: Optional[Path] = await asyncio.to_thread(render_ovito_subprocess, struct_path)
        if gif and gif.is_file():
            gif_key = str(gif.resolve())
            _mark_viz_done(self.session, gif_key)
            await self._emit_media("gif", gif_key)
        else:
            await self._emit({
                "event": "error",
                "text": f"OVITO 渲染失败: {struct_path.name}",
                "path": str(struct_path),
            })
