"""
hooks.py — 生命周期事件系统 (inspired by Claude Code's 27-event hook system)

支持的生命周期事件:
  - before_agent       : Agent 开始处理消息前
  - after_agent        : Agent 处理消息完成后
  - before_llm         : 每次 LLM 调用前
  - after_llm          : 每次 LLM 调用后
  - before_tool        : 工具执行前 (可阻止执行)
  - after_tool         : 工具执行后 (可修改结果)
  - on_compaction      : 上下文压缩时
  - on_error           : Agent 循环出错时
  - on_session_start   : 新会话创建时
  - on_session_end     : 会话结束时

Hook 优先级: critical(0) > high(10) > normal(50) > low(100)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .logger import get_logger

logger = get_logger(__name__)


class HookPriority(IntEnum):
    CRITICAL = 0
    HIGH = 10
    NORMAL = 50
    LOW = 100


@dataclass
class HookContext:
    """传递给每个 hook 的上下文数据"""
    event: str
    chat_id: str = ""
    channel: str = ""
    account_id: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    # 阻止标志: before_tool hook 可以设置此标志阻止工具执行
    prevent: bool = False
    prevent_reason: str = ""


HookHandler = Callable[[HookContext], Awaitable[Optional[HookContext]]]


@dataclass
class HookRegistration:
    handler: HookHandler
    priority: HookPriority
    hook_id: str
    description: str = ""
    enabled: bool = True


class HookSystem:
    """全局钩子系统"""

    _instance: Optional["HookSystem"] = None

    def __init__(self):
        self._hooks: Dict[str, List[HookRegistration]] = {}
        self._hook_counter: int = 0

    @classmethod
    def get_instance(cls) -> "HookSystem":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def on(
        self,
        event: str,
        *,
        priority: HookPriority = HookPriority.NORMAL,
        description: str = "",
    ):
        """装饰器: 注册一个事件处理器"""
        def decorator(handler: HookHandler) -> HookHandler:
            self.register(event, handler, priority=priority, description=description)
            return handler
        return decorator

    def register(
        self,
        event: str,
        handler: HookHandler,
        *,
        priority: HookPriority = HookPriority.NORMAL,
        description: str = "",
    ) -> str:
        """注册一个 hook 处理器，返回 hook_id"""
        self._hook_counter += 1
        hook_id = f"hook:{self._hook_counter}:{event}"
        reg = HookRegistration(
            handler=handler,
            priority=priority,
            hook_id=hook_id,
            description=description,
        )
        self._hooks.setdefault(event, []).append(reg)
        self._hooks[event].sort(key=lambda r: r.priority)
        logger.debug("Hook registered: %s → %s (priority=%s)", hook_id, event, priority.name)
        return hook_id

    def unregister(self, hook_id: str) -> bool:
        """移除一个 hook"""
        for event, registrations in self._hooks.items():
            for i, reg in enumerate(registrations):
                if reg.hook_id == hook_id:
                    self._hooks[event].pop(i)
                    if not self._hooks[event]:
                        del self._hooks[event]
                    logger.debug("Hook unregistered: %s", hook_id)
                    return True
        return False

    def list_hooks(self) -> Dict[str, List[Dict]]:
        """列出所有已注册的 hooks"""
        return {
            event: [
                {"id": r.hook_id, "priority": r.priority.name, "desc": r.description, "enabled": r.enabled}
                for r in regs
            ]
            for event, regs in sorted(self._hooks.items())
        }

    async def fire(self, event: str, ctx: Optional[HookContext] = None, **kwargs) -> HookContext:
        """
        触发一个事件，按优先级顺序调用所有注册的处理器。

        返回最终的 HookContext (可能被处理器修改)。
        before_tool 处理器可以设置 ctx.prevent = True 来阻止执行。
        """
        ctx = ctx or HookContext(event=event, **kwargs)
        registrations = self._hooks.get(event, [])
        if not registrations:
            return ctx

        for reg in registrations:
            if not reg.enabled:
                continue
            try:
                result = await reg.handler(ctx)
                if result is not None:
                    ctx = result
                if ctx.prevent:
                    logger.info("Hook '%s' prevented event '%s': %s", reg.hook_id, event, ctx.prevent_reason)
                    break
            except Exception:
                logger.exception("Hook '%s' failed for event '%s'", reg.hook_id, event)

        return ctx

    async def fire_parallel(self, event: str, ctx: Optional[HookContext] = None, **kwargs) -> List[HookContext]:
        """并行触发所有处理器（用于无副作用的观察类事件），返回所有结果。"""
        ctx = ctx or HookContext(event=event, **kwargs)
        registrations = self._hooks.get(event, [])
        if not registrations:
            return [ctx]

        async def _run_one(reg: HookRegistration) -> Optional[HookContext]:
            if not reg.enabled:
                return None
            try:
                return await reg.handler(ctx)
            except Exception:
                logger.exception("Hook '%s' failed for event '%s'", reg.hook_id, event)
                return None

        results = await asyncio.gather(*[_run_one(r) for r in registrations])
        return [r for r in results if r is not None] or [ctx]

    def clear(self):
        """清空所有 hooks"""
        self._hooks.clear()
        self._hook_counter = 0


# 便捷导入
hook_system = HookSystem.get_instance()


# ── 内置 Hooks ────────────────────────────────────────────

@hook_system.on("before_tool", priority=HookPriority.HIGH, description="记录工具调用耗时")
async def _tool_timing_hook(ctx: HookContext) -> HookContext:
    """为每个工具调用记录开始时间，供 after_tool 计算耗时。"""
    ctx.data["_tool_start_time"] = time.time()
    return ctx


@hook_system.on("after_tool", priority=HookPriority.LOW, description="记录工具调用耗时和结果长度")
async def _tool_metrics_hook(ctx: HookContext) -> HookContext:
    """记录工具调用指标。"""
    start_time = ctx.data.get("_tool_start_time")
    if start_time:
        elapsed_ms = (time.time() - start_time) * 1000
        tool_name = ctx.data.get("tool_name", "unknown")
        result_len = len(str(ctx.data.get("result", "")))
        logger.info("Tool '%s' completed in %.0fms (result: %s chars)", tool_name, elapsed_ms, result_len)
    return ctx


@hook_system.on("on_error", priority=HookPriority.HIGH, description="记录错误详情")
async def _error_logging_hook(ctx: HookContext) -> HookContext:
    """记录详细的错误上下文。"""
    error = ctx.data.get("error")
    chat_id = ctx.chat_id or "unknown"
    logger.error("Agent error in chat '%s': %s", chat_id, error)
    return ctx
