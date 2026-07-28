"""Coordinator tools for discovering and controlling delegated Agent tasks."""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from .registry import tool_registry

if TYPE_CHECKING:
    from ..subagent import SubAgentManager

_manager: Optional["SubAgentManager"] = None


def register_subagent_manager(manager: "SubAgentManager") -> None:
    global _manager
    _manager = manager


def _require_manager() -> "SubAgentManager":
    if _manager is None:
        raise RuntimeError("Sub-agent manager is not configured")
    return _manager


@tool_registry.register(
    name="list_agents",
    description="List available Agent profiles and their capabilities.",
    schema={
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "List available Agent profiles that can receive delegated work.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    tags=["coordination"],
)
def list_agents_tool(**kwargs: Any) -> str:
    manager = _require_manager()
    manager.catalog.refresh()
    profiles = manager.catalog.list_all()
    lines = []
    for profile in profiles:
        if profile.name == "coordinator":
            continue
        lines.append(f"- {profile.name}: {profile.description}")
    return "\n".join(lines) if lines else "No sub-agent profiles are available."


@tool_registry.register(
    name="delegate_task",
    description="Start a task using a selected Agent profile.",
    schema={
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": (
                "Delegate a concrete objective to an Agent profile. Independent tasks "
                "may be delegated before waiting for any one of them."
            ),
            "parameters": {
                "type": "object",
                "required": ["agent", "objective"],
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": "Exact Agent profile name from list_agents.",
                    },
                    "objective": {
                        "type": "string",
                        "description": "Concrete objective, context, constraints, and expected output.",
                    },
                    "sandbox": {
                        "type": "boolean",
                        "description": "Run in an isolated temporary workspace. Default false.",
                    },
                },
            },
        },
    },
    tags=["coordination"],
)
async def delegate_task_tool(
    agent: str,
    objective: str,
    sandbox: bool = False,
    _context=None,
    **kwargs: Any,
) -> str:
    context = _context or {}
    return await _require_manager().spawn(
        objective,
        parent_session_id=context.get("chat_id"),
        agent_name=agent,
        sandbox=bool(sandbox),
        event_callback=context.get("task_event_callback"),
    )


@tool_registry.register(
    name="inspect_task",
    description="Inspect the current state and result of a delegated task.",
    schema={
        "type": "function",
        "function": {
            "name": "inspect_task",
            "description": "Inspect a delegated task without waiting for it.",
            "parameters": {
                "type": "object",
                "required": ["task_id"],
                "properties": {"task_id": {"type": "string"}},
            },
        },
    },
    tags=["coordination"],
)
def inspect_task_tool(task_id: str, **kwargs: Any) -> dict:
    task = _require_manager().get(task_id)
    if task is None:
        return {"status": "not_found", "task_id": task_id}
    return task.to_dict()


@tool_registry.register(
    name="wait_task",
    description="Wait for a delegated task and return its result.",
    schema={
        "type": "function",
        "function": {
            "name": "wait_task",
            "description": "Wait for a delegated task to finish, up to the supplied timeout.",
            "parameters": {
                "type": "object",
                "required": ["task_id"],
                "properties": {
                    "task_id": {"type": "string"},
                    "timeout": {
                        "type": "number",
                        "description": "Maximum seconds to wait. Default 300.",
                    },
                },
            },
        },
    },
    tags=["coordination"],
)
async def wait_task_tool(
    task_id: str,
    timeout: float = 300,
    **kwargs: Any,
) -> str:
    result = await _require_manager().wait(task_id, timeout=float(timeout))
    return result if result is not None else f"Error: Unknown task '{task_id}'"


@tool_registry.register(
    name="cancel_task",
    description="Cancel a delegated task.",
    schema={
        "type": "function",
        "function": {
            "name": "cancel_task",
            "description": "Cancel a delegated task and clean up its sandbox.",
            "parameters": {
                "type": "object",
                "required": ["task_id"],
                "properties": {"task_id": {"type": "string"}},
            },
        },
    },
    tags=["coordination"],
)
async def cancel_task_tool(task_id: str, **kwargs: Any) -> str:
    if _require_manager().get(task_id) is None:
        return f"Error: Unknown task '{task_id}'"
    await _require_manager().kill(task_id)
    return f"Cancelled {task_id}"
