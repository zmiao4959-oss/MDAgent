"""Public-channel communication using the standard tool registry."""
from __future__ import annotations

import inspect
import json

from .registry import tool_registry


@tool_registry.register(
    name="publish_message",
    schema={
        "type": "function",
        "function": {
            "name": "publish_message",
            "description": (
                "Publish new evidence, a question, a proposal, or a reply to the other room "
                "members. The room routes it to each member's current turn or inbox as "
                "appropriate. Publish directly. Use your ordinary final response for private notes."
            ),
            "parameters": {
                "type": "object", "additionalProperties": False, "required": ["message"],
                "properties": {"message": {"type": "string", "minLength": 1, "maxLength": 32_000}},
            },
        },
    },
    tags=["communication", "coordination"],
)
async def publish_message_tool(message: str, _context=None, **kwargs) -> str:
    context = _context or {}
    publish = context.get("room_publish")
    if context.get("channel") != "agent_room" or not callable(publish):
        return "Error: public-channel publishing is unavailable outside a room turn"
    if not isinstance(message, str) or not message.strip() or len(message) > 32_000:
        return "Error: message must contain 1–32000 characters and cannot be blank"
    result = publish(message)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, str) else json.dumps(result or {"status": "published"}, ensure_ascii=False)


@tool_registry.register(
    name="finish_discussion",
    schema={
        "type": "function",
        "function": {
            "name": "finish_discussion",
            "description": (
                "Publish the coordinator's final public summary and end this discussion. "
                "Use complete for a plan ready to present and stalled for unresolved discussion."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["summary", "reason"],
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 32_000},
                    "reason": {"type": "string", "enum": ["complete", "stalled"]},
                },
            },
        },
    },
    tags=["communication", "coordination"],
)
async def finish_discussion_tool(summary: str, reason: str, _context=None, **kwargs) -> str:
    context = _context or {}
    finish = context.get("room_finish")
    if context.get("channel") != "agent_room" or not callable(finish):
        return "Error: only the active room coordinator can finish the discussion"
    result = await finish(summary, reason)
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
