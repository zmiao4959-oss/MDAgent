"""Coordinator tools for discovering, messaging, and controlling delegated tasks."""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from .registry import tool_registry

if TYPE_CHECKING:
    from ..subagent import SubAgentManager

_manager: Optional["SubAgentManager"] = None
_TASK_ID_DESCRIPTION = (
    "Opaque task ID returned by delegate_task. Copy it verbatim, including "
    "the 'subagent:' prefix. Never shorten, parse, or reconstruct it."
)


def register_subagent_manager(manager: "SubAgentManager") -> None:
    global _manager
    _manager = manager


def _require_manager() -> "SubAgentManager":
    if _manager is None:
        raise RuntimeError("Sub-agent manager is not configured")
    return _manager


@tool_registry.register(
    name="list_agents",
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
    name="list_tasks",
    schema={
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": (
                "List delegated tasks for the current parent conversation only. "
                "Use this after an exact task lookup fails; reconcile by task_id, "
                "idempotency_key, Agent, or objective before considering a rerun."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Optional case-insensitive substring matched against "
                            "task_id, idempotency_key, Agent name, and objective."
                        ),
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "running", "done", "error"],
                        "description": "Optional exact status filter.",
                    },
                },
            },
        },
    },
    tags=["coordination"],
)
def list_tasks_tool(
    query: str = "",
    status: str = "",
    _context=None,
    **kwargs: Any,
) -> dict:
    context = _context or {}
    parent_session_id = context.get("chat_id")
    if not parent_session_id:
        return {
            "status": "error",
            "error": "Current parent conversation is unavailable.",
            "tasks": [],
        }

    tasks = _require_manager().list_for_parent(parent_session_id)
    normalized_query = query.strip().lower()
    if status:
        tasks = [task for task in tasks if task.status == status]
    if normalized_query:
        tasks = [
            task
            for task in tasks
            if normalized_query
            in " ".join(
                (
                    task.task_id,
                    task.idempotency_key or "",
                    task.agent_name,
                    task.task_prompt,
                )
            ).lower()
        ]
    return {
        "status": "ok",
        "count": len(tasks),
        "tasks": [
            {
                "task_id": task.task_id,
                "idempotency_key": task.idempotency_key,
                "agent": task.agent_name,
                "objective": task.task_prompt,
                "status": task.status,
                "has_result": task.result is not None,
            }
            for task in tasks
        ],
    }


@tool_registry.register(
    name="delegate_task",
    schema={
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": (
                "Use agent and objective to open a task or consultation. Use task_id and "
                "message to send a question, confirmation, or update to an existing task in this conversation. "
                "The receiver reads it at the next model decision or continues the same "
                "conversation in a follow-up turn. Send directly and use inspect_task or "
                "wait_task when you need the reply. Repeated equivalent delegations reuse "
                "the original task unless force_new is true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": "For a new task, the exact Agent profile name from list_agents.",
                    },
                    "objective": {
                        "type": "string",
                        "description": "For a new task, the objective or question with context and constraints.",
                    },
                    "task_id": {
                        "type": "string",
                        "description": _TASK_ID_DESCRIPTION,
                    },
                    "message": {
                        "type": "string",
                        "description": "Question, confirmation, or update for an existing task_id.",
                    },
                    "sandbox": {
                        "type": "boolean",
                        "description": "For a new task, use an isolated temporary workspace. Default false.",
                    },
                    "idempotency_key": {
                        "type": "string",
                        "description": (
                            "Optional stable key for retrying this delegation or message. "
                            "Use a distinct key for each new message to the same task."
                        ),
                    },
                    "force_new": {
                        "type": "boolean",
                        "description": (
                            "Create a new task even when an equivalent delegation exists. "
                            "Use only for an explicit rerun. Default false."
                        ),
                    },
                },
            },
        },
    },
    tags=["coordination"],
)
async def delegate_task_tool(
    agent: str = "",
    objective: str = "",
    sandbox: bool = False,
    idempotency_key: str = "",
    force_new: bool = False,
    _context=None,
    *,
    task_id: Optional[str] = None,
    message: Optional[str] = None,
    **kwargs: Any,
) -> dict:
    context = _context or {}
    if task_id is not None or message is not None:
        if agent or objective or sandbox or force_new:
            return {
                "status": "error",
                "error": "Use task_id and message for an existing task, or agent and objective for a new task.",
            }
        if not context.get("chat_id"):
            return {"status": "error", "error": "Current parent conversation is unavailable"}
        if not isinstance(idempotency_key, str):
            return {"status": "error", "error": "idempotency_key must be a string"}
        try:
            return await _require_manager().send_task_message(
                task_id, message, parent_session_id=context["chat_id"],
                event_callback=context.get("task_event_callback"),
                idempotency_key=idempotency_key,
            )
        except (ValueError, RuntimeError) as exc:
            return {"status": "error", "task_id": task_id, "error": str(exc)}

    if not isinstance(agent, str) or not agent.strip() or not isinstance(objective, str) or not objective.strip():
        return {
            "status": "error",
            "error": "Provide agent and objective for a new task, or task_id and message for an existing task.",
        }
    return await _require_manager().delegate(
        objective,
        parent_session_id=context.get("chat_id"),
        agent_name=agent,
        sandbox=bool(sandbox),
        workspace_dir=context.get("workspace_dir") or None,
        event_callback=context.get("task_event_callback"),
        idempotency_key=idempotency_key or None,
        force_new=bool(force_new),
    )


