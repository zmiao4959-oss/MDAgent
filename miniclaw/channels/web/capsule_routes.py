"""Research capsule HTTP routes kept separate from the WebChat transport."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from ...config import WORKSPACE_DIR
from ...research_capsule import (
    CapsuleIntegrityError,
    ResearchCapsuleManager,
    latest_trace_for_chat,
)


def register_capsule_routes(app, adapter) -> None:
    def project_and_manager(project_id: str):
        project = adapter.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        workspace = adapter._workspace_for_project(project)
        if workspace == WORKSPACE_DIR.resolve():
            raise HTTPException(
                status_code=409,
                detail="research capsules require an isolated project workspace",
            )
        return project, workspace, ResearchCapsuleManager(workspace)

    @app.get("/api/projects/{project_id}/capsules")
    async def list_project_capsules(project_id: str):
        _project, _workspace, manager = project_and_manager(project_id)
        capsules = await asyncio.to_thread(manager.list_capsules)
        summaries = []
        for capsule in capsules:
            summary = capsule.to_summary()
            verification = await asyncio.to_thread(manager.get_verification, capsule.capsule_id)
            summary["verification"] = verification.to_dict() if verification else None
            summaries.append(summary)
        return {"ok": True, "capsules": summaries}

    @app.get("/api/projects/{project_id}/capsules/{capsule_id}")
    async def get_project_capsule(project_id: str, capsule_id: str):
        _project, _workspace, manager = project_and_manager(project_id)
        try:
            capsule = await asyncio.to_thread(manager.get_capsule, capsule_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if capsule is None:
            raise HTTPException(status_code=404, detail="capsule not found")
        return {"ok": True, "capsule": capsule.to_dict()}

    @app.post("/api/projects/{project_id}/capsules")
    async def create_project_capsule(project_id: str):
        project, workspace, manager = project_and_manager(project_id)
        task = adapter._project_runs.get(project_id)
        if task is not None and not task.done():
            raise HTTPException(status_code=409, detail="project is still running")

        session = None
        if adapter.session_manager is not None:
            session = await adapter.session_manager.resolve_session(project.chat_id)
        trace = await asyncio.to_thread(latest_trace_for_chat, workspace, project.chat_id)
        capsule = await asyncio.to_thread(
            manager.capture,
            project_id=project.project_id,
            title=project.title,
            objective=project.objective,
            chat_id=project.chat_id,
            session=session,
            trace=trace,
        )
        adapter.projects.add_event(
            project.project_id,
            "capsule_created",
            f"Research capsule {capsule.capsule_id} captured {capsule.file_count} files",
        )
        return {"ok": True, "capsule": capsule.to_dict()}

    @app.post("/api/projects/{project_id}/capsules/{capsule_id}/verify")
    async def verify_project_capsule(project_id: str, capsule_id: str):
        project, _workspace, manager = project_and_manager(project_id)
        task = adapter._project_runs.get(project_id)
        if task is not None and not task.done():
            raise HTTPException(status_code=409, detail="project is still running")
        try:
            report = await asyncio.to_thread(manager.verify, capsule_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="capsule not found") from exc
        adapter.projects.add_event(
            project.project_id,
            "capsule_verified",
            f"Research capsule {capsule_id} integrity status: {report.status}",
        )
        return {"ok": True, "verification": report.to_dict()}

    @app.post("/api/projects/{project_id}/capsules/{capsule_id}/export")
    async def export_project_capsule(project_id: str, capsule_id: str, request: Request):
        project, _workspace, manager = project_and_manager(project_id)
        task = adapter._project_runs.get(project_id)
        if task is not None and not task.done():
            raise HTTPException(status_code=409, detail="project is still running")
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        confirmation = str(payload.get("confirmation", "")) if isinstance(payload, dict) else ""
        include_files = bool(payload.get("include_files", True)) if isinstance(payload, dict) else True
        try:
            exported = await asyncio.to_thread(
                manager.export_capsule,
                capsule_id,
                confirmation=confirmation,
                include_files=include_files,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="capsule not found") from exc
        except CapsuleIntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": str(exc), "verification": exc.report.to_dict()},
            ) from exc
        adapter.projects.add_event(
            project.project_id,
            "capsule_exported",
            f"Research capsule {capsule_id} exported with {exported.file_count} files",
        )
        return FileResponse(
            exported.path,
            media_type="application/zip",
            filename=exported.filename,
            headers={
                "X-Capsule-File-Count": str(exported.file_count),
                "X-Capsule-Redacted-Count": str(exported.redacted_file_count),
            },
        )
