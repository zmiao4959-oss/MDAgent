"""Project CRUD, reporting, and lifecycle routes."""
from __future__ import annotations

import uuid
from typing import Any
from fastapi import Request


def register_project_routes(app: Any, adapter: Any) -> None:
    @app.get("/api/projects")
    async def list_projects(include_archived: bool = False):
        await adapter._ensure_projects()
        projects = adapter.projects.list(include_archived=include_archived)
        return {
            "projects": [
                await adapter._project_payload(project) for project in projects
            ]
        }

    @app.post("/api/projects")
    async def create_project(request: Request):
        if adapter.session_manager is None:
            return {"ok": False, "error": "session_manager not available"}
        body = await request.json()
        title = (body.get("title") or "").strip()
        objective = (body.get("objective") or "").strip()
        if not title:
            return {"ok": False, "error": "title is required"}
        chat_id = f"webchat:{uuid.uuid4().hex[:10]}"
        session = adapter.session_manager.get_or_create(
            chat_id, "webchat", "local"
        )
        session.metadata["alias"] = title
        await adapter.session_manager.save(session)
        project = adapter.projects.create(title, objective, chat_id)
        adapter._create_project_workspace(project)
        return {"ok": True, "project": await adapter._project_payload(project)}

    @app.get("/api/projects/{project_id}/artifacts")
    async def project_artifacts(project_id: str):
        project = adapter.projects.get(project_id)
        if project is None:
            return {"ok": False, "error": "project not found"}
        return {
            "ok": True,
            "workspace_dir": str(adapter._workspace_for_project(project)),
            "artifacts": adapter._project_artifacts(project),
        }

    @app.get("/api/projects/{project_id}/summary")
    async def project_summary(project_id: str):
        project = adapter.projects.get(project_id)
        if project is None:
            return {"ok": False, "error": "project not found"}
        artifacts = adapter._project_artifacts(project)
        return {
            "ok": True,
            "project": await adapter._project_payload(project),
            "artifact_count": len(artifacts),
            "latest_artifact": artifacts[0] if artifacts else None,
        }

    @app.get("/api/projects/{project_id}/timeline")
    async def project_timeline(project_id: str):
        project = adapter.projects.get(project_id)
        if project is None:
            return {"ok": False, "error": "project not found"}
        return {"ok": True, "events": list(reversed(project.timeline or []))}

    @app.patch("/api/projects/{project_id}")
    async def update_project(project_id: str, request: Request):
        body = await request.json()
        project = adapter.projects.get(project_id)
        if project is None:
            return {"ok": False, "error": "project not found"}
        title = body.get("title") if "title" in body else None
        objective = body.get("objective") if "objective" in body else None
        try:
            project = adapter.projects.update(
                project_id, title=title, objective=objective
            )
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        if project is None:
            return {"ok": False, "error": "project not found"}
        if title and adapter.session_manager is not None:
            session = await adapter.session_manager.resolve_session(project.chat_id)
            if session is not None:
                session.metadata["alias"] = project.title
                await adapter.session_manager.save(session)
        return {"ok": True, "project": await adapter._project_payload(project)}

    @app.post("/api/projects/{project_id}/action")
    async def project_action(project_id: str, request: Request):
        action = (await request.json()).get("action")
        project = adapter.projects.get(project_id)
        if project is None:
            return {"ok": False, "error": "project not found"}
        task = adapter._project_runs.get(project_id)
        running = bool(task and not task.done())
        if action == "pause":
            project = adapter.projects.update(project_id, status="paused")
            adapter._project_event(project, "paused", "项目已暂停")
            if running:
                task.cancel()
        elif action == "resume":
            if project.status != "paused":
                return {"ok": False, "error": "only paused projects can resume"}
            project = adapter.projects.update(project_id, status="active")
            adapter._project_event(project, "resumed", "项目已恢复，可继续发送任务")
        elif action == "complete":
            if running:
                return {
                    "ok": False,
                    "error": "pause the running project before completing it",
                }
            project = adapter.projects.update(project_id, status="completed")
        elif action == "archive":
            if running:
                return {
                    "ok": False,
                    "error": "pause the running project before archiving it",
                }
            project = adapter.projects.update(project_id, status="archived")
        else:
            return {"ok": False, "error": "unsupported project action"}
        return {"ok": True, "project": await adapter._project_payload(project)}
