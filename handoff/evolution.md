# 自我进化系统交接

本文记录阶段 1–23 完成的整套受控自我进化能力。它不只是向 prompt 注入经验，而是包含运行轨迹、证据晋升、跨项目共享、结构化策略、Skill、受限可执行策略，以及源代码候选分支的完整闭环。

## 当前状态

- 阶段 1–23 已实现并通过验收。
- 全量测试基线：`146 passed`。
- `scripts/acceptance_stage1.py` 至 `scripts/acceptance_stage23.py` 全部通过。
- 前端 `app.js`、`evolution.js` 语法检查与 `git diff --check` 通过。
- 关键 Git 节点：`fab9fbb`（阶段 1–6）、`e9dfeb4`（阶段 7–14）、`9dd65a9`（阶段 15）、`bef8bcb`（阶段 16）、`58686e3`（阶段 17）、`041530e`（阶段 18）、`8cc1087`（阶段 19）、`f321250`（阶段 20）。
- 源代码进化计划：`docs/SOURCE_EVOLUTION_SINGLE_CANDIDATE_PLAN.md`，计划提交为 `be8e326`。
- 平台化加固任务书：`docs/EVOLUTION_PLATFORM_HARDENING_PLAN.md`；阶段 21–23 提交为 `3025d77`、`37595ab`、`d9c53f4`。

## 能力全景

```text
Agent 对话与工具调用
  -> EvolutionTrace（项目本地原始轨迹）
  -> ExperienceEngine（候选经验与证据）
  -> 离线评估 / 人工反馈 / Canary / Replay
  -> Verified Experience（跨项目共享）
  -> 语义化 StructuredStrategy
  -> Skill 草案 -> 隔离测试 -> 精确确认安装
  -> 可执行策略草案 -> 固定包装器 -> 隔离测试 -> 审批
  -> 重复失败聚合 -> SourceProposal
  -> Git worktree 中测试优先修复
  -> 受保护全量评估 -> 候选提交
  -> 精确确认合并 -> git revert 回滚
```

经验层和源代码层是两条不同路径：经验层影响后续 Agent 的上下文、工作流、Skill 与受限工具编排；源代码层产生真实 Git 分支、测试、提交、合并提交和回滚提交。

## 阶段 1–23

| 阶段 | 完成内容 |
|---|---|
| 1 | 持久化任务轨迹，记录质量分数、错误和工具调用。 |
| 2 | 建立候选经验、正负证据、晋升、检索和拒绝机制。 |
| 3 | 将已验证经验安全注入 Agent prompt，支持反馈和回滚。 |
| 4 | 增加经验管理面板、筛选、反馈和回滚入口。 |
| 5 | 增加来源、使用次数、应用结果评分和失败自动回退。 |
| 6 | 增加上下文指纹，防止不同任务领域的经验错误匹配。 |
| 7 | 增加离线评估门槛、策略版本快照和版本回滚。 |
| 8 | 增加脱敏的结构化反思、人工审阅和重新评估。 |
| 9 | 增加确定性 Canary 分流、指标、晋升和失败回滚。 |
| 10 | 增加隔离 A/B Replay、安全检查和回放报告。 |
| 11 | 增加经验去重、过期、Trace 清理、定期维护和报告。 |
| 12 | 增加脱敏导出、备份、恢复、可恢复清理和审计日志。 |
| 13 | 将稳定经验合成为 Skill 草案，隔离测试并精确确认安装。 |
| 14 | 经验证据在所有项目间共享，原始 Trace 仍保持项目本地。 |
| 15 | 将经验升级为带条件、步骤和验证标准的结构化语义策略。 |
| 16 | 多条稳定结构化经验合成标准 `SKILL.md`。 |
| 17 | 生成无动态代码的受限可执行策略，由固定包装器执行。 |
| 18 | 聚合跨项目重复失败，脱敏后生成可审计的源代码提案。 |
| 19 | 在独立 Git worktree 中先生成失败测试，再允许修改实现。 |
| 20 | 运行受保护评估，创建候选提交，精确确认晋升，并用 revert 回滚。 |
| 21 | 将进化路由和前端进化面板从巨型 WebChat 文件拆成独立模块。 |
| 22 | 接入受限 LLM 补丁编排器，自动生成失败测试和精确源码替换。 |
| 23 | 统一治理经验、提案、实验、Skill 和可执行策略的备份、恢复与清理。 |

