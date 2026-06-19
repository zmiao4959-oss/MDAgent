"""
agent.py — Agent 主循环（★ 核心引擎）
"""
import json
import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Callable, Awaitable, Tuple

from dataclasses import dataclass, field

from .config import config, WORKSPACE_DIR
from .logger import get_logger
from .llm.base import LLMMessage, LLMResponse, LLMStreamChunk
from .llm.router import LLMRouter
from .tools import ensure_tools_loaded
from .tools.registry import tool_registry
from .memory.session import Session, SessionManager
from .memory.search import keyword_search
from .skills.loader import SkillLoader
from .viz import AutoVisualizer, snapshot_workspace
from .hooks import hook_system, HookContext
from .stats import agent_stats, RunStats

logger = get_logger(__name__)

# 参与系统提示组装的 workspace 文件（用于缓存失效检测）
_SYSTEM_PROMPT_FILES = (
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "MEMORY.md",
)

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

    def __init__(self, llm_router: LLMRouter, session_manager: SessionManager):
        ensure_tools_loaded()
        self.llm = llm_router
        self.sessions = session_manager
        self._skill_loader = SkillLoader()
        self._system_prompt_cache: Optional[str] = None
        self._system_prompt_cache_key: Optional[Tuple[Any, ...]] = None
        self._current_run: Optional[RunStats] = None

    def _system_prompt_fingerprint(self) -> Tuple[Any, ...]:
        """根据文件 mtime 判断系统提示是否需要重建。"""
        parts: List[Any] = []
        for name in _SYSTEM_PROMPT_FILES:
            path = WORKSPACE_DIR / name
            if path.exists():
                stat = path.stat()
                parts.append((name, stat.st_mtime_ns, stat.st_size))
        skills_dir = WORKSPACE_DIR / "skills"
        if skills_dir.exists():
            skill_mtimes = tuple(
                sorted(
                    (p.name, p.stat().st_mtime_ns)
                    for p in skills_dir.iterdir()
                    if p.is_dir() and (p / "SKILL.md").exists()
                )
            )
            parts.append(("skills", skill_mtimes))
        parts.append(("tools", tuple(tool_registry.tool_names())))
        return tuple(parts)

    def _build_system_prompt(self) -> str:
        """组装系统提示 —— 这是整个 Agent 的"灵魂注入"（带文件变更缓存）。"""
        key = self._system_prompt_fingerprint()
        if self._system_prompt_cache is not None and self._system_prompt_cache_key == key:
            static = self._system_prompt_cache
            return f"{static}\n\n## Runtime Info\n- Current time: {datetime.now().isoformat()}\n- Workspace: {WORKSPACE_DIR}"

        self._skill_loader._refresh()
        parts: List[str] = []

        for filename in ("AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md"):
            path = WORKSPACE_DIR / filename
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))

        mem = WORKSPACE_DIR / "MEMORY.md"
        if mem.exists():
            parts.append(f"## Long-term Memory\n{mem.read_text(encoding='utf-8')}")

        parts.append(f"## Available Tools\n{tool_registry.get_descriptions()}")

        skills_list = self._skill_loader.list_all()
        if skills_list:
            parts.append("<available_skills>")
            for s in skills_list:
                parts.append("  <skill>")
                parts.append(f"    <name>{s.name}</name>")
                parts.append(f"    <description>{s.description}</description>")
                parts.append(f"    <location>{s.location}</location>")
                parts.append("  </skill>")
            parts.append("</available_skills>")

        static = "\n\n".join(parts)
        self._system_prompt_cache = static
        self._system_prompt_cache_key = key
        return f"{static}\n\n## Runtime Info\n- Current time: {datetime.now().isoformat()}\n- Workspace: {WORKSPACE_DIR}"

    def _build_tool_definitions(self) -> List[Dict]:
        """生成 OpenAI function-calling 格式的工具定义"""
        return tool_registry.list_for_llm()

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

    async def _execute_one_tool(
        self, tc: Dict, context: AgentContext
    ) -> LLMMessage:
        func_name = tc["function"]["name"]
        try:
            arguments = json.loads(tc["function"]["arguments"])
        except json.JSONDecodeError:
            return LLMMessage(
                role="tool",
                tool_call_id=tc["id"],
                content=f"Error: Invalid JSON arguments: {tc['function']['arguments']}",
            )

        # ── 幂等性追踪: 同一轮中相同工具+相同参数跳过重复执行 ──
        idem_key = context.metadata.setdefault("_tool_idem_keys", set())
        arg_fp = (func_name, json.dumps(arguments, sort_keys=True, default=str))
        if arg_fp in idem_key:
            logger.info("Skipping duplicate tool call: %s", func_name)
            return LLMMessage(
                role="tool",
                tool_call_id=tc["id"],
                content="[Skipped: duplicate tool call with identical arguments in this round]",
                name=func_name,
            )
        idem_key.add(arg_fp)

        # ── before_tool hook (可阻止工具执行) ──
        hook_ctx = await hook_system.fire("before_tool",
            chat_id=context.chat_id,
            channel=context.channel,
            data={"tool_name": func_name, "arguments": arguments},
        )
        if hook_ctx.prevent:
            return LLMMessage(
                role="tool",
                tool_call_id=tc["id"],
                content=f"[Tool '{func_name}' blocked by hook: {hook_ctx.prevent_reason}]",
                name=func_name,
            )

        # 为 execute 工具注入 stdout 逐行回调，用于 LAMMPS 进度上报
        on_progress: Optional[ProgressCallback] = context.metadata.get("_on_progress")
        if on_progress and func_name == "execute":
            loop = asyncio.get_running_loop()
            cmd_text: str = arguments.get("command", "")

            # 尝试从 LAMMPS input 文件解析总步数 (run N)
            total: Optional[int] = None
            m_in = re.search(r'(?:^|\s)-in\s+(?:"([^"]+)"|(\S+))', cmd_text)
            if m_in:
                in_path = m_in.group(1) or m_in.group(2)
                try:
                    p = Path(in_path)
                    if not p.is_absolute():
                        wd = arguments.get("working_dir", "")
                        base = Path(wd) if wd else WORKSPACE_DIR
                        if not base.is_absolute():
                            base = WORKSPACE_DIR / base
                        p = base / in_path
                    p = p.resolve()
                    if p.exists():
                        content = p.read_text(encoding="utf-8", errors="replace")
                        runs = re.findall(
                            r"^run\s+(\d+)", content, re.MULTILINE | re.IGNORECASE
                        )
                        if runs:
                            total = sum(int(n) for n in runs)
                            logger.debug("LAMMPS total steps: %s (from %s run(s))", total, len(runs))
                except Exception:
                    pass

            def _on_line(line: str) -> None:
                """在 stdout 读取线程中调用；检测 LAMMPS thermo 行并推送进度。"""
                stripped = line.strip()
                parts = stripped.split()
                if len(parts) < 3:
                    return
                # LAMMPS thermo 行：第一个 token 是正整数步数
                if not parts[0].isdigit():
                    return
                step = int(parts[0])
                data: Dict[str, Any] = {"current": step}
                if total is not None:
                    data["total"] = total
                try:
                    asyncio.run_coroutine_threadsafe(on_progress(data), loop)
                except Exception:
                    pass

            arguments["_on_line"] = _on_line

        exec_context = {
            "chat_id": context.chat_id,
            "channel": context.channel,
            "account_id": context.account_id,
            "approved": context.metadata.get("approved", False),
        }
        logger.info("Executing tool: %s(%s)", func_name, arguments)
        result = await tool_registry.execute(func_name, arguments, exec_context)

        # ── after_tool hook ──
        await hook_system.fire("after_tool",
            chat_id=context.chat_id,
            channel=context.channel,
            data={"tool_name": func_name, "arguments": arguments, "result": result},
        )

        return LLMMessage(
            role="tool",
            tool_call_id=tc["id"],
            content=result,
            name=func_name,
        )

    async def _handle_tool_calls(
        self, tool_calls: List[Dict], context: AgentContext
    ) -> List[LLMMessage]:
        """并行执行工具调用，返回工具结果消息列表（顺序与 tool_calls 一致）。"""
        if not tool_calls:
            return []
        if len(tool_calls) == 1:
            return [await self._execute_one_tool(tool_calls[0], context)]
        return list(
            await asyncio.gather(
                *[self._execute_one_tool(tc, context) for tc in tool_calls]
            )
        )

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
                    for tc_delta in chunk.delta_tool_calls:
                        idx = tc_delta.get("index", 0)
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "id": tc_delta.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        acc = accumulated_tool_calls[idx]
                        if tc_delta.get("id"):
                            acc["id"] = tc_delta["id"]
                        fn = tc_delta.get("function") or {}
                        if fn.get("name"):
                            acc["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            acc["function"]["arguments"] += fn["arguments"]

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
        session = await self._resolve_session(context)

        # ── Stats tracking ──
        self._current_run = agent_stats.start_run(context.chat_id)

        # ── before_agent hook ──
        hook_ctx = await hook_system.fire("before_agent",
            chat_id=context.chat_id,
            channel=context.channel,
            account_id=context.account_id,
            data={"message": context.user_message},
        )
        if hook_ctx.prevent:
            return f"[Agent blocked: {hook_ctx.prevent_reason}]"

        if context.user_message:
            prefix = self._memory_prefix(context.user_message)
            full_content = prefix + context.user_message if prefix else context.user_message
            session.add_message(LLMMessage(role="user", content=full_content))

        system_prompt = self._build_system_prompt()
        messages = [LLMMessage(role="system", content=system_prompt)] + session.messages
        tools = self._build_tool_definitions()

        max_rounds = config.agent.max_tool_rounds
        final_response = ""
        all_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        hit_max_rounds = False
        pending_viz: List[asyncio.Task] = []
        context.metadata["viz_tasks"] = pending_viz
        auto_viz: Optional[AutoVisualizer] = None
        if context.channel == "webchat" and on_viz_event:
            auto_viz = AutoVisualizer(session, on_viz_event, pending_viz)

        # 追踪本次运行的 token 用量（记录到 session metadata）
        session.metadata.setdefault("total_tokens", 0)

        try:
            for round_num in range(1, max_rounds + 1):
                if session.should_compact():
                    logger.info(
                        "Compacting session %s before round %s",
                        session.session_id,
                        round_num,
                    )
                    await hook_system.fire("on_compaction",
                        chat_id=context.chat_id,
                        data={"session_id": session.session_id, "round": round_num},
                    )
                    await self.sessions.compact(session, self.llm)
                    messages = [
                        LLMMessage(role="system", content=system_prompt),
                        *session.messages,
                    ]

                # 所有轮次都支持流式输出（首轮文本流式，后续轮工具调用也尽量流式）
                should_stream = bool(on_stream_chunk)
                response = await self._call_llm(
                    messages,
                    tools,
                    stream=should_stream,
                    on_stream_chunk=on_stream_chunk,
                )

                all_usage["prompt_tokens"] += response.usage.get("prompt_tokens", 0)
                all_usage["completion_tokens"] += response.usage.get("completion_tokens", 0)
                round_tokens = response.usage.get("prompt_tokens", 0) + response.usage.get("completion_tokens", 0)
                session.metadata["total_tokens"] = session.metadata.get("total_tokens", 0) + round_tokens

                if response.content:
                    final_response = response.content

                if self._should_stop_loop(response):
                    if response.content:
                        session.add_message(
                            LLMMessage(
                                role="assistant",
                                content=response.content,
                                reasoning_content=response.reasoning_content,
                            )
                        )
                    break

                assistant_msg = LLMMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                    reasoning_content=response.reasoning_content,
                )
                session.add_message(assistant_msg)
                messages.append(assistant_msg)

                snap_before = snapshot_workspace() if auto_viz else {}
                tool_results = await self._handle_tool_calls(
                    response.tool_calls, context
                )
                for tr in tool_results:
                    session.add_message(tr)
                    messages.append(tr)

                if auto_viz:
                    snap_after = snapshot_workspace()
                    await auto_viz.after_tool_round(snap_before, snap_after)

                # ── 每轮后保存 session (checkpoint recovery) ──
                await self.sessions.save(session)

                logger.info(
                    "Round %s: executed %s tool(s), tokens=%s",
                    round_num,
                    len(response.tool_calls),
                    round_tokens,
                )
            else:
                hit_max_rounds = True

            if hit_max_rounds:
                recovery = await self._finalize_after_max_rounds(
                    session, system_prompt, on_stream_chunk
                )
                if recovery:
                    final_response = recovery

        except Exception:
            logger.exception("Agent loop failed for chat_id=%s", context.chat_id)
            # ── on_error hook ──
            import sys
            err_msg = str(sys.exc_info()[1])
            await hook_system.fire("on_error",
                chat_id=context.chat_id,
                channel=context.channel,
                data={"error": err_msg, "round": round_num if 'round_num' in dir() else 0},
            )
            # ── Record error stats ──
            if self._current_run:
                self._current_run.error = err_msg
                agent_stats.end_run(self._current_run)
                self._current_run = None
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

        # ── after_agent hook ──
        await hook_system.fire("after_agent",
            chat_id=context.chat_id,
            channel=context.channel,
            data={
                "rounds": round_num if 'round_num' in dir() else 0,
                "final_response_length": len(final_response),
                "total_tokens": all_usage,
                "session_id": session.session_id,
            },
        )

        # ── Record stats ──
        if self._current_run:
            self._current_run.prompt_tokens = all_usage["prompt_tokens"]
            self._current_run.completion_tokens = all_usage["completion_tokens"]
            self._current_run.rounds = round_num if 'round_num' in dir() else 0
            agent_stats.end_run(self._current_run)
            self._current_run = None

        logger.info(
            "Agent run complete: %s messages, %s tokens",
            len(session.messages),
            all_usage,
        )
        return final_response
