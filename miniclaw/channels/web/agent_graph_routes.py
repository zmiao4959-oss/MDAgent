"""Profile discovery and persisted communication-canvas configuration."""
from __future__ import annotations

import time

from fastapi import HTTPException, Request
from ...skills.project import ProjectSkillStore


def register_agent_graph_routes(app, adapter):
    def runtime():
        value = getattr(adapter, "agent_graph_runtime", None)
        if value is None or adapter.session_manager is None:
            raise HTTPException(503, "智能体画布运行时尚未连接")
        return value

    async def session_for(chat_id):
        runtime()
        session = await adapter.session_manager.resolve_session(chat_id)
        if session is None:
            raise HTTPException(404, "会话不存在")
        return session

    def project_for(chat_id):
        return next((p for p in adapter.projects.list(include_archived=True) if p.chat_id == chat_id), None)

    def check_idle(chat_id, project):
        task = adapter._project_runs.get(project.project_id) if project else None
        if runtime().is_running(chat_id) or (task is not None and not task.done()):
            raise HTTPException(409, "请先暂停当前任务，再修改智能体画布或临时 Skill")

    def skill_payload(project):
        if project is None:
            return {"bindings": {}, "requests": []}
        store = ProjectSkillStore(adapter._workspace_for_project(project), project.project_id)
        runner = runtime()
        runner.catalog.refresh()
        availability = {p.name: set(store.mounted(p)) for p in runner.catalog.list_all()}
        bindings = {
            agent: {name: {**{key: value[key] for key in ("name", "version", "digest", "request_id", "required_tools", "mounted_at")}, "available": name in availability.get(agent, set())}
                    for name, value in skills.items()}
            for agent, skills in store.list_bindings().items()
        }
        requests = [{key: value.get(key) for key in ("request_id", "requester", "objective", "status", "skill_name", "error")} for value in store.requests()[-20:]]
        return {"bindings": bindings, "requests": requests}

    @app.get("/api/agent-profiles")
    async def agent_profiles():
        runner = runtime()
        runner.catalog.refresh()
        return {
            "default_profile": runner.default_agent.profile.name if runner.default_agent.profile else "",
            "profiles": [
                {"name": p.name, "description": p.description, "skills": list(p.skills), "tools": list(p.tools)}
                for p in runner.catalog.list_all()
            ],
        }

    @app.get("/api/conversations/{chat_id}/agent-graph")
    async def get_graph(chat_id: str):
        session = await session_for(chat_id)
        record = session.metadata.get("last_graph_run")
        if record and record.get("status") == "running" and not runtime().is_running(chat_id):
            record["status"] = "interrupted"
            record["ended_at"] = time.time()
            record.setdefault("events", []).append({"run_id": record["run_id"], "kind": "interrupted", "time": record["ended_at"]})
            await adapter.session_manager.save(session)
        return {"graph": session.metadata.get("agent_graph"), "last_run": session.metadata.get("last_graph_run"), "project_skills": skill_payload(project_for(chat_id))}

    @app.put("/api/conversations/{chat_id}/agent-graph")
    async def save_graph(chat_id: str, request: Request):
        runner = runtime()
        session = await session_for(chat_id)
        project = project_for(chat_id)
        check_idle(chat_id, project)
        try:
            body = await request.json()
            if not isinstance(body, dict) or "graph" not in body:
                raise ValueError("请求需要包含 graph 字段；设为 null 可恢复默认模式")
            graph = runner.validate(body["graph"])
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        session.metadata["agent_graph"] = graph.to_dict() if graph else None
        await adapter.session_manager.save(session)
        return {"ok": True, "graph": session.metadata["agent_graph"]}

    @app.delete("/api/conversations/{chat_id}/project-skills/{agent}/{name}")
    async def revoke_project_skill(chat_id: str, agent: str, name: str):
        await session_for(chat_id)
        project = project_for(chat_id)
        if project is None:
            raise HTTPException(404, "会话没有对应项目")
        check_idle(chat_id, project)
        try:
            return ProjectSkillStore(adapter._workspace_for_project(project), project.project_id).revoke(agent, name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
