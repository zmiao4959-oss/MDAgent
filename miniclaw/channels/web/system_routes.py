"""WebChat configuration, diagnostics, and statistics routes."""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request

from ...settings import WORKSPACE_DIR
from ...viz.constants import SKIP_DIR_NAMES, VISUAL_EXT


def register_system_routes(app: Any, adapter: Any) -> None:
    @app.get("/api/config")
    async def get_config():
        return {
            "workspace_root": str(WORKSPACE_DIR.resolve()),
            "visual_ext": sorted(VISUAL_EXT),
            "skip_dir_names": sorted(SKIP_DIR_NAMES),
        }

    @app.get("/api/diagnostics")
    async def get_diagnostics():
        return await adapter._runtime_diagnostics()

    @app.get("/api/settings/task-behavior")
    async def get_task_behavior():
        return dict(adapter.project_runner.behavior)

    @app.put("/api/settings/task-behavior")
    async def update_task_behavior(request: Request):
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="设置格式无效") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="设置格式无效")
        settings = await adapter.project_runner.update_behavior(body)
        return {"ok": True, "settings": settings}

    @app.get("/api/stats")
    async def get_stats():
        from ...hooks import hook_system
        from ...stats import agent_stats

        return {
            "agent": agent_stats.summary(),
            "hooks": hook_system.list_hooks(),
        }
