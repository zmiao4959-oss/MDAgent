"""HTTP route for streaming one WebChat Agent request."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

from ...agent import AgentContext
from ...logger import get_logger
from ...settings import config
from .chat_events import ChatEventStream
from .chat_run import ChatRunService

logger = get_logger(__name__)


def register_chat_routes(app: Any, adapter: Any) -> None:
    @app.post("/api/chat")
    async def chat(request: Request):
        body = await request.json()
        user_message = body.get("message", "")
        chat_id = body.get("chat_id") or "webchat:default"
        project_id = body.get("project_id") or ""
        plan_mode = bool(body.get("plan_mode", False))
        project: Optional[Any] = None
        if project_id:
            project = adapter.projects.get(project_id)
            if project is None:
                return {"ok": False, "error": "project not found"}
            if project.status in ("paused", "completed", "archived"):
                return {
                    "ok": False,
                    "error": (
                        f"project is {project.status}; "
                        "resume it before sending a message"
                    ),
                }
            chat_id = project.chat_id
            adapter.projects.update(project_id, status="queued", last_run=True)
            adapter._project_event(project, "queued", "任务已加入运行队列")
        timeout = float(body.get("timeout", config.agent.task_timeout_sec))

        async def event_stream():
            metadata: Dict[str, Any] = {}
            if project is not None:
                metadata.update(
                    workspace_dir=str(adapter._workspace_for_project(project)),
                    project_id=project.project_id,
                    project_title=project.title,
                    project_objective=project.objective,
                )
            if plan_mode:
                metadata["plan_mode"] = True
            context = AgentContext(
                chat_id=chat_id,
                channel="webchat",
                account_id="local",
                user_message=user_message,
                metadata=metadata,
            )
            events = ChatEventStream()
            runner = ChatRunService(
                adapter=adapter,
                context=context,
                project=project,
                timeout=timeout,
                events=events,
            )
            task = asyncio.create_task(runner.run())
            if project is not None:
                adapter._project_runs[project.project_id] = task
            try:
                while True:
                    try:
                        item = await events.next(
                            float(config.agent.sse_keepalive_sec)
                        )
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            events.unsubscribe()
                            if project is None:
                                task.cancel()
                                try:
                                    await task
                                except (asyncio.CancelledError, Exception):
                                    pass
                            return
                        continue
                    if item is None:
                        break
                    payload = ChatEventStream.sse_payload(item)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            finally:
                if project is None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
            done = {"done": True, "full": runner.result["response"] or ""}
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")
