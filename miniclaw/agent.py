"""
agent.py — Agent 主循环（★ 核心引擎）
"""
import asyncio
from typing import List, Dict, Optional, Any, Callable, Awaitable, Tuple, TYPE_CHECKING

from dataclasses import dataclass, field

from .settings import active_workspace_dir, config, workspace_scope, WORKSPACE_DIR
from .logger import get_logger
from .llm.base import (
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    merge_stream_fragment,
)
from .llm.router import LLMRouter
from .tools import ensure_tools_loaded
from .tools.registry import tool_registry
from .memory.session import Session, SessionManager
from .memory.search import keyword_search
from .skills.loader import SkillLoader
from .hooks import hook_system
from .learning.trace import EvolutionTrace
from .learning.service import EvolutionService
from .runtime import (
    PromptBuilder,
    ToolExecutor,
    RunLifecycle,
    AgentLoop,
    RequestPreparer,
)

if TYPE_CHECKING:
    from .agents.profile import AgentProfile

logger = get_logger(__name__)

StreamChunkCallback = Callable[[str], Awaitable[None]]
VizEventCallback = Callable[[Dict[str, Any]], Awaitable[None]]
ProgressCallback = Callable[[Dict[str, Any]], Awaitable[None]]

@dataclass
class AgentContext:
    """单次 Agent 调用的上下文"""
    chat_id: str
    channel: str
    account_id: str
    user_message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class Agent:
    """Agent 核心引擎"""

    def __init__(
        self,
        llm_router: LLMRouter,
        session_manager: SessionManager,
        profile: Optional["AgentProfile"] = None,
    ):
        ensure_tools_loaded()
        self.llm = llm_router
        self.sessions = session_manager
        self.profile = profile
        self._skill_loader = SkillLoader()
        if profile is not None:
            known_tools = set(tool_registry.tool_names())
            unknown_tools = set(profile.tools) - {"*"} - known_tools
            if unknown_tools:
                raise ValueError(
                    f"Agent profile '{profile.name}' references unknown tools: "
                    f"{', '.join(sorted(unknown_tools))}"
                )
            known_skills = {skill.name for skill in self._skill_loader.list_all()}
            unknown_skills = set(profile.skills) - {"*"} - known_skills
            if unknown_skills:
                raise ValueError(
                    f"Agent profile '{profile.name}' references unknown skills: "
                    f"{', '.join(sorted(unknown_skills))}"
                )
        self.prompt_builder = PromptBuilder(
            profile,
            skill_loader=self._skill_loader,
            workspace_dir=WORKSPACE_DIR,
        )
        self.tool_executor = ToolExecutor(profile)
        self.run_lifecycle = RunLifecycle(
            profile,
            complete_trace=self._complete_evolution_trace,
            generate_reflection=self._maybe_generate_reflection,
        )
        self.agent_loop = AgentLoop(
            sessions=self.sessions,
            llm=self.llm,
            call_llm=self._call_llm,
            execute_tools=self._handle_tool_calls,
            finalize_after_max_rounds=self._finalize_after_max_rounds,
        )
        self.request_preparer = RequestPreparer(
            sessions=self.sessions,
            lifecycle=self.run_lifecycle,
            prompt_builder=self.prompt_builder,
            profile=self.profile,
            memory_prefix=self._memory_prefix,
            experience_prefix=self._experience_prefix,
        )
        self._system_prompt_cache: Optional[str] = None
        self._system_prompt_cache_key: Optional[Tuple[Any, ...]] = None

    # ── 三层上下文工程 (Stable / Context / Volatile) ──

    def _stable_fingerprint(self) -> Tuple[Any, ...]:
        """Stable 层指纹：身份文件 + 工具 + 技能（最少变动，缓存友好）。"""
        return self.prompt_builder.stable_fingerprint()

    def _visible_tool_names(self) -> List[str]:
        return self.prompt_builder.visible_tool_names()

    def _visible_skills(self) -> List[Any]:
        return self.prompt_builder.visible_skills()

    def _context_fingerprint(self) -> Tuple[Any, ...]:
        """Context 层指纹：环境文件（AGENTS.md, USER.md, MEMORY.md）。"""
        return self.prompt_builder.context_fingerprint()

    def _build_stable_prompt(self) -> str:
        """Stable 层：身份 + 工具描述 + 技能列表（缓存友好，极少重建）。"""
        return self.prompt_builder.build_stable_prompt()

    def _build_tool_prompt_summary(self) -> str:
        """Keep the prompt compact; exact tool schemas are sent separately."""
        return self.prompt_builder.build_tool_summary()

    def _build_skill_prompt_summary(self, skills_list: List[Any]) -> str:
        """Summarise skills without flooding the prompt with file paths."""
        return self.prompt_builder.build_skill_summary(skills_list)

    def _build_context_prompt(self) -> str:
        """Context 层：项目指令 + 用户偏好 + 长期记忆。"""
        return self.prompt_builder.build_context_prompt()

    def _build_system_prompt(self) -> str:
        """三层上下文组装：Stable（缓存） + Context（文件感知缓存） + Volatile（每轮动态）。"""
        self.prompt_builder.skill_loader = self._skill_loader
        prompt = self.prompt_builder.build_system_prompt()
        self._system_prompt_cache = prompt
        return prompt

    def _build_tool_definitions(self) -> List[Dict]:
        """生成 OpenAI function-calling 格式的工具定义"""
        return self.prompt_builder.build_tool_definitions()

    async def _resolve_session(self, context: AgentContext) -> Session:
        """优先从磁盘恢复会话，避免 Gateway 重启后丢失历史。"""
        session = await self.sessions.resolve_session(context.chat_id)
        if session is not None:
            return session
        return self.sessions.get_or_create(
            context.chat_id, context.channel, context.account_id
        )

    def _memory_prefix(self, user_message: str) -> str:
        if not user_message:
            return ""
        search_results = keyword_search(user_message, max_results=3)
        if not search_results:
            return ""
        lines = ["[Memory Search Results]"]
        for path, score, snippet in search_results:
            lines.append(f"Source: {path}# (score: {score:.2f})")
            lines.append(snippet)
            lines.append("")
        logger.debug("Found %s memory matches", len(search_results))
        return "\n".join(lines).rstrip() + "\n\n"

    def _experience_prefix(
        self,
        user_message: str,
        trace: Optional[EvolutionTrace] = None,
    ) -> str:
        if not (
            config.evolution.enabled
            and config.evolution.inject_verified
            and user_message
        ):
            return ""
        try:
            prefix, experience_ids = EvolutionService(active_workspace_dir()).prompt_context(
                user_message, limit=config.evolution.max_injected
            )
            if trace is not None and experience_ids:
                trace.metadata["applied_experience_ids"] = experience_ids
            return prefix
        except Exception:
            logger.exception("Failed to retrieve verified agent experience")
            return ""

    @staticmethod
    def _complete_evolution_trace(trace: EvolutionTrace) -> None:
        if not config.evolution.enabled:
            return
        try:
            service = EvolutionService(active_workspace_dir())
            service.complete_trace(
                trace,
                learn=config.evolution.auto_observe,
                evaluate_applied=config.evolution.auto_evaluate_applied,
            )
            if config.evolution.maintenance_enabled:
                service.maintain_if_due(
                    interval_hours=config.evolution.maintenance_interval_hours,
                    candidate_ttl_days=config.evolution.candidate_ttl_days,
                    verified_review_days=config.evolution.verified_review_days,
                    trace_retention_days=config.evolution.trace_retention_days,
                )
        except Exception:
            logger.exception("Failed to persist self-evolution trace")

    async def _maybe_generate_reflection(self, trace: EvolutionTrace) -> None:
        if not (config.evolution.enabled and config.evolution.deep_reflection_enabled):
            return
        try:
            await asyncio.wait_for(
                EvolutionService(active_workspace_dir()).generate_reflection(
                    trace, self.llm, model=config.llm.model
                ),
                timeout=config.evolution.deep_reflection_timeout_sec,
            )
        except Exception:
            logger.exception("Deep reflection proposal generation failed")

    async def _execute_one_tool(
        self, tc: Dict, context: AgentContext
    ) -> LLMMessage:
        return await self.tool_executor.execute_one(tc, context)

    async def _handle_tool_calls(
        self, tool_calls: List[Dict], context: AgentContext
    ) -> List[LLMMessage]:
        """并行执行工具调用，返回工具结果消息列表（顺序与 tool_calls 一致）。"""
        return await self.tool_executor.execute_many(tool_calls, context)

    async def _call_llm(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict]],
        *,
        stream: bool,
        on_stream_chunk: Optional[StreamChunkCallback],
    ) -> LLMResponse:
        # ── before_llm hook ──
        await hook_system.fire("before_llm",
            data={"message_count": len(messages), "tool_count": len(tools) if tools else 0},
        )


        # print("messages:\n\n", messages)
        # print("tools:\n\n", tools)


        if stream and on_stream_chunk:
            full_content = ""
            # 流式工具调用累积 (类似 openai_compat.py 的实现)
            accumulated_tool_calls: Dict[int, Dict] = {}
            stream_reasoning: Optional[str] = None
            stream_finish: Optional[str] = None
            usage: Dict[str, int] = {}
            async for chunk in self.llm.chat_stream(
                messages,
                tools,
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens,
            ):
                # ── 处理 delta 文本内容 ──
                if chunk.delta_content:
                    full_content += chunk.delta_content
                    await on_stream_chunk(chunk.delta_content)

                # ── 捕获 reasoning_content (DeepSeek thinking) ──
                if chunk.reasoning_content:
                    stream_reasoning = chunk.reasoning_content

                # ── 处理增量 tool_calls ──
                if chunk.delta_tool_calls:
                    for pos, tc_delta in enumerate(chunk.delta_tool_calls):
                        idx = tc_delta.get("index", pos)
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "index": idx,
                                "id": tc_delta.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        acc = accumulated_tool_calls[idx]
                        if tc_delta.get("id"):
                            acc["id"] = tc_delta["id"]
                        fn = tc_delta.get("function") or {}
                        # name 是原子字段，用赋值而非拼接（避免重复 chunk 导致 readread）
                        if fn.get("name"):
                            acc["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            acc["function"]["arguments"] = merge_stream_fragment(
                                acc["function"]["arguments"],
                                fn["arguments"],
                            )

                if chunk.finish_reason:
                    stream_finish = chunk.finish_reason
                if chunk.usage:
                    usage = chunk.usage

            # 构建最终的 tool_calls 列表
            final_tool_calls = list(accumulated_tool_calls.values()) if accumulated_tool_calls else []

            response = LLMResponse(
                content=full_content,
                tool_calls=final_tool_calls,
                finish_reason=stream_finish or "stop",
                usage=usage,
                reasoning_content=stream_reasoning,
            )
        else:
            response = await self.llm.chat(
                messages,
                tools,
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens,
            )
            if on_stream_chunk and response.content:
                await on_stream_chunk(response.content)

        # ── after_llm hook ──
        await hook_system.fire("after_llm",
            data={
                "finish_reason": response.finish_reason,
                "has_tool_calls": bool(response.tool_calls),
                "content_length": len(response.content),
                "usage": response.usage,
            },
        )

        return response

    def _should_stop_loop(self, response: LLMResponse) -> bool:
        """无待执行工具时结束循环（有 tool_calls 则继续）。"""
        return not response.tool_calls

    async def _finalize_after_max_rounds(
        self,
        session: Session,
        system_prompt: str,
        on_stream_chunk: Optional[StreamChunkCallback],
    ) -> str:
        """达到轮次上限且仍以 tool 消息结尾时，再请求一次纯文本收尾。"""
        if not session.messages or session.messages[-1].role != "tool":
            return ""
        logger.warning(
            "Max tool rounds reached with pending tool results; requesting final reply"
        )
        messages = [
            LLMMessage(role="system", content=system_prompt),
            *session.messages,
        ]
        response = await self._call_llm(
            messages, tools=None, stream=False, on_stream_chunk=on_stream_chunk
        )
        if response.content:
            session.add_message(LLMMessage(
                role="assistant",
                content=response.content,
                reasoning_content=response.reasoning_content,
            ))
            return response.content
        return "已达到最大工具调用轮数，任务可能未完成，请简化需求后重试。"

    async def process_message(
        self,
        context: AgentContext,
        on_stream_chunk: Optional[StreamChunkCallback] = None,
        on_viz_event: Optional[VizEventCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> str:
        """Run one request inside its project-specific workspace, if provided."""
        with workspace_scope(context.metadata.get("workspace_dir")):
            return await self._process_message(
                context,
                on_stream_chunk=on_stream_chunk,
                on_viz_event=on_viz_event,
                on_progress=on_progress,
            )

    async def _process_message(
        self,
        context: AgentContext,
        on_stream_chunk: Optional[StreamChunkCallback] = None,
        on_viz_event: Optional[VizEventCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> str:
        """
        核心方法：接收用户消息，运行 Agent Loop，返回最终回复

        新增特性:
          - before_agent / after_agent hooks
          - 每轮后自动保存 session (checkpoint recovery)
          - 单轮工具失败不终止整个循环
          - 错误时触发 on_error hook
        """
        if on_progress:
            context.metadata["_on_progress"] = on_progress
        prepared = await self.request_preparer.prepare(context)
        if prepared.blocked_response is not None:
            return prepared.blocked_response
        session = prepared.session
        run_state = prepared.run_state
        system_prompt = prepared.system_prompt
        tools = prepared.tools
        max_rounds = prepared.max_rounds

        all_usage = run_state.usage

        try:
            loop_result = await self.agent_loop.run(
                session=session,
                context=context,
                system_prompt=system_prompt,
                tools=tools,
                max_rounds=max_rounds,
                run_state=run_state,
                lifecycle=self.run_lifecycle,
                on_stream_chunk=on_stream_chunk,
                on_viz_event=on_viz_event,
            )
            final_response = loop_result.final_response
            round_num = loop_result.rounds
            hit_max_rounds = loop_result.hit_max_rounds

        except Exception as exc:
            logger.exception("Agent loop failed for chat_id=%s", context.chat_id)
            await self.run_lifecycle.fail(
                run_state,
                context,
                exc,
                rounds=round_num if "round_num" in locals() else 0,
            )
            # ── 错误时也要尽力保存 session ──
            await self.sessions.save(session)
            raise

        # 持久化 token 用量统计
        session.metadata["last_run_tokens"] = (
            all_usage["prompt_tokens"] + all_usage["completion_tokens"]
        )
        await self.sessions.save(session)

        if on_stream_chunk:
            print(file=__import__("sys").stdout, flush=True)
        if not (final_response or "").strip():
            for m in reversed(session.messages):
                if m.role == "assistant" and (m.content or "").strip():
                    final_response = m.content
                    break
            if not (final_response or "").strip():
                final_response = "（模型未返回文本，可能仅执行了工具调用；请查看上文工具输出或重试。）"

        await self.run_lifecycle.finish(
            run_state,
            context,
            session,
            final_response,
            rounds=round_num if "round_num" in locals() else 0,
            hit_max_rounds=hit_max_rounds,
        )

        logger.info(
            "Agent run complete: %s messages, %s tokens",
            len(session.messages),
            all_usage,
        )
        return final_response
