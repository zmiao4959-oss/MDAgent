# MiniClaw 项目交接

> 自我进化系统已经完成阶段 1–23，科研胶囊阶段 24–25 已完成，并已接入 MDSynth LAMMPS 编译器。声明式多智能体、WebChat 并行任务状态和统一可移植配置已经完成。完整计划见 [`../docs/MULTI_AGENT_REFACTOR_PLAN.md`](../docs/MULTI_AGENT_REFACTOR_PLAN.md)。当前测试基线为 175 项通过；自动源码补丁默认关闭，必须显式配置源码 Git 仓库并启用开关。

AI Agent 工作台 — 多轮工具调用 Agent，面向科学计算（LAMMPS/GPUMD 分子动力学、OVITO 可视化、领域知识检索），多渠道接入（WebChat / Telegram / WebSocket）。

## 速览

| 项目 | 说明 |
|------|------|
| 语言 | Python 3.11+ (FastAPI + asyncio) / 原生 JS (无框架) |
| 入口 | 在仓库根目录运行 `python main.py` |
| WebChat | `http://127.0.0.1:8000` |
| Gateway | `ws://127.0.0.1:8765` |
| 配置 | `miniclaw/config.yaml` |
| 工作区 | `miniclaw/` 上级目录（`WORKSPACE_DIR`） |

## 文档索引

按需阅读，不必全看：

| 文档 | 何时读 |
|------|--------|
| [`architecture.md`](architecture.md) | 需要理解整体架构、组件关系、数据流 |
| [`webchat.md`](webchat.md) | 修改 WebChat 界面、SSE 流、前后端交互 |
| [`agent.md`](agent.md) | 修改 Agent 循环、工具执行、LLM 调用 |
| [`rag.md`](rag.md) | 修改 GPUMD 知识库、文档同步、embedding 与增量索引 |
| [`viz.md`](viz.md) | 修改可视化（OVITO GIF、3D 结构、新文件检测） |
| [`frontend.md`](frontend.md) | 修改 app.js、evolution.js，前端状态与 localStorage |
| [`api.md`](api.md) | 查阅 API 端点定义 |
| [`bugs.md`](bugs.md) | 理解近期修过的 Bug 和改动的上下文 |
| [`research-capsules.md`](research-capsules.md) | 修改科研胶囊、文件清单、验证或重放能力 |
| [`lammps-script-generation.md`](lammps-script-generation.md) | 修改 MDSynth 桥接、LAMMPS 编译 Tool 或内置 Skill |

## 启动

```bash
# 先进入包含 main.py 和 requirements.txt 的仓库根目录
cd openclaw-study
python -m pip install -r requirements.txt
python main.py
```

## 目录结构（仅关键文件）

```
openclaw-study/
├── main.py                    # 启动入口
├── requirements.txt           # 项目依赖
└── miniclaw/
    ├── config.yaml            # 全局配置
    ├── agent.py               # Agent 核心引擎
    ├── hooks.py               # 生命周期 Hook
    ├── planning.py            # 计划模式
    ├── projects.py            # 多项目管理
    ├── stats.py               # 运行统计
    ├── llm/
    │   ├── base.py            # 数据结构
    │   ├── router.py          # Provider 路由
    │   └── openai_compat.py   # OpenAI 兼容 + SSE
    ├── channels/
    │   ├── webchat.py         # 向后兼容导出
    │   ├── webchat_adapter.py # WebChat 服务组合与服务器生命周期
    │   ├── telegram.py
    │   └── web/
    │       ├── files.py       # 文件浏览 API
    │       ├── chat_routes.py # 对话 SSE 入口
    │       ├── chat_run.py    # 单次 Agent 请求编排
    │       ├── chat_events.py # SSE 事件流
    │       ├── project_routes.py # 项目生命周期 API
    │       ├── project_task_routes.py # 项目计划与任务 API
    │       ├── project_run.py # 项目后台执行、并发与重试
    │       ├── project_workspace.py # 项目工作区与产物视图
    │       ├── knowledge_service.py # GPUMD 知识库任务状态机
    │       ├── knowledge_routes.py # GPUMD 知识库 API
    │       ├── diagnostics.py # 本地运行能力诊断
    │       ├── system_routes.py # 配置、诊断和统计 API
    │       ├── conversation_service.py # 会话生命周期与导出
    │       ├── conversation_routes.py # 会话管理 API
    │       ├── file_routes.py # 项目范围文件 API
    │       ├── web_app.py # FastAPI 应用工厂与统一路由注册
    │       ├── evolution_routes.py # 进化管理 API
    │       └── static/
    │           ├── index.html
    │           ├── app.js     # 对话、项目和可视化主逻辑
    │           ├── evolution.js # 经验与反思管理面板
    │           └── style.css
    ├── tools/
    │   ├── registry.py
    │   ├── rag_tool.py        # GPUMD 文档检索工具
    │   └── paths.py           # 工作区路径安全解析
    ├── rag/
    │   ├── embedding.py       # OpenAI/火山多模态 embedding 客户端
    │   ├── store.py           # JSON 向量索引、检索、增量构建
    │   ├── cli.py             # 向量索引构建命令
    │   └── sync_gpumd.py      # 官方手册与 Tutorials 同步器
    ├── skills/
    │   └── loader.py          # 标准 YAML + 旧 XML Skill 加载
    ├── memory/
    │   ├── session.py
    │   └── session_store.py
    ├── viz/
    │   ├── auto.py            # 自动可视化调度
    │   ├── ovito_render.py    # OVITO 子进程渲染
    │   ├── snapshot.py        # 文件快照 + diff
    │   └── constants.py
    └── gateway/server.py      # WebSocket Gateway
```

## GPUMD RAG 速查

```powershell
# 同步 GPUMD 5.5 官方手册和 Tutorials 语料
python -m miniclaw.rag.sync_gpumd

# 默认增量更新向量索引
python -m miniclaw.rag.cli build

# 更换模型或需要彻底重建时
python -m miniclaw.rag.cli build --full
```

当前语料为 703 块，向量模型为 `doubao-embedding-vision-251215`，实际向量维度 2048。完整运维与实现说明见 [`rag.md`](rag.md)。
