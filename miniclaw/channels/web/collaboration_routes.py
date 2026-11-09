"""Conversation-scoped mode selection and public-channel records."""
from __future__ import annotations

import time

from fastapi import HTTPException, Request

from ...agents.room_runtime import RoomConfig
from ...agents.collaboration import MODES


def register_collaboration_routes(app, adapter):
    def runtime():
        value = getattr(adapter, "collaboration_runtime", None)
        if value is None or adapter.session_manager is None:
            raise HTTPException(503, "协作运行时尚未连接")
        return value

    async def session_for(chat_id):
        runtime()
        value = await adapter.session_manager.resolve_session(chat_id)
        if value is None:
            raise HTTPException(404, "会话不存在")
        return value

    def check_idle(chat_id):
        project = next((p for p in adapter.projects.list(include_archived=True) if p.chat_id == chat_id), None)
        task = adapter._project_runs.get(project.project_id) if project else None
        if runtime().is_running(chat_id) or (task is not None and not task.done()):
            raise HTTPException(409, "请先暂停当前任务，再切换协作模式")

    def payload(session):
        values = session.metadata
        mode = values.get("collaboration_mode", "graph" if values.get("agent_graph") else "default")
        room = values.get("agent_room")
        channel = None
        if room:
            try:
                key = RoomConfig.parse(room, runtime().catalog).fingerprint
                channel = values.get("room_channels", {}).get(key)
            except ValueError:
                pass
        return {"mode": mode, "room": room, "graph": values.get("agent_graph"),
                "last_room_run": values.get("last_room_run"), "channel": channel}

    @app.get("/api/conversations/{chat_id}/collaboration")
    async def get_collaboration(chat_id: str):
        session = await session_for(chat_id)
        record = session.metadata.get("last_room_run")
        if record and record.get("status") == "running" and not runtime().is_running(chat_id):
            record.update(status="interrupted", ended_at=time.time())
            record.setdefault("events", []).append({"kind": "interrupted", "time": time.time(), "run_id": record["run_id"]})
            await adapter.session_manager.save(session)
        return payload(session)

    @app.put("/api/conversations/{chat_id}/collaboration")
    async def save_collaboration(chat_id: str, request: Request):
        session = await session_for(chat_id)
        check_idle(chat_id)
        body = await request.json()
        if not isinstance(body, dict) or "mode" not in body:
            raise HTTPException(400, "请求需要包含 mode")
        if not isinstance(body["mode"], str) or body["mode"] not in MODES:
            raise HTTPException(400, "协作模式必须是 default、graph 或 room")
        values = dict(session.metadata)
        values["collaboration_mode"] = body["mode"]
        for external, internal in (("room", "agent_room"), ("graph", "agent_graph")):
            if external in body:
                values[internal] = body[external]
        try:
            snapshot = runtime().snapshot(values)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        session.metadata.update(snapshot)
        await adapter.session_manager.save(session)
        return {"ok": True, **payload(session)}
