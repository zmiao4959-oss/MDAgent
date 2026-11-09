"""Request-local capabilities for the public channel's registered tool."""
from __future__ import annotations

from typing import Any

PUBLISH_TOOL = "publish_message"
PUBLISH_CONTEXT_KEY = "_room_publish"
MAX_PUBLIC_MESSAGE_CHARS = 32_000


def room_publisher(context: Any):
    value = context.metadata.get(PUBLISH_CONTEXT_KEY)
    return value if context.channel == "agent_room" and callable(value) else None


def blocks_room_tool(name: str, context: Any, registry: Any) -> bool:
    active = room_publisher(context) is not None
    if name == PUBLISH_TOOL:
        return not active
    tool = registry.get(name)
    return bool(active and tool and {"coordination", "communication"}.intersection(tool.tags))


def room_allowed_tools(profile: Any, context: Any, registry: Any):
    allowed = None if profile is None else list(profile.tools)
    if room_publisher(context) is not None and allowed is not None and "*" not in allowed:
        allowed = [name for name in allowed if not blocks_room_tool(name, context, registry)]
        allowed.append(PUBLISH_TOOL)
    return allowed
