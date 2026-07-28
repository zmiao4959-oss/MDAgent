"""Project-scoped workspace file and asset routes."""
from __future__ import annotations

from typing import Any

from ...settings import workspace_scope
from .files import get_workspace_asset, list_workspace_files


def register_file_routes(app: Any, adapter: Any) -> None:
    @app.get("/api/files")
    async def list_files(work_dir: str = "", project_id: str = ""):
        project, error = _resolve_project(adapter, project_id)
        if error:
            return error
        workspace = adapter.project_workspaces.resolve(project) if project else None
        with workspace_scope(workspace):
            return list_workspace_files(work_dir)

    @app.get("/api/asset")
    async def get_asset(path: str, project_id: str = ""):
        project, error = _resolve_project(adapter, project_id)
        if error:
            return error
        workspace = adapter.project_workspaces.resolve(project) if project else None
        with workspace_scope(workspace):
            return get_workspace_asset(path)


def _resolve_project(adapter: Any, project_id: str):
    if not project_id:
        return None, None
    project = adapter.projects.get(project_id)
    if project is None:
        return None, {"ok": False, "error": "project not found"}
    return project, None
