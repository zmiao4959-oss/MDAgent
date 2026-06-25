"""
tools/registry.py — 工具注册与执行中心
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Callable, Any, Optional, Set

from dataclasses import dataclass, field

from ..logger import get_logger

logger = get_logger(__name__)

# 任意工具返回给模型的总字符上限
MAX_TOOL_RESULT_CHARS = 32_000


@dataclass
class ToolDefinition:
    """一个工具的完整定义"""
    name: str
    func: Callable
    schema: Dict[str, Any]
    description: str = ""
    require_approval: bool = False
    risk_level: str = "low"  # low | medium | high
    tags: List[str] = field(default_factory=list)
    timeout_sec: Optional[float] = None  # 异步/同步工具可选超时


class ToolRegistry:
    """全局工具注册中心"""

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._by_tag: Dict[str, List[ToolDefinition]] = {}

    def register(
        self,
        name: str,
        schema: Dict[str, Any],
        description: str = "",
        require_approval: bool = False,
        risk_level: str = "low",
        tags: Optional[List[str]] = None,
        timeout_sec: Optional[float] = None,
    ):
        def decorator(func: Callable):
            tool_def = ToolDefinition(
                name=name,
                func=func,
                schema=schema,
                description=description,
                require_approval=require_approval,
                risk_level=risk_level,
                tags=tags or [],
                timeout_sec=timeout_sec,
            )
            self._tools[name] = tool_def
            for tag in tool_def.tags:
                self._by_tag.setdefault(tag, []).append(tool_def)
            logger.debug("Registered tool: %s (risk=%s)", name, risk_level)
            return func

        return decorator

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def tool_names(self) -> List[str]:
        return sorted(self._tools.keys())

    def list_for_llm(self, tag_filter: Optional[List[str]] = None) -> List[Dict]:
        tools = list(self._tools.values())
        if tag_filter:
            tag_set = set(tag_filter)
            tools = [t for t in tools if tag_set.issubset(set(t.tags))]
        return [t.schema for t in tools]

    def list_for_llm_exclude(self, exclude_tags: Optional[List[str]] = None) -> List[Dict]:
        """返回工具 schema 列表，排除包含任一 exclude_tag 的工具（OR 逻辑）。"""
        tools = list(self._tools.values())
        if exclude_tags:
            exclude_set = set(exclude_tags)
            tools = [t for t in tools if not exclude_set.intersection(set(t.tags))]
        return [t.schema for t in tools]

    def get_descriptions(self, exclude_tags: Optional[List[str]] = None) -> str:
        lines = []
        exclude_set = set(exclude_tags) if exclude_tags else set()
        for td in self._tools.values():
            if exclude_set and exclude_set.intersection(set(td.tags)):
                continue
            extra = ""
            if td.risk_level != "low":
                extra = f" [{td.risk_level} risk]"
            if td.require_approval:
                extra += " [requires approval]"
            lines.append("  <tool>")
            lines.append(f"    <name>{td.name}</name>")
            lines.append(f"    <description>{td.description}{extra}</description>")
            lines.append("  </tool>")
        return "\n".join(lines)

    def _schema_parameters(self, tool: ToolDefinition) -> Dict[str, Any]:
        fn = tool.schema.get("function") or {}
        return fn.get("parameters") or {}

    def _allowed_arg_names(self, tool: ToolDefinition) -> Set[str]:
        params = self._schema_parameters(tool)
        return set((params.get("properties") or {}).keys())

    def _validate_arguments(self, tool: ToolDefinition, arguments: Dict[str, Any]) -> Optional[str]:
        params = self._schema_parameters(tool)
        required = params.get("required") or []
        missing = [k for k in required if k not in arguments]
        if missing:
            return f"Error: Missing required argument(s) for '{tool.name}': {', '.join(missing)}"
        return None

    def _filter_arguments(self, tool: ToolDefinition, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """只保留 schema 声明的参数，避免模型多传字段导致 TypeError。_ 开头的内部参数始终放行。"""
        allowed = self._allowed_arg_names(tool)
        if not allowed:
            return dict(arguments)
        return {k: v for k, v in arguments.items() if k in allowed or k.startswith("_")}

    def _truncate_result(self, text: str) -> str:
        if len(text) <= MAX_TOOL_RESULT_CHARS:
            return text
        return truncate_tool_result(text, MAX_TOOL_RESULT_CHARS)

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        context: Optional[Dict] = None,
    ) -> str:
        tool = self.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"

        args = dict(arguments or {})
        args.pop("_context", None)

        err = self._validate_arguments(tool, args)
        if err:
            return err

        args = self._filter_arguments(tool, args)

        if tool.require_approval and not (context or {}).get("approved"):
            logger.warning("Tool '%s' is marked require_approval (not enforced yet)", name)

        try:
            result = await self._invoke(tool, args)
            text = result if isinstance(result, str) else str(result)
            return self._truncate_result(text)
        except asyncio.TimeoutError:
            logger.warning("Tool '%s' timed out after %ss", name, tool.timeout_sec)
            return f"Error: Tool '{name}' timed out after {tool.timeout_sec}s"
        except TypeError as e:
            logger.warning("Tool '%s' argument error: %s", name, e)
            return f"Error: Invalid arguments for '{name}': {e}"
        except Exception as e:
            logger.exception("Tool '%s' execution failed", name)
            return f"Error executing '{name}': {e}"

    async def _invoke(self, tool: ToolDefinition, args: Dict[str, Any]) -> Any:
        if asyncio.iscoroutinefunction(tool.func):
            coro = tool.func(**args)
            if tool.timeout_sec:
                return await asyncio.wait_for(coro, timeout=tool.timeout_sec)
            return await coro

        def _run_sync():
            return tool.func(**args)

        if tool.timeout_sec:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_sync),
                timeout=tool.timeout_sec,
            )
        return await asyncio.to_thread(_run_sync)


def truncate_tool_result(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    omitted = len(text) - head - tail
    return (
        f"{text[:head].rstrip()}\n\n"
        f"... [tool output truncated, {omitted} chars omitted] ...\n\n"
        f"{text[-tail:].lstrip()}"
    )


tool_registry = ToolRegistry()
