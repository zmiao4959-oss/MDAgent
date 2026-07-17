# 架构

> `miniclaw/learning/` 现在包含从 Trace、经验、语义策略、Skill、可执行策略到源代码候选分支的阶段 1–23 自我进化闭环。验证经验全局共享、原始 Trace 项目本地；源码补丁只在独立 Git worktree 中生成。进化路由和前端面板已拆成独立模块。详见 [`evolution.md`](evolution.md)。

## 组件关系

```
main.py
├── LLMRouter           → llm/router.py     多 Provider 路由 + fallback
├── SessionManager      → memory/session.py  对话持久化
├── Agent               → agent.py          核心循环（LLM + 工具执行）
├── SkillLoader         → skills/loader.py  技能发现与渐进加载
├── GPUMD RAG           → rag/ + rag_tool.py 官方知识库与向量检索
├── GatewayServer       → gateway/server.py  WebSocket 协议
├── WebChatAdapter      → channels/webchat.py FastAPI + SSE
├── TelegramAdapter     → channels/telegram.py
├── HeartbeatLoop       → heartbeat.py      定时心跳
├── CronScheduler       → cron_scheduler.py Cron 任务
└── SubAgentManager     → subagent.py       子 Agent
```

## GPUMD RAG 数据流

```
gpumd.org sitemap + GPUMD-Tutorials ZIP
    └─► rag/sync_gpumd.py
          ├─ 修复稳定版 sitemap URL
          ├─ 提取正文、标题、代码和表格
          ├─ 按章节切块（最大约 2800 字符，重叠 240）
          └─► ~/.miniclaw/workspace/skills/gpumd-script/references/corpus.jsonl
                  └─► rag/cli.py + rag/store.py
                        ├─ ID + 内容哈希 + 模型名增量判断
                        ├─ rag/embedding.py 调用远程 embedding
                        └─► ~/.miniclaw/workspace/rag/gpumd-script/index.json
                                └─► tools/rag_tool.py
                                      └─► Agent 工具 search_gpumd_docs
```

索引为 JSON v2 格式，写入时先生成同目录临时文件，再用 `os.replace` 原子替换。构建失败不会破坏旧索引。详见 [`rag.md`](rag.md)。

## 一条消息的完整链路

```
浏览器                    FastAPI (webchat.py)           Agent (agent.py)          LLM
──────                    ───────────────────           ────────────────          ───

POST /api/chat
  {message, chat_id}
  └─► event_stream()
        ├─ create_task(run_agent())
        │    ├─ agent.process_message()
        │    │    ├─ 构建 system prompt（三层缓存）
        │    │    ├─ for round in 1..max_rounds:
        │    │    │    ├─ _call_llm(stream=True)
        │    │    │    │    └─ on_stream_chunk(text) ──► out_q ──► SSE: delta ──► 浏览器渲染
        │    │    │    ├─ 有 tool_calls？ → _handle_tool_calls()
        │    │    │    │    ├─ 并行执行工具
        │    │    │    │    ├─ session.add_message(tool_result)
        │    │    │    │    └─ auto_viz.after_tool_round()
        │    │    │    │         └─ 检测新文件 → create_task(render)
        │    │    │    └─ 无 tool_calls？ → break + return final_response
        │    │    └─ return final_response
        │    ├─ finally: asyncio.gather(viz_tasks)
        │    │    └─ viz task: _emit_media → out_q → SSE: viz ──► 浏览器展示 GIF
        │    └─ out_q.put(None) → 终止 while 循环
        └─ yield SSE: done ──► 浏览器收尾渲染
```

## 关键时序

1. `agent.process_message()` 返回时，viz 渲染任务**可能还在跑**（`asyncio.create_task` 创建的）
2. `run_agent()` 的 `finally` 块 `asyncio.gather(*pending)` **等待所有 viz 完成后**才 put `None`
3. 如果 SSE 在等待期间断开（10s 超时 + `is_disconnected()`），`done` 事件不会发出
4. **兜底**: 客户端 `!receivedDone` 时自动调 `/api/history` 恢复文本 + `viz_done` 恢复 GIF

## 三层上下文工程

Agent 的 system prompt 分三层构建（`agent.py:64-172`）：

| 层 | 来源 | 缓存策略 |
|----|------|---------|
| **Stable** | `SOUL.md` + `IDENTITY.md` + 工具描述 + 技能列表 | 文件指纹缓存（极少重建） |
| **Context** | `AGENTS.md` + `USER.md` + `MEMORY.md` | 文件指纹缓存 |
| **Volatile** | 当前时间、工作区路径 | 每轮动态注入 |

技能列表位于 Stable 层，只注入名称和短描述。Agent 需要使用某个 Skill 时调用 `read_skill` 加载完整 `SKILL.md`。`SkillLoader` 同时兼容标准 YAML frontmatter 和项目历史遗留的 `<name>/<description>` 标签格式。