## 核心模块

所有实现位于 `miniclaw/learning/`：

| 文件 | 职责 |
|---|---|
| `trace.py` | `EvolutionTrace`、工具轨迹和 JSONL 持久化。 |
| `experience.py` | 经验模型、SQLite 存储、证据、检索、Canary 与版本。 |
| `evaluation.py` | 固定的离线经验评估规则。 |
| `service.py` | Agent、WebChat 和各进化模块使用的统一门面。 |
| `reflection.py` | 深度结构化反思与脱敏校验。 |
| `replay.py` | 隔离 A/B 回放评估。 |
| `maintenance.py` | 去重、过期、审查和 Trace 保留。 |
| `governance.py` | 导出、备份、恢复、清理和审计。 |
| `strategy.py` | 语义步骤、适用条件和验证标准。 |
| `skill_evolution.py` | Skill 草案合成、测试、审批和安装。 |
| `executable_policy.py` | 受限可执行工作流的编译、测试、审批和执行。 |
| `source_proposal.py` | 重复缺陷检测、脱敏和源代码提案库。 |
| `source_experiment.py` | 单候选 worktree、测试优先约束和补丁策略。 |
| `source_agent.py` | 受限 LLM JSON 计划、上下文脱敏和精确文件编辑。 |
| `source_promotion.py` | 全量评估、候选提交、合并晋升和 revert 回滚。 |

## 数据位置与共享范围

| 数据 | 默认位置 | 共享范围 |
|---|---|---|
| 原始 Trace | `<project>/.miniclaw/evolution/traces.jsonl` | 项目本地；评估时联邦读取。 |
| 经验、证据、评估、Canary、Replay | `<WORKSPACE_DIR>/.miniclaw/evolution/experiences.db` | 所有项目共享。 |
| Skill 草案 | `<WORKSPACE_DIR>/.miniclaw/evolution/skill-drafts/` | 所有项目共享。 |
| 已安装 Skill | `<WORKSPACE_DIR>/skills/<skill-name>/SKILL.md` | 所有项目可加载。 |
| 可执行策略 | `<WORKSPACE_DIR>/.miniclaw/evolution/executable-policies/` | 草案与已批准版本分开保存。 |
| 源代码提案与实验 | `<WORKSPACE_DIR>/.miniclaw/evolution/source-evolution.db` | 聚合所有项目证据。 |
| 源代码 worktree | `<WORKSPACE_DIR>/.miniclaw/evolution/source-evolution/worktrees/` | 每个实验一个独立目录。 |

共享验证经验和聚合证据不等于复制原始对话。原始 Trace 仍留在各项目目录，`EvolutionService._evaluation_traces()` 在需要评估时联邦读取。

阶段 23 的统一治理会列出并备份经验数据库、Trace、审计日志、源代码提案/实验数据库、Skill 草案、由 approved 草案安装的 Skill，以及可执行策略。用户手工安装且没有 approved 草案来源的普通 Skill 不会被覆盖。Git worktree 不进入备份；已结束实验的 worktree 可在保留候选分支的前提下清理。清理默认只预览，实际执行要求精确输入 `CLEAN EVOLUTION ARTIFACTS`。

## 经验状态与反哺

```text
candidate
  -> 正向证据达到门槛 -> pending_evaluation
  -> 离线评估通过 -> verified
  -> 若启用 Canary -> canary -> verified / rejected

任意已应用经验
  -> 负向证据达到门槛 -> rejected
  -> 人工 rollback -> 回退并保留版本记录
```

