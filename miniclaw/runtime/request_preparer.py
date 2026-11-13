"""Prepare one Agent request before entering the shared loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ..hooks import hook_system
from ..llm.base import LLMMessage
from ..logger import get_logger
from ..settings import config
from ..tools.registry import tool_registry
from .room_context import (
    BUDGET_CONTEXT_KEY,
    FINISH_TOOL,
    PUBLISH_TOOL,
    blocks_room_tool,
    room_finisher,
    room_publisher,
)

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
        for key in ("project_id", "parent_session_id", "root_session_id", "task_id"):
            if context.metadata.get(key):
                session.metadata[key] = context.metadata[key]
        session.metadata.setdefault("root_session_id", session.session_id)

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

        plan_mode = context.metadata.get("plan_mode", False)
        if plan_mode:
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

        binding = context.metadata.get("_graph_binding")
        if self.profile is not None:
            from ..skills.project import project_store

            store = project_store(context.metadata)
            mounted = store.mounted(self.profile) if store is not None else {}
            if mounted:
                system_prompt += (
                    "\n\n## Temporary project Skills (this role only)\n"
                    "These validated, version-pinned packages extend your available Skills for this project. "
                    "Load a matching package with read_skill. They do not grant tools or authorize business operations outside your role. "
                    "Within your original role responsibilities, you may use these supplied scripts in addition to the initially packaged Skills. "
                    "The prohibition on ad-hoc implementations and on explicitly forbidden business operations remains.\n"
                    + self.prompt_builder.build_skill_summary(list(mounted.values()))
                )
        mode = "default"
        mode_context = ""
        if binding is not None:
            mode = "graph"
            mode_context = binding.prompt()
            tools = [
                tool for tool in (tools or [])
                if not binding.blocks(tool["function"]["name"], tool_registry)
            ] + binding.tool_definitions()

        # Registration is global; availability and identity belong to this request.
        tools = [tool for tool in (tools or []) if not blocks_room_tool(tool["function"]["name"], context, tool_registry)]
        if room_publisher(context) is not None:
            mode = "room"
            room_tools = {PUBLISH_TOOL}
            if room_finisher(context) is not None:
                room_tools.add(FINISH_TOOL)
            visible = {tool["function"]["name"] for tool in tools}
            tools += tool_registry.list_for_llm(allowed_names=room_tools - visible)
            member = self.profile.name if self.profile else context.chat_id
            mode_context += f"\n\nYour room member: {member}."
            budget = context.metadata.get(BUDGET_CONTEXT_KEY)
            if budget is not None:
                mode_context += (
                    f"\n本轮总发言上限 {budget['max_messages']} 条。"
                    f"本次唤醒时普通发言还剩 {budget['discussion_remaining']} 条，"
                    f"其中总预算内留 {budget['reserved_for_final']} 条给最终总结。"
                )
                if "members" in budget:
                    mode_context += (
                        f"\n频道成员 {', '.join(budget['members'])}。"
                        f"已公开发言 {', '.join(budget['public_members']) or '暂无'}。"
                        f"已至少完成一轮 {', '.join(budget['finished_members']) or '暂无'}。"
                        f"首轮机会仍待完成的成员 {', '.join(budget['pending_members']) or '无'}。"
                    )
                    if budget["failed_members"]:
                        mode_context += (
                            f"\n本轮后续不可用的成员 {', '.join(budget['failed_members'])}。"
                            "其已公开的意见保留，请依据现有证据收束并注明相关缺口。"
                        )
                    if budget["closeout"]:
                        mode_context += (
                            "\n频道已经空闲，本次唤醒用于收束。请根据已有信息给出最终总结，"
                            "并调用 finish_discussion 结束本轮。"
                        )

        if not plan_mode:
            system_prompt += "\n\n" + self.prompt_builder.build_collaboration_prompt(mode)
        system_prompt += mode_context

        return PreparedRequest(
            session=session,
            run_state=run_state,
            system_prompt=system_prompt,
            tools=tools,
            max_rounds=max_rounds,
        )
