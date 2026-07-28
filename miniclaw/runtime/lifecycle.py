"""Run-scoped statistics, hooks, and evolution trace lifecycle."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict

from ..hooks import hook_system
from ..learning.trace import EvolutionTrace
from ..stats import RunStats, agent_stats


@dataclass
class RunState:
    stats: RunStats
    trace: EvolutionTrace
    usage: Dict[str, int] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
    )


class RunLifecycle:
    """Own all bookkeeping around one Agent loop."""

    def __init__(
        self,
        profile=None,
        *,
        complete_trace: Callable[[EvolutionTrace], None],
        generate_reflection: Callable[[EvolutionTrace], Awaitable[None]],
    ):
        self.profile = profile
        self.complete_trace = complete_trace
        self.generate_reflection = generate_reflection

    def start(self, context: Any) -> RunState:
        stats = agent_stats.start_run(context.chat_id)
        trace = EvolutionTrace(
            chat_id=context.chat_id,
            channel=context.channel,
            objective=context.user_message,
            metadata={
                "project_id": context.metadata.get("project_id", ""),
                "plan_mode": bool(context.metadata.get("plan_mode", False)),
                "agent_profile": self.profile.name if self.profile else "default",
            },
        )
        context.metadata["_run_stats"] = stats
        context.metadata["_evolution_trace"] = trace
        return RunState(stats=stats, trace=trace)

    @staticmethod
    def add_usage(state: RunState, response: Any) -> int:
        prompt = response.usage.get("prompt_tokens", 0)
        completion = response.usage.get("completion_tokens", 0)
        state.usage["prompt_tokens"] += prompt
        state.usage["completion_tokens"] += completion
        return prompt + completion

    async def fail(
        self,
        state: RunState,
        context: Any,
        error: Exception,
        *,
        rounds: int,
    ) -> None:
        message = str(error)
        await hook_system.fire(
            "on_error",
            chat_id=context.chat_id,
            channel=context.channel,
            data={"error": message, "round": rounds},
        )
        state.stats.error = message
        state.trace.finish(
            success=False,
            rounds=rounds,
            prompt_tokens=state.usage["prompt_tokens"],
            completion_tokens=state.usage["completion_tokens"],
            error=message,
        )
        state.stats.success = False
        state.stats.quality_score = state.trace.score
        state.stats.tool_errors = len(state.trace.errors)
        agent_stats.end_run(state.stats)
        self.complete_trace(state.trace)
        await self.generate_reflection(state.trace)

    async def finish(
        self,
        state: RunState,
        context: Any,
        session: Any,
        final_response: str,
        *,
        rounds: int,
        hit_max_rounds: bool,
    ) -> None:
        await hook_system.fire(
            "after_agent",
            chat_id=context.chat_id,
            channel=context.channel,
            data={
                "rounds": rounds,
                "final_response_length": len(final_response),
                "total_tokens": state.usage,
                "session_id": session.session_id,
            },
        )
        state.stats.prompt_tokens = state.usage["prompt_tokens"]
        state.stats.completion_tokens = state.usage["completion_tokens"]
        state.stats.rounds = rounds
        state.trace.finish(
            success=bool(final_response.strip()),
            final_response=final_response,
            rounds=rounds,
            prompt_tokens=state.usage["prompt_tokens"],
            completion_tokens=state.usage["completion_tokens"],
            hit_max_rounds=hit_max_rounds,
        )
        state.stats.success = state.trace.success
        state.stats.quality_score = state.trace.score
        state.stats.hit_max_rounds = hit_max_rounds
        agent_stats.end_run(state.stats)
        self.complete_trace(state.trace)
        await self.generate_reflection(state.trace)
