# Agent 核心引擎

> Agent 主循环已接入 `EvolutionService`：任务结束记录 Trace、积累候选经验，并在后续请求中注入匹配的 verified 策略。`LLMSourcePatchAgent` 可在开关、批准提案和精确确认保护下生成受限测试优先候选；模型不能直接执行 shell 或决定晋升。详见 [`evolution.md`](evolution.md)。

**核心文件**：`miniclaw/agent.py`，以及 `miniclaw/runtime/` 下被它组装的可复用组件。

## 组成组件

`Agent.__init__` 把职责拆到几个协作对象上：

| 组件 | 文件 | 职责 |
|---|---|---|
| `PromptBuilder` | `runtime/prompt_builder.py` | 组装分层 system prompt、生成工具的 function-calling schema |
| `ToolExecutor` | `runtime/tool_executor.py` | 执行工具调用（幂等、hooks、进度、profile 授权） |
| `RunLifecycle` | `runtime/lifecycle.py` | 一次运行的统计 / 钩子 / EvolutionTrace 生命周期 |
| `AgentLoop` | `runtime/agent_loop.py` | 多轮 LLM↔工具循环（平台无关，注入服务） |
| `RequestPreparer` | `runtime/request_preparer.py` | 会话解析、消息入队、选择 prompt/tools/轮次 |
| `LLMRouter` | `llm/router.py` | 多 provider 重试 + 降级 |

## 核心流程 (`_process_message`)

```python
async def _process_message(context, on_stream_chunk, on_viz_event, on_progress):
    # 1. 准备请求：解析会话 → before_agent 钩子 → 入队用户消息 → 选 prompt/tools/轮次
    prepared = await self.request_preparer.prepare(context)
    if prepared.blocked_response is not None:
        return prepared.blocked_response   # before_agent 钩子拦截

    # 2. 运行多轮循环（在 AgentLoop 内部完成）
    loop_result = await self.agent_loop.run(
        session=prepared.session, context=context,
        system_prompt=prepared.system_prompt, tools=prepared.tools,
        max_rounds=prepared.max_rounds, run_state=prepared.run_state,
        lifecycle=self.run_lifecycle,
        on_stream_chunk=on_stream_chunk, on_viz_event=on_viz_event,
    )

    # 3. 收尾：异常时 fail、正常时 finish；落盘 token 统计与 session
    ...
```

`process_message` 只是外层包装：`with workspace_scope(...)` 后进入 `_process_message`。

## 准备阶段 (`RequestPreparer.prepare`)

1. `resolve_session(chat_id)` —— 优先从磁盘恢复会话，避免进程重启丢历史；没有则 `get_or_create`。
2. 触发 `before_agent` 钩子；钩子返回 `prevent=True` 时直接产出 `blocked_response`。
3. `lifecycle.start(context)` —— 建 `RunState`（`RunStats` + `EvolutionTrace` + token 计数）。
4. 用户消息入队：前置 `experience_prefix`（进化经验）+ `memory_prefix`（关键词记忆检索）。
5. 按模式选择：
   - **普通模式**：`prompt_builder.build_system_prompt()` + `build_tool_definitions()`，轮次取 profile 的 `runtime.max_tool_rounds`（缺省用全局 `config.agent.max_tool_rounds`）。
   - **Plan Mode**：`build_planning_system_prompt(...)` + `tool_registry.list_for_llm_exclude(exclude_tags=["execution","shell","browser","communication"])`。

## 提示词 (`PromptBuilder`)

三层结构，Stable / Context 层带缓存指纹：

- **Stable**：`SOUL.md` / `IDENTITY.md` / profile 的 `system_prompt` / 技能摘要（`build_skill_summary`，只列名称+一行描述，最多 12 个）。
- **Context**：`AGENTS.md` / `USER.md` / `MEMORY_FILE`（过长截断到 8000 字符）。
- **Volatile**：每次重建的 `## Runtime Info`（当前时间、活动工作区）。