默认正向证据门槛为 3，负向证据门槛为 2。只有与查询及上下文指纹匹配的 `verified` 经验会注入 prompt；Canary 开启时可按确定性桶向少量流量注入候选。

阶段 15 后，经验不再只保存工具名字顺序，还可包含：

- `conditions`：何时适用。
- `steps`：每一步的意图、工具、输入说明和预期输出。
- `validation`：完成标准和失败处理。

敏感工具执行内容不会直接变成全局经验。

## Skill 与可执行策略

`SkillSynthesizer` 从同一任务模式下多条稳定经验生成标准 `SKILL.md` 草案。草案必须通过格式和隔离测试，批准时需要精确确认 Skill 名称，之后才安装到共享 `skills/`。

可执行策略不是任意生成 Python 代码。生成物只描述允许的步骤和参数绑定，由仓库内固定包装器执行，并限制工具白名单、工作区范围、参数、超时和步骤数量；同样必须隔离测试和明确审批。

## 单候选 PR 型源代码进化

### 流程

1. `RepeatedFailureDetector` 从多个项目的失败 Trace 中聚合相同问题。
2. 达到最小次数和项目数后写入 `SourceProposalStore`。
3. 提案从 `open` 经审阅变成 `approved` 或 `rejected`。
4. `SingleCandidateExperimentRunner` 要求源码仓库干净，并且一次只能有一个活动实验。
5. 从基线创建 `codex/evolution-<proposal>-<id>` 分支和独立 worktree。
6. `SourcePatchAgent.write_reproduction_test()` 只能新增 `tests/test_evolution_generated_*.py`。
7. 新测试必须在旧实现上失败，否则拒绝继续。
8. `SourcePatchAgent.implement_patch()` 修改实现后，检查受保护路径、变更规模和危险代码。
9. 针对性测试通过后，评估器运行全量 Pytest、可选前端语法检查和 `git diff --check`。
10. 全部通过后在候选分支创建提交，状态变成 `ready`，但不会自动合并。
11. 精确输入 `PROMOTE <experiment_id>` 才执行 `git merge --no-ff`。
12. 精确输入 `ROLLBACK <experiment_id>` 才执行 `git revert -m 1`。

### 保护边界

- 不允许修改已有测试，只能新增专用回归测试。
- 不允许修改 Git 元数据、工作流、自进化门禁、治理逻辑、执行工具和既有验收脚本。
- 默认最多修改 8 个文件，总增删不超过 500 行。
- 拒绝二进制、符号链接、路径逃逸、动态执行、危险反序列化、直接网络下载和 `shell=True`。
- 主工作区必须干净且仍位于实验基线，否则禁止晋升。
- 候选分支 HEAD 必须与已评估提交完全一致。

### 补丁 Agent 接口

```python
class SourcePatchAgent(Protocol):
    def write_reproduction_test(self, worktree, proposal) -> PatchActionResult: ...
    def implement_patch(self, worktree, proposal) -> PatchActionResult: ...
```

阶段 22 后，`LLMSourcePatchAgent` 会先请求结构化回归测试，确认旧实现失败后才请求实现补丁。模型只能返回 JSON 中的“创建生成测试”或“在可疑源码文件中精确替换唯一文本”；不能执行 shell、安装依赖或自由写文件。运行 API 还要求功能开关、已批准提案和精确口令 `RUN <proposal_id>`。自动运行只产生 `patched` 实验，不会绕过评估或自动合并。

WebChat 的进化路由位于 `miniclaw/channels/web/evolution_routes.py`，经验与反思前端位于 `miniclaw/channels/web/static/evolution.js`；`webchat.py` 和 `app.js` 只负责注册和调用。

## 配置

关键默认配置位于 `miniclaw/config.yaml`：

