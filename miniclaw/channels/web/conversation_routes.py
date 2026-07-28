"""Conversation management, history, and export routes."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from fastapi.responses import PlainTextResponse


def register_conversation_routes(app: Any, adapter: Any) -> None:
    conversations = adapter.conversations

    @app.get("/api/conversations")
    async def list_conversations():
        return {"conversations": await conversations.list()}

    @app.post("/api/conversations")
    async def new_conversation():
        return {"chat_id": await conversations.create()}

    @app.post("/api/conversations/{chat_id}/fork")
    async def fork_conversation(chat_id: str, request: Request):
        body = await _json_body(request)
        try:
            at_index = int(body.get("at_index", -1))
        except (TypeError, ValueError):
            at_index = -1
        return await conversations.fork(chat_id, at_index)

    @app.patch("/api/conversations/{chat_id}")
    async def rename_conversation(chat_id: str, request: Request):
        body = await _json_body(request)
        return await conversations.rename(chat_id, body.get("alias") or "")

    @app.delete("/api/conversations/{chat_id}")
    async def delete_conversation(chat_id: str):
        return await conversations.delete(chat_id)

    @app.get("/api/history")
    async def history(chat_id: str):
        return await conversations.history(chat_id)

    @app.get("/api/export")
    async def export_conversation(chat_id: str, fmt: str = "markdown"):
        if fmt == "json":
            return await conversations.export_json(chat_id)
        result = await conversations.export_markdown(chat_id)
        if isinstance(result, dict):
            return result
        return PlainTextResponse(
            result,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": (
                    "attachment; "
                    f"filename=miniclaw-{chat_id.replace(':', '-')}.md"
                )
            },
        )


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}
