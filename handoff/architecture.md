# 架构

> `miniclaw/learning/` 现在包含从 Trace、经验、语义策略、Skill、可执行策略到源代码候选分支的阶段 1–23 自我进化闭环。验证经验全局共享、原始 Trace 项目本地；源码补丁只在独立 Git worktree 中生成。进化路由和前端面板已拆成独立模块。详见 [`evolution.md`](evolution.md)。

## 组件关系

```
main.py
├── LLMRouter           → llm/router.py     多 Provider 路由 + fallback
├── SessionManager      → memory/session.py  对话持久化
├── Coordinator         → agents/profiles/  默认主 Agent
├── AgentCatalog        → agents/catalog.py 声明式 Profile 发现
├── Agent               → agent.py          共享循环（LLM + 工具执行）
├── SkillLoader         → skills/loader.py  技能发现与渐进加载
├── GPUMD RAG           → rag/ + rag_tool.py 官方知识库与向量检索
├── ResearchCapsule     → research_capsule.py 科研证据、文件哈希与复现清单
├── GatewayServer       → gateway/server.py  WebSocket 协议
├── WebChatAdapter      → channels/webchat_adapter.py 服务组合与生命周期
├── TelegramAdapter     → channels/telegram.py
├── HeartbeatLoop       → heartbeat.py      定时心跳
├── CronScheduler       → cron_scheduler.py Cron 任务
└── SubAgentManager     → subagent.py       子 Agent
```

## 声明式多智能体

默认入口加载 `coordinator` Profile。Coordinator 通过 `list_agents`、
`list_tasks`、`delegate_task`、`inspect_task`、`wait_task` 和 `cancel_task`
管理子任务。
每个子任务创建独立 `Agent` 实例和 Session，但复用 LLM Router、ToolRegistry、
Hook、Trace 与安全基础设施。

`delegate_task` 返回结构化回执，其中 `task_id` 是必须原样传递的不透明标识符。
Manager 以父会话、Profile、目标、模型和沙箱设置生成幂等键；等价重复委派复用
原任务，只有显式 `force_new=true` 才创建新任务。查询失败不会被解释为委派失败，
Coordinator 必须先从原回执恢复完整 ID，再通过当前父会话隔离的 `list_tasks`
按 ID、幂等键、Agent 或目标进行对账，禁止因此自动重复派发。

内置 Profile 位于 `miniclaw/agents/profiles/`；用户可在工作区 `agents/`
下放置同结构目录，自定义或覆盖同名 Profile。Profile 的工具和 Skill 白名单
既用于过滤提示上下文，也在实际执行阶段再次校验。

子任务状态按 `pending → running → done/error` 生成结构化事件。WebChat 将事件
通过当前聊天的 SSE 流转发，前端“协作任务”面板按任务 ID 增量更新，因此多个
并行 Agent 可以同时显示各自的角色、目标和状态。

WebChat 的 HTTP 边界位于 `miniclaw/channels/web/`：`chat_routes.py` 只负责
解析请求和建立 SSE 响应，`chat_run.py` 驱动一次 Agent 请求，`chat_events.py`
统一编码流式事件；`project_routes.py` 管理项目生命周期和报告，
`project_task_routes.py` 管理计划任务接口。`ProjectRunService` 负责后台任务、
并发限制、重试、状态和摘要，`ProjectWorkspaceService` 负责项目目录、产物列表、
会话预览和旧对话初始化。`KnowledgeService` 独立维护 GPUMD 语料同步、索引构建
和任务状态，`diagnostics.py` 提供无敏感信息的本地运行能力探测；
`knowledge_routes.py` 与 `system_routes.py` 暴露相应接口。会话生命周期、历史、
分叉、导出与消息投递由 `ConversationService` 负责，文件访问由
`file_routes.py` 按项目工作区限定。`web_app.py` 是唯一 FastAPI 应用工厂，
集中完成中间件、静态资源与所有路由注册；`webchat_adapter.py` 只组合服务并
管理服务器生命周期，`webchat.py` 仅保留向后兼容导出。

共享运行时组件位于 `miniclaw/runtime/`。`PromptBuilder` 负责 Profile 感知的
三层 Prompt、Skill 摘要和 Tool schemas；`ToolExecutor` 负责幂等、Hook、
进度上报和 Profile 权限校验；`RunLifecycle` 负责 Hook、Trace、统计和进化
收尾；`AgentLoop` 负责多轮 LLM、Tool、Session checkpoint、上下文压缩和自动
可视化；`RequestPreparer` 负责 Session、before hook、记忆/经验注入、规划
模式和运行参数。`Agent` 现在主要负责组装组件和保留兼容入口。

## 配置与可移植路径

`miniclaw/settings.py` 是配置结构、默认值、YAML、环境变量、路径解析和校验的
唯一内部入口；`miniclaw/config.py` 只保留向后兼容导出。所有运行目录由
`AppPaths` 从 `MINICLAW_HOME` 派生，并允许 `MINICLAW_WORKSPACE` 覆盖。
配置优先级为代码默认值、`config.yaml`、环境变量、显式配置路径。WebChat、
默认 Coordinator、并发度和晨间调度均已从 `main.py` 移入 `config.yaml`。

## 科研胶囊数据流

```text
项目元数据 + Session 统计 + 最近 EvolutionTrace + 项目工作区
    └─► research_capsule.py
          ├─ 排除运行状态、凭据文件、符号链接和路径逃逸
          ├─ 对普通文件流式计算 SHA-256
          ├─ 记录环境、MiniClaw Git 状态和脱敏工具轨迹
          └─► <project>/.miniclaw/research-capsules/capsule_<id>/
                  ├─ capsule.json
                  ├─ README.md
                  ├─ verification.json
                  └─ exports/*.zip
```

HTTP 入口位于 `channels/web/capsule_routes.py`。捕获、核验和导出在线程中执行，避免文件散列阻塞 FastAPI 事件循环；正在运行或仍使用全局共享工作区的项目会被拒绝。实际文件导出先复核清单，再对 UTF-8 文本二次脱敏，并用 `export.json` 记录派生文件哈希。阶段 26 将在此模型上增加隔离重放。

## MDSynth 编译器桥接

```text
用户 MD 目标
  └─► 内置 Skill: lammps-script-gen
        └─► Tool: generate_lammps_script（高风险、需审批）
              └─► 最小环境的 Python 子进程
                    └─► 外部 MDSynth: Intent → IR → Validator → Compiler
                          └─► 当前项目/generated/... 证据包
```

桥接位于 `tools/lammps_script_tool.py`，子进程入口位于 `integrations/mdsynth_runner.py`。MDSynth 源码不复制进 MiniClaw；默认读取用户指定的独立仓库，也可通过 `MDSYNTH_PROJECT_DIR` 切换。模板外直接生成和 LAMMPS 预检默认关闭，并需要精确确认。

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
浏览器                 chat_routes / chat_run       Agent / runtime             LLM
──────                 ───────────────────────       ───────────────             ───

POST /api/chat
  {message, chat_id}
  └─► ChatEventStream
        ├─ create_task(ChatRunService.run())
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