```yaml
evolution:
  enabled: true
  auto_observe: true
  inject_verified: true
  max_injected: 3
  auto_evaluate_applied: true
  deep_reflection_enabled: false
  canary_enabled: false
  replay_enabled: false
  maintenance_enabled: true
  skill_min_experiences: 2
  auto_skill_drafts_enabled: true
  executable_policy_timeout_sec: 30

  source_evolution_enabled: false
  source_repo_path: ""
  source_failure_min_occurrences: 3
  source_failure_min_projects: 2
  source_patch_max_files: 8
  source_patch_max_changed_lines: 500
  source_test_timeout_sec: 300
  source_auto_patch_enabled: false
  source_context_max_files: 6
  source_context_max_chars: 30000
  source_llm_timeout_sec: 120
  artifact_retention_days: 90
```

启用源代码进化至少需要：

```yaml
evolution:
  source_evolution_enabled: true
  source_auto_patch_enabled: true
  source_repo_path: "C:/absolute/path/to/git/repository"
```

长期运行的 MiniClaw 进程需要重启才能读取新配置。

## Web 管理 API

经验与评估：

- `GET /api/evolution/experiences`
- `POST /api/evolution/feedback`
- `POST /api/evolution/rollback`
- `GET /api/evolution/evaluations`
- `POST /api/evolution/maintenance`
- `GET /api/evolution/reflections`
- `POST /api/evolution/reflections/{reflection_id}/review`

治理、Skill 与策略：

- `GET /api/evolution/governance`
- `POST /api/evolution/governance/cleanup`
- `GET /api/evolution/export`
- `POST /api/evolution/backup|restore|purge`
- `GET|POST /api/evolution/skill-drafts`
- `POST /api/evolution/skill-drafts/{draft_id}/test|review`
- `GET|POST /api/evolution/executable-policies`
- `POST /api/evolution/executable-policies/{policy_id}/test|review`

源代码进化：

- `GET /api/evolution/source/proposals`
- `POST /api/evolution/source/proposals/detect`
- `POST /api/evolution/source/proposals/{proposal_id}/review`
- `POST /api/evolution/source/proposals/{proposal_id}/run`
- `GET /api/evolution/source/experiments`
- `POST /api/evolution/source/experiments/{experiment_id}/evaluate`
- `POST /api/evolution/source/experiments/{experiment_id}/promote`
- `POST /api/evolution/source/experiments/{experiment_id}/rollback`

源代码相关 API 在功能关闭时返回 403；仓库路径未配置或不是 Git 仓库时返回 400。

## 验收与开发规则

```powershell
python -m pytest -q

$scripts = Get-ChildItem scripts -Filter 'acceptance_stage*.py' |
  Sort-Object { [int]([regex]::Match($_.BaseName, '\d+').Value) }
foreach ($script in $scripts) {
  python $script.FullName
  if ($LASTEXITCODE -ne 0) { break }
}

node --check miniclaw/channels/web/static/app.js
node --check miniclaw/channels/web/static/evolution.js
git diff --check
```

修改时必须保持以下不变量：模型不能决定自己的评估是否通过；候选不能修改评估器和晋升门禁；原始项目 Trace 不因全局共享而集中复制；Skill 和可执行策略必须测试后审批；源代码候选不得自动合并；回滚必须使用可审计的新提交而不是硬重置。

## 已知边界与后续方向

- 当前是单候选搜索，不会并行生成多个补丁后择优。
- 当前是本地 PR 型流程：worktree、候选分支、候选提交和非快进合并；尚未自动创建远程 GitHub/GitLab PR。
- 自动补丁默认关闭，且只支持可疑文件中的精确文本替换；复杂跨文件重构仍需要人工开发流程。
- 源代码晋升仍需要人工或外部控制器提供精确确认口令。
- 后续可增加远程 CI 验证、真实 PR 发布和多候选锦标赛，但不能削弱现有保护边界。
