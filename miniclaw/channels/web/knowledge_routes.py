"""GPUMD knowledge management HTTP routes."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from ...settings import config


def register_knowledge_routes(app: Any, adapter: Any) -> None:
    @app.get("/api/knowledge/gpumd")
    async def get_gpumd_knowledge():
        return adapter.knowledge.payload()

    @app.post("/api/knowledge/gpumd/sync")
    async def sync_gpumd_knowledge():
        return _start_job(adapter, "sync")

    @app.post("/api/knowledge/gpumd/index")
    async def index_gpumd_knowledge():
        from ...rag.cli import default_corpus

        if not config.rag.enabled:
            raise HTTPException(status_code=400, detail="RAG 已在配置中关闭")
        if not default_corpus().is_file():
            raise HTTPException(status_code=400, detail="请先同步 GPUMD 官方文档")
        if not config.rag.resolved_api_key:
            raise HTTPException(
                status_code=400,
                detail="尚未配置 embedding API 密钥",
            )
        return _start_job(adapter, "index")


def _start_job(adapter: Any, action: str) -> Dict[str, Any]:
    try:
        adapter.knowledge.start(action)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "job": adapter.knowledge.state}
