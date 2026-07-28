"""The reusable multi-round LLM and Tool execution loop."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..hooks import hook_system
from ..llm.base import LLMMessage
from ..logger import get_logger
from ..viz.auto import AutoVisualizer
from ..viz.snapshot import snapshot_workspace

logger = get_logger(__name__)


@dataclass
class LoopResult:
    final_response: str
    rounds: int
    hit_max_rounds: bool


class AgentLoop:
    """Run one prepared Agent request using injected shared services."""

    def __init__(
        self,
        *,
        sessions: Any,
        llm: Any,
        call_llm: Callable[..., Awaitable[Any]],
        execute_tools: Callable[..., Awaitable[List[LLMMessage]]],
        finalize_after_max_rounds: Callable[..., Awaitable[str]],
    ):
        self.sessions = sessions
        self.llm = llm
        self.call_llm = call_llm
        self.execute_tools = execute_tools
        self.finalize_after_max_rounds = finalize_after_max_rounds

    async def run(
        self,
        *,
        session: Any,
        context: Any,
        system_prompt: str,
        tools: Optional[List[Dict]],
        max_rounds: int,
        run_state: Any,
        lifecycle: Any,
        on_stream_chunk=None,
        on_viz_event=None,
    ) -> LoopResult:
        messages = [LLMMessage(role="system", content=system_prompt), *session.messages]
        final_response = ""
        pending_viz: List[asyncio.Task] = []
        context.metadata["viz_tasks"] = pending_viz
        auto_viz = (
            AutoVisualizer(session, on_viz_event, pending_viz)
            if context.channel == "webchat" and on_viz_event
            else None
        )
        session.metadata.setdefault("total_tokens", 0)

        for round_num in range(1, max_rounds + 1):
            if session.should_compact():
                logger.info(
                    "Compacting session %s before round %s",
                    session.session_id,
                    round_num,
                )
                await hook_system.fire(
                    "on_compaction",
                    chat_id=context.chat_id,
                    data={"session_id": session.session_id, "round": round_num},
                )
                await self.sessions.compact(session, self.llm)
                messages = [
                    LLMMessage(role="system", content=system_prompt),
                    *session.messages,
                ]

            response = await self.call_llm(
                messages,
                tools,
                stream=bool(on_stream_chunk),
                on_stream_chunk=on_stream_chunk,
            )
            round_tokens = lifecycle.add_usage(run_state, response)
            session.metadata["total_tokens"] = (
                session.metadata.get("total_tokens", 0) + round_tokens
            )
            if response.content:
                final_response = response.content
            if not response.tool_calls:
                if response.content:
                    session.add_message(
                        LLMMessage(
                            role="assistant",
                            content=response.content,
                            reasoning_content=response.reasoning_content,
                        )
                    )
                return LoopResult(final_response, round_num, False)

            assistant = LLMMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
            )
            session.add_message(assistant)
            messages.append(assistant)

            before = snapshot_workspace() if auto_viz else {}
            context.metadata["_tool_idem_keys"] = set()
            results = await self.execute_tools(response.tool_calls, context)
            for result in results:
                session.add_message(result)
                messages.append(result)
            if auto_viz:
                await auto_viz.after_tool_round(before, snapshot_workspace())
            await self.sessions.save(session)
            logger.info(
                "Round %s: executed %s tool(s), tokens=%s",
                round_num,
                len(response.tool_calls),
                round_tokens,
            )

        recovery = await self.finalize_after_max_rounds(
            session,
            system_prompt,
            on_stream_chunk,
        )
        return LoopResult(recovery or final_response, max_rounds, True)
