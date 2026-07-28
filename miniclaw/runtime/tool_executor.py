"""Shared, profile-enforcing Tool execution lifecycle."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..settings import active_workspace_dir
from ..hooks import hook_system
from ..learning.trace import EvolutionTrace
from ..llm.base import LLMMessage
from ..logger import get_logger
from ..stats import RunStats
from ..tools.registry import ToolRegistry, tool_registry

logger = get_logger(__name__)


class ToolExecutor:
    """Execute calls with hooks, idempotency, progress, tracing, and policy."""

    def __init__(self, profile=None, *, registry: ToolRegistry = tool_registry):
        self.profile = profile
        self.registry = registry

    async def execute_one(self, tool_call: Dict, context: Any) -> LLMMessage:
        func_name = tool_call["function"]["name"]
        try:
            arguments = json.loads(tool_call["function"]["arguments"])
        except json.JSONDecodeError:
            return LLMMessage(
                role="tool",
                tool_call_id=tool_call["id"],
                content=f"Error: Invalid JSON arguments: {tool_call['function']['arguments']}",
            )

        idem_keys = context.metadata.setdefault("_tool_idem_keys", set())
        fingerprint = (
            func_name,
            json.dumps(arguments, sort_keys=True, default=str),
        )
        if fingerprint in idem_keys:
            logger.info("Skipping duplicate tool call: %s", func_name)
            return LLMMessage(
                role="tool",
                tool_call_id=tool_call["id"],
                content="[Skipped: duplicate tool call with identical arguments in this round]",
                name=func_name,
            )
        idem_keys.add(fingerprint)

        hook = await hook_system.fire(
            "before_tool",
            chat_id=context.chat_id,
            channel=context.channel,
            data={"tool_name": func_name, "arguments": arguments},
        )
        if hook.prevent:
            return LLMMessage(
                role="tool",
                tool_call_id=tool_call["id"],
                content=f"[Tool '{func_name}' blocked by hook: {hook.prevent_reason}]",
                name=func_name,
            )

        self._inject_progress_callback(func_name, arguments, context)
        execution_context = self._execution_context(context)
        logger.info("Executing tool: %s(%s)", func_name, arguments)
        result = await self.registry.execute(func_name, arguments, execution_context)
        self._record_result(func_name, arguments, result, context)

        await hook_system.fire(
            "after_tool",
            chat_id=context.chat_id,
            channel=context.channel,
            data={"tool_name": func_name, "arguments": arguments, "result": result},
        )
        return LLMMessage(
            role="tool",
            tool_call_id=tool_call["id"],
            content=result,
            name=func_name,
        )

    async def execute_many(self, tool_calls: List[Dict], context: Any) -> List[LLMMessage]:
        if not tool_calls:
            return []
        if len(tool_calls) == 1:
            return [await self.execute_one(tool_calls[0], context)]
        return list(
            await asyncio.gather(
                *(self.execute_one(tool_call, context) for tool_call in tool_calls)
            )
        )

    def _execution_context(self, context: Any) -> Dict[str, Any]:
        return {
            "chat_id": context.chat_id,
            "channel": context.channel,
            "account_id": context.account_id,
            "approved": context.metadata.get("approved", False),
            "project_id": context.metadata.get("project_id", ""),
            "project_title": context.metadata.get("project_title", ""),
            "project_objective": context.metadata.get("project_objective", ""),
            "workspace_dir": context.metadata.get("workspace_dir", ""),
            "allowed_tools": (
                None if self.profile is None else list(self.profile.tools)
            ),
            "allowed_skills": (
                None if self.profile is None else list(self.profile.skills)
            ),
            "task_event_callback": context.metadata.get("_on_task_event"),
        }

    @staticmethod
    def _record_result(
        func_name: str,
        arguments: Dict[str, Any],
        result: Any,
        context: Any,
    ) -> None:
        trace = context.metadata.get("_evolution_trace")
        if isinstance(trace, EvolutionTrace):
            trace.add_tool(func_name, arguments, result)
        run = context.metadata.get("_run_stats")
        if isinstance(run, RunStats):
            run.tools_called.append(func_name)
            if str(result).lstrip().lower().startswith(("error", "[error")):
                run.tool_errors += 1

    @staticmethod
    def _inject_progress_callback(
        func_name: str,
        arguments: Dict[str, Any],
        context: Any,
    ) -> None:
        on_progress = context.metadata.get("_on_progress")
        if not on_progress or func_name != "execute":
            return
        loop = asyncio.get_running_loop()
        total: Optional[int] = None
        command = str(arguments.get("command", ""))
        match = re.search(r'(?:^|\s)-in\s+(?:"([^"]+)"|(\S+))', command)
        if match:
            input_path = match.group(1) or match.group(2)
            try:
                path = Path(input_path)
                if not path.is_absolute():
                    working_dir = arguments.get("working_dir", "")
                    base = Path(working_dir) if working_dir else active_workspace_dir()
                    if not base.is_absolute():
                        base = active_workspace_dir() / base
                    path = base / input_path
                path = path.resolve()
                if path.exists():
                    content = path.read_text(encoding="utf-8", errors="replace")
                    runs = re.findall(
                        r"^run\s+(\d+)",
                        content,
                        re.MULTILINE | re.IGNORECASE,
                    )
                    if runs:
                        total = sum(int(value) for value in runs)
            except Exception:
                pass

        def on_line(line: str) -> None:
            parts = line.strip().split()
            if len(parts) < 3 or not parts[0].isdigit():
                return
            data: Dict[str, Any] = {"current": int(parts[0])}
            if total is not None:
                data["total"] = total
            try:
                asyncio.run_coroutine_threadsafe(on_progress(data), loop)
            except Exception:
                pass

        arguments["_on_line"] = on_line