> **工具 schema 不再写进提示词**。工具的完整 JSON schema（`{"type":"function","function":{...}}`）通过 `tools` 参数随 API 请求单独发送，`PromptBuilder.build_tool_definitions()` → `registry.list_for_llm(allowed_names=profile.tools)` 产出。

## 多轮循环 (`AgentLoop.run`)

```python
messages = [system, *session.messages]
for round_num in range(1, max_rounds + 1):
    if session.should_compact():
        await sessions.compact(session, llm)          # 上下文压缩
    response = await call_llm(messages, tools, stream=...)
    lifecycle.add_usage(run_state, response)          # 累计 token

    if not response.tool_calls:                        # 无工具调用 → 结束
        session.add_message(assistant_msg)
        return LoopResult(response.content, round_num, False)

    session.add_message(assistant_with_tool_calls)
    results = await execute_tools(response.tool_calls, context)   # 并行
    for r in results:
        session.add_message(r)
    await sessions.save(session)                       # 每轮 checkpoint

# 达到轮次上限 → 若仍以 tool 结尾，再请求一次纯文本收尾
recovery = await finalize_after_max_rounds(session, system_prompt, ...)
```

## LLM 调用 (`_call_llm`)

- `before_llm` / `after_llm` 钩子包裹。
- **流式**：`llm.chat_stream()` 逐 chunk 调 `on_stream_chunk`，累积文本 + 增量 tool_calls + DeepSeek `reasoning_content`。
- **非流式**：`llm.chat()`；有 `on_stream_chunk` 时一次性推送。
- 工具以 `tools` 字段传入 `LLMRouter`，由 `OpenAICompatProvider` 序列化进 OpenAI 请求。

## 工具执行 (`ToolExecutor` → `ToolRegistry`)

- `execute_many` 用 `asyncio.gather` 并行，顺序与 tool_calls 一致。
- `execute_one`：解析 JSON 参数 → **幂等去重**（同轮内相同工具+相同参数）→ `before_tool` 钩子（可拦截）→ 注入进度回调 → `registry.execute`。
- `registry.execute`：profile `allowed_tools` 白名单校验 → 未知工具 / 缺必填参数校验 → 按 schema 过滤多余参数 → 执行（sync 走线程池 / async 直接 await，可选超时）→ 结果截断到 32k 字符。

## 生命周期 (`RunLifecycle`) 与钩子

- `start` → 建 `RunState`；`add_usage` → 累计 token。
- `finish`：`after_agent` 钩子 → `trace.finish(success=...)` → `complete_trace`（进化持久化）→ `generate_reflection`（可选深度反思）。
- `fail`：`on_error` 钩子 → `trace.finish(success=False)` → 同样走 `complete_trace` / `generate_reflection`。

## 上下文压缩

`session.should_compact()` 检查消息数或 token 数是否超标 → `sessions.compact(session, llm)` 用 LLM 摘要替换中间消息。触发条件在 `memory/session.py` 中定义。

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
- system prompt 替换为 planning 专用 prompt。
- 工具集受限（排除 execution/shell/browser/communication）。
- 用于将用户目标分解为可执行步骤。

## Skill 与 GPUMD RAG

- `SkillLoader` 启动时扫描工作区技能根目录，只把名称和描述加入 Stable prompt。
- 匹配 GPUMD 脚本、`run.in`、`model.xyz`、热输运、扩散、声子等任务时，Agent 应先调用 `read_skill(name="gpumd-script")`。
- `gpumd-script` 要求在生成或修改输入文件前调用 `search_gpumd_docs`，不得用 LAMMPS 语法猜测 GPUMD 命令。
- `search_gpumd_docs` 在索引、密钥和 RAG 开关均可用时执行向量检索；否则自动降级为关键词检索。
- 中文查询会扩展为常见 GPUMD 英文命令，例如“热导率”扩展到 `compute_hac`、`compute_hnemd`，“扩散”扩展到 `compute_msd`、`compute_sdc`。
- 检索结果会轻量重排并限制同一来源最多占两个结果；普通语法问题优先手册，明确询问示例时提高 Tutorial 权重。

实现与运维细节见 [`rag.md`](rag.md)。
