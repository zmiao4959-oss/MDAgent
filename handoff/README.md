# MiniClaw 项目交接

AI Agent 工作台 — 多轮工具调用 Agent，面向科学计算（LAMMPS 分子动力学 + OVITO 可视化），多渠道接入（WebChat / Telegram / WebSocket）。

## 速览

| 项目 | 说明 |
|------|------|
| 语言 | Python 3.11+ (FastAPI + asyncio) / 原生 JS (无框架) |
| 入口 | `python main.py` |
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
| [`viz.md`](viz.md) | 修改可视化（OVITO GIF、3D 结构、新文件检测） |
| [`frontend.md`](frontend.md) | 修改 app.js（~1700 行），前端状态、localStorage |
| [`api.md`](api.md) | 查阅 API 端点定义 |
| [`bugs.md`](bugs.md) | 理解近期修过的 Bug 和改动的上下文 |

## 启动

```bash
cd miniclaw
pip install -r requirements.txt
python main.py
```

## 目录结构（仅关键文件）

```
miniclaw/
├── main.py                    # 启动入口
├── config.yaml                # 全局配置
├── agent.py                   # Agent 核心引擎
├── hooks.py                   # 生命周期 Hook
├── planning.py                # 计划模式
├── projects.py                # 多项目管理
├── stats.py                   # 运行统计
├── llm/
│   ├── base.py                # 数据结构
│   ├── router.py              # Provider 路由
│   └── openai_compat.py       # OpenAI 兼容 + SSE
├── channels/
│   ├── webchat.py             # WebChat FastAPI
│   ├── telegram.py
│   └── web/
│       ├── files.py           # 文件浏览 API
│       └── static/
│           ├── index.html
│           ├── app.js         # 前端全部逻辑
│           └── style.css
├── tools/
│   ├── registry.py
│   └── paths.py               # 工作区路径安全解析
├── memory/
│   ├── session.py
│   └── session_store.py
├── viz/
│   ├── auto.py                # 自动可视化调度
│   ├── ovito_render.py        # OVITO 子进程渲染
│   ├── snapshot.py            # 文件快照 + diff
│   └── constants.py
└── gateway/server.py          # WebSocket Gateway
```
