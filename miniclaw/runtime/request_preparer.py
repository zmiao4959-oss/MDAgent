"""Prepare one Agent request before entering the shared loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ..hooks import hook_system
from ..llm.base import LLMMessage
from ..logger import get_logger
from ..settings import config
from ..tools.registry import tool_registry

logger = get_logger(__name__)


@dataclass
class PreparedRequest:
    session: Any
    run_state: Any = None
    system_prompt: str = ""
    tools: Optional[List[dict]] = None
    max_rounds: int = 0
    blocked_response: Optional[str] = None


class RequestPreparer:
    """Resolve session, enrich the message, and choose runtime capabilities."""

    def __init__(
        self,
        *,
        sessions: Any,
        lifecycle: Any,
        prompt_builder: Any,
        profile: Any = None,
        memory_prefix: Callable[[str], str],
        experience_prefix: Callable[[str, Any], str],
    ):
        self.sessions = sessions
        self.lifecycle = lifecycle
        self.prompt_builder = prompt_builder
        self.profile = profile
        self.memory_prefix = memory_prefix
        self.experience_prefix = experience_prefix

    async def prepare(self, context: Any) -> PreparedRequest:
        session = await self.sessions.resolve_session(context.chat_id)
        if session is None:
            session = self.sessions.get_or_create(
                context.chat_id,
                context.channel,
                context.account_id,
            )

        hook = await hook_system.fire(
            "before_agent",
            chat_id=context.chat_id,
            channel=context.channel,
            account_id=context.account_id,
            data={"message": context.user_message},
        )
        if hook.prevent:
            return PreparedRequest(
                session=session,
                blocked_response=f"[Agent blocked: {hook.prevent_reason}]",
            )

        run_state = self.lifecycle.start(context)
        if context.user_message:
            prefix = (
                self.experience_prefix(context.user_message, run_state.trace)
                + self.memory_prefix(context.user_message)
            )
            session.add_message(
                LLMMessage(
                    role="user",
                    content=prefix + context.user_message if prefix else context.user_message,
                )
            )

        if context.metadata.get("plan_mode", False):
            from ..planning import build_planning_system_prompt

            project_id = context.metadata.get("project_id", "")
            system_prompt = build_planning_system_prompt(
                project_id,
                context.metadata.get("project_title", ""),
                context.metadata.get("project_objective", ""),
            )
            tools = tool_registry.list_for_llm_exclude(
                exclude_tags=["execution", "shell", "browser", "communication"],
                allowed_names=(
                    None if self.profile is None else set(self.profile.tools)
                ),
            )
            max_rounds = config.agent.planning.max_tool_rounds
            logger.info(
                "Plan mode active for project %s (chat %s)",
                project_id or "(none)",
                context.chat_id,
            )
        else:
            system_prompt = self.prompt_builder.build_system_prompt()
            tools = self.prompt_builder.build_tool_definitions()
            max_rounds = (
                self.profile.runtime.max_tool_rounds
                if self.profile and self.profile.runtime.max_tool_rounds is not None
                else config.agent.max_tool_rounds
            )

        return PreparedRequest(
            session=session,
            run_state=run_state,
            system_prompt=system_prompt,
            tools=tools,
            max_rounds=max_rounds,
        )
