"""GPUMD knowledge status and background maintenance service."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ...logger import get_logger
from ...settings import config

logger = get_logger(__name__)


def gpumd_knowledge_snapshot(
    corpus_path: Path,
    index_path: Path,
) -> Dict[str, Any]:
    """Return a small, secret-free summary for the WebChat settings panel."""
    corpus_count, corpus_error = _count_corpus(corpus_path)
    index = _read_index_metadata(index_path)

    if not corpus_path.is_file():
        state = "missing_corpus"
    elif not index_path.is_file():
        state = "missing_index"
    elif corpus_error or index["error"]:
        state = "error"
    elif index["model"] != config.rag.model:
        state = "model_mismatch"
    elif corpus_count != index["count"]:
        state = "outdated"
    else:
        state = "ready"

    return {
        "state": state,
        "enabled": config.rag.enabled,
        "api_key_configured": bool(config.rag.resolved_api_key),
        "model": config.rag.model,
        "api_type": config.rag.api_type,
        "corpus": {
            "exists": corpus_path.is_file(),
            "count": corpus_count,
            "modified_at": _modified_at(corpus_path),
            "error": corpus_error,
        },
        "index": {
            "exists": index_path.is_file(),
            "count": index["count"],
            "model": index["model"],
            "schema_version": index["schema_version"],
            "modified_at": _modified_at(index_path),
            "error": index["error"],
        },
    }


def _count_corpus(path: Path) -> tuple[int, str]:
    if not path.is_file():
        return 0, ""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip()), ""
    except OSError as exc:
        return 0, str(exc)


def _read_index_metadata(path: Path) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "count": 0,
        "model": "",
        "schema_version": 0,
        "error": "",
    }
    if not path.is_file():
        return metadata
    try:
        with path.open("r", encoding="utf-8") as handle:
            prefix = handle.read(16384)
        schema_match = re.search(r'"schema_version"\s*:\s*(\d+)', prefix)
        model_match = re.search(r'"model"\s*:\s*("(?:\\.|[^"\\])*")', prefix)
        count_match = re.search(r'"count"\s*:\s*(\d+)', prefix)
        if not (schema_match and model_match and count_match):
            raise ValueError("无法读取索引元数据")
        metadata.update(
            schema_version=int(schema_match.group(1)),
            model=str(json.loads(model_match.group(1))),
            count=int(count_match.group(1)),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        metadata["error"] = str(exc)
    return metadata


def _modified_at(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(
            timespec="seconds"
        )
    except OSError:
        return ""


class KnowledgeService:
    """Own the lifecycle and status of GPUMD corpus/index jobs."""

    def __init__(self) -> None:
        self.task: Optional[asyncio.Task[Any]] = None
        self.state: Dict[str, Any] = {
            "state": "idle",
            "action": "",
            "message": "",
            "result": None,
            "updated_at": "",
        }

    def payload(self) -> Dict[str, Any]:
        from ...rag.cli import default_corpus, default_index

        payload = gpumd_knowledge_snapshot(default_corpus(), default_index())
        job = dict(self.state)
        job["running"] = bool(self.task and not self.task.done())
        payload["job"] = job
        return payload

    def start(self, action: str) -> None:
        if action not in {"sync", "index"}:
            raise ValueError("不支持的知识库任务")
        if self.task and not self.task.done():
            raise RuntimeError("已有知识库任务正在运行")
        labels = {"sync": "同步官方文档", "index": "增量更新向量"}
        self.state = {
            "state": "running",
            "action": action,
            "message": f"正在{labels[action]}…",
            "result": None,
            "updated_at": _now(),
        }
        self.task = asyncio.create_task(self._run(action))

    async def stop(self) -> None:
        if self.task is None or self.task.done():
            return
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def _run(self, action: str) -> None:
        from ...rag.cli import build_index, default_corpus, default_index
        from ...rag.sync_gpumd import sync

        try:
            if action == "sync":
                result, message = await self._sync(sync, default_corpus())
            else:
                result, message = await self._index(
                    build_index,
                    default_corpus(),
                    default_index(),
                )
            self.state.update(
                state="completed",
                message=message,
                result=result,
            )
        except asyncio.CancelledError:
            self.state.update(state="cancelled", message="知识库任务已停止")
            raise
        except Exception as exc:
            logger.exception("GPUMD knowledge job failed: %s", action)
            self.state.update(state="failed", message=str(exc), result=None)
        finally:
            self.state["updated_at"] = _now()

    @staticmethod
    async def _sync(sync, corpus_path: Path) -> tuple[Dict[str, Any], str]:
        pages, manual_chunks, tutorial_chunks = await asyncio.to_thread(
            sync,
            corpus_path,
        )
        result = {
            "pages": pages,
            "manual_chunks": manual_chunks,
            "tutorial_chunks": tutorial_chunks,
            "total": manual_chunks + tutorial_chunks,
        }
        return result, f"文档同步完成，共 {result['total']} 个文档块"

    @staticmethod
    async def _index(
        build_index,
        corpus_path: Path,
        index_path: Path,
    ) -> tuple[Dict[str, Any], str]:
        stats = await asyncio.to_thread(build_index, corpus_path, index_path)
        result = {
            "total": stats.total,
            "reused": stats.reused,
            "embedded": stats.embedded,
            "removed": stats.removed,
            "full_rebuild": stats.full_rebuild,
        }
        message = (
            f"向量更新完成：复用 {stats.reused}，新增或更新 "
            f"{stats.embedded}，删除失效 {stats.removed}"
        )
        return result, message


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
