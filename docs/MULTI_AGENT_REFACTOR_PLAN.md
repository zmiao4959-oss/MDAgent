# MiniClaw 多智能体重构计划

## 1. 目标

将当前单体 `Agent` 演进为声明式多智能体系统：

```text
Agent = 共享 AgentRuntime + AgentProfile + 隔离 RunContext
```

- Coordinator 是默认用户入口，负责理解目标、委派任务和汇总结果。
- 所有子 Agent 共用 LLM Router、工具执行、安全策略、会话、Hook、Trace 和进化基础设施。
- Agent 之间只通过 Prompt、Skills、Tools 和少量可选运行参数产生差异。
- 新增或修改 Agent 不需要编写 Python 类，只需增加一个 Profile 目录。

## 2. 非目标

第一阶段不做以下事情：

- 不引入新的工作流框架或外部消息队列。
- 不重写现有 LLM/Tool 循环。
- 不删除自我进化系统。
- 不强制所有请求进入多 Agent；Coordinator 可以直接处理简单任务。
- 不让子 Agent 共享可变消息历史。

## 3. 目标架构

```text
WebChat / Gateway / Cron
          |
          v
   Coordinator Profile
          |
          v
  delegation tools
          |
          v
   SubAgentManager
      |         |
      v         v
Agent(Profile A) Agent(Profile B)
      \         /
       shared LLM Router
       shared ToolRegistry
       isolated session/workspace
```

### 3.1 AgentProfile

Profile 声明：

- `name`：稳定标识。
- `description`：供 Coordinator 发现和选择。
- `prompt`：角色专属系统提示。
- `skills`：允许发现和读取的 Skill 名称。
- `tools`：允许暴露和执行的 Tool 名称。
- `runtime`：可选的轮次、超时、模型等覆盖项。

`"*"` 表示继承全部能力。默认采用最小权限：未声明即为空。

### 3.2 AgentCatalog

Catalog 扫描两个位置：

1. 包内 `miniclaw/agents/profiles/`，提供内置 Agent。
2. 工作区 `agents/`，提供用户自定义 Agent；同名 Profile 覆盖内置版本。

Profile 文件或提示词变化后可刷新，无需修改业务代码。

### 3.3 能力隔离

能力隔离必须同时发生在两层：

1. 构建 LLM Tool schemas 和 Skill 摘要时过滤，减少上下文噪声。
2. 工具真正执行和 `read_skill` 时再次校验，避免只靠 Prompt 约束。

### 3.4 运行隔离

- 每个子任务创建独立 `Agent` 实例，避免共享 Prompt 缓存和运行状态。
- 每个任务使用独立 `chat_id` 和 Session。
- 沙箱任务通过 `workspace_scope` 真正切换工作区。
- 任务状态由 `SubAgentManager` 统一管理，可查询、等待和取消。

## 4. 内置 Profile

第一阶段提供：

- `coordinator`：用户入口；拥有委派、查询、取消子任务及少量只读工具。
- `general-worker`：通用执行 Agent；继承现有 Skills，保留现有业务工具，但不能继续委派。
- `researcher`：只读资料检索和 GPUMD RAG。
- `reviewer`：读取、检索、系统检查和结果审阅，不修改正式文件。

领域 Agent（如 `lammps-engineer`）后续可完全通过用户 Profile 增加。

## 5. 迁移步骤

### 阶段 A：Profile 基础设施

- 实现 `AgentProfile` 和 `AgentCatalog`。
- 加入内置 Profile。
- 增加 Profile 解析和覆盖测试。

### 阶段 B：共享运行时能力过滤

- `Agent` 接受可选 Profile。
- Prompt、Tool schemas、Skill 摘要按 Profile 过滤。
- ToolRegistry 和 `read_skill` 在执行阶段强制校验。
- 无 Profile 的旧调用保持完整能力，保证向后兼容。

### 阶段 C：Coordinator 与子 Agent

- `SubAgentManager.spawn()` 接受 Agent Profile 名称。
- 子任务创建独立 Agent 实例。
- 注册 `list_agents`、`delegate_task`、`inspect_task`、`wait_task`、`cancel_task`。
- `main.py` 使用 Coordinator Profile，并注册管理器。

### 阶段 D：继续瘦身

在行为稳定后，从 `agent.py` 依次抽出：

- `PromptBuilder`（已完成）
- `ToolExecutor`（已完成）
- `AgentLoop`（已完成）
- `RunLifecycle`（已完成）
- `RequestPreparer`（已完成）

每次只移动职责，不改变行为，持续保持完整测试通过。

## 6. 验收标准

- 原有测试全部通过。
- 新鲜环境能解析仓库默认配置。
- 默认入口加载 Coordinator Profile。
- Coordinator 能发现可用 Agent。
- 子 Agent 只能看到并执行 Profile 允许的 Tools。
- 子 Agent 只能发现并读取 Profile 允许的 Skills。
- 两个子任务并发时使用不同 Session 和独立 Agent 实例。
- 沙箱任务的文件操作真实落在沙箱内。
- 自定义 Profile 无需修改 Python 代码即可被 Catalog 发现。
- WebChat 能通过 SSE 实时展示多个并行子任务的状态。

## 7. 回滚策略

- `Agent(profile=None)` 保留原单 Agent 行为。
- `main.py` 可通过不传 Profile 回退为旧入口。
- Profile 与调度代码均为新增模块，核心循环采用渐进式接入。
- 每一阶段单独提交，出现问题可按阶段回退。

## 8. 配置与可移植性（已完成）

- `settings.py` 是唯一内部配置入口，`config.py` 为兼容门面。
- `AppPaths` 统一管理 home、workspace、sessions、skills、agents、RAG、日志和临时目录。
- 支持 `MINICLAW_HOME`、`MINICLAW_WORKSPACE`、`MINICLAW_CONFIG`。
- WebChat、默认 Agent Profile、多智能体并发度和调度参数由 `config.yaml` 管理。
- 内部模块不再直接导入兼容配置模块。
