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
            "description": "Publish new evidence, a question, or a proposal to all other members of your public room. Your ordinary final response is private. Do not publish acknowledgements or repeated information.",
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
