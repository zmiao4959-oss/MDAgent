"""Project planning and task execution routes."""
from __future__ import annotations

from typing import Any
from fastapi import Request


def register_project_task_routes(app: Any, adapter: Any) -> None:
    @app.get("/api/projects/{project_id}/plan")
    async def project_plan(project_id: str):
        project = adapter.projects.get(project_id)
        if project is None:
            return {"ok": False, "error": "project not found"}
        return {
            "ok": True,
            "tasks": project.tasks,
            "summary": project.summary,
            "summary_updated_at": project.summary_updated_at,
        }

    @app.post("/api/projects/{project_id}/tasks")
    async def add_project_task(project_id: str, request: Request):
        body = await request.json()
        project = adapter.projects.add_task(project_id, body.get("title") or "")
        if project is None:
            return {"ok": False, "error": "project or task not found"}
        return {"ok": True, "tasks": project.tasks}

    @app.patch("/api/projects/{project_id}/tasks/{task_id}")
    async def update_project_task(project_id: str, task_id: str, request: Request):
        body = await request.json()
        project = adapter.projects.update_task(
            project_id,
            task_id,
            done=body.get("done"),
            title=body.get("title"),
        )
        if project is None:
            return {"ok": False, "error": "project or task not found"}
        adapter._refresh_project_summary(project, "")
        return {"ok": True, "tasks": project.tasks, "summary": project.summary}

    @app.post("/api/projects/{project_id}/run-next")
    async def run_next_task(project_id: str):
        project = adapter.projects.get(project_id)
        if project is None:
            return {"ok": False, "error": "project not found"}
        active = adapter._project_runs.get(project_id)
        if active and not active.done():
            return {"ok": False, "error": "project already has a running task"}
        task = next((item for item in project.tasks if not item.get("done")), None)
        if task is None:
            return {"ok": False, "error": "no unfinished task"}
        adapter._run_next_task(project, task)
        return {"ok": True, "task": task}
