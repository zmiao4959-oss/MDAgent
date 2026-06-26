# Agent 核心引擎

**文件**: `miniclaw/agent.py`

## 核心循环 (`_process_message`)

```python
async def _process_message(context, on_stream_chunk, on_viz_event, on_progress):
    session = await self._resolve_session(context)

    # 1. 构建 system prompt（三层缓存）
    system_prompt = self._build_system_prompt()

    # 2. 计划模式？→ 受限 prompt + 受限工具集
    if plan_mode:
        system_prompt = build_planning_system_prompt(...)
        tools = tool_registry.list_for_llm_exclude(tags=["execution", "shell", ...])
    else:
        tools = self._build_tool_definitions()

    # 3. 主循环
    for round_num in range(1, max_rounds + 1):
        # 上下文压缩检查
        if session.should_compact():
            await self.sessions.compact(session, self.llm)

        # LLM 调用（流式或非流式）
        response = await self._call_llm(messages, tools, stream=bool(on_stream_chunk),
                                        on_stream_chunk=on_stream_chunk)

        if response.content:
            final_response = response.content

        # 无 tool_calls → 结束
        if self._should_stop_loop(response):  # = not response.tool_calls
            session.add_message(LLMMessage(role="assistant", content=response.content))
            break

        # 有 tool_calls → 执行工具
        session.add_message(assistant_msg_with_tool_calls)
        tool_results = await self._handle_tool_calls(response.tool_calls, context)
        for tr in tool_results:
            session.add_message(tr)      # tool result 消息

        # 自动可视化（仅 webchat 渠道）
        if auto_viz:
            await auto_viz.after_tool_round(snap_before, snap_after)

        await self.sessions.save(session)  # 每轮后 checkpoint

    # 4. 达到最大轮数 → 收尾
    if hit_max_rounds:
        recovery = await self._finalize_after_max_rounds(session, ...)
        if recovery: final_response = recovery

    return final_response
```

## LLM 调用 (`_call_llm`)

- **流式模式** (`stream=True`): `llm.chat_stream()` → 逐 chunk 调 `on_stream_chunk` + 累积 `full_content` + 处理增量 tool_calls
- **非流式模式** (`stream=False`): `llm.chat()` → 完整响应，若有 `on_stream_chunk` 则一次性推送

## 工具执行 (`_handle_tool_calls`)

- 并行执行（`asyncio.gather`）
- 单条 tool_call: `_execute_one_tool` → `tool_registry.execute(func_name, arguments, exec_context)`
- **幂等性**: 同一轮内相同工具+相同参数去重
- **LAMMPS 进度上报**: `execute` 工具检测 `run N` 行数 → 逐行解析 thermo 输出 → `on_progress` 推送进度百分比

## 上下文压缩

`session.should_compact()` 检查消息数或 token 数是否超标 → `sessions.compact(session, llm)` 用 LLM 摘要替换中间消息。

触发条件在 `memory/session.py` 中定义。

## 关键回调

Agent 通过三个可选回调与 channel 解耦：

```python
on_stream_chunk(chunk: str)   # LLM 流式文本
on_viz_event(event: dict)     # 可视化事件（status/media/error）
on_progress(data: dict)       # 执行进度（如 LAMMPS 步数）
```

WebChat 将这些回调桥接到 SSE 流（见 [`webchat.md`](webchat.md)）。

## Plan Mode

`context.metadata["plan_mode"] = True` 时启用：
- system prompt 替换为 planning 专用 prompt
- 工具集受限（排除 execution/shell/browser/communication）
- 用于将用户目标分解为可执行步骤