@tool_registry.register(
    name="continue_task",
    schema={
        "type": "function",
        "function": {
            "name": "continue_task",
            "description": (
                "Start a follow-up turn on a finished task, keeping its history and workspace. "
                "To send a message to an existing task at any stage, use delegate_task "
                "with task_id and message. Use the same task_id to retrieve the reply."
            ),
            "parameters": {
                "type": "object",
                "required": ["task_id", "instruction"],
                "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string", "description": _TASK_ID_DESCRIPTION},
                    "instruction": {"type": "string", "description": "Follow-up work to append to the original conversation."},
                },
            },
        },
    },
    tags=["coordination"],
)
async def continue_task_tool(task_id: str, instruction: str, _context=None, **kwargs: Any) -> dict:
    context = _context or {}
    if not context.get("chat_id"):
        return {"status": "error", "task_id": task_id, "error": "Current parent conversation is unavailable"}
    try:
        return await _require_manager().continue_task(
            task_id, instruction, parent_session_id=context["chat_id"],
            event_callback=context.get("task_event_callback"),
        )
    except (ValueError, RuntimeError) as exc:
        return {"status": "error", "task_id": task_id, "error": str(exc)}


@tool_registry.register(
    name="inspect_task",
    schema={
        "type": "function",
        "function": {
            "name": "inspect_task",
            "description": "Inspect a delegated task without waiting for it.",
            "parameters": {
                "type": "object",
                "required": ["task_id"],
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": _TASK_ID_DESCRIPTION,
                    }
                },
            },
        },
    },
    tags=["coordination"],
)
def inspect_task_tool(task_id: str, **kwargs: Any) -> dict:
    task = _require_manager().get(task_id)
    if task is None:
        return {
            "status": "not_found",
            "task_id": task_id,
            "instruction": (
                "Do not delegate again. Retry once with the exact task_id from the "
                "original receipt, then use list_tasks to reconcile this conversation."
            ),
        }
    return task.to_dict()


@tool_registry.register(
    name="wait_task",
    schema={
        "type": "function",
        "function": {
            "name": "wait_task",
            "description": "Wait for a delegated task to finish, up to the supplied timeout.",
            "parameters": {
                "type": "object",
                "required": ["task_id"],
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": _TASK_ID_DESCRIPTION,
                    },
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
    return result if result is not None else (
        f"Error: Unknown task '{task_id}'. Do not delegate again; reuse the exact "
        "task_id from the original receipt, then use list_tasks to reconcile."
    )


@tool_registry.register(
    name="cancel_task",
    schema={
        "type": "function",
        "function": {
            "name": "cancel_task",
            "description": "Cancel a delegated task and clean up its sandbox.",
            "parameters": {
                "type": "object",
                "required": ["task_id"],
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": _TASK_ID_DESCRIPTION,
                    }
                },
            },
        },
    },
    tags=["coordination"],
)
async def cancel_task_tool(task_id: str, **kwargs: Any) -> str:
    if _require_manager().get(task_id) is None:
        return (
            f"Error: Unknown task '{task_id}'. Do not delegate again; reuse the exact "
            "task_id from the original receipt, then use list_tasks to reconcile."
        )
    await _require_manager().kill(task_id)
    return f"Cancelled {task_id}"
