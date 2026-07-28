# 科研胶囊

科研胶囊把项目工作区、对话统计、最近一次运行 Trace、环境摘要和文件哈希清单保存为一个项目本地的复现单元。阶段 24–25 已实现捕获、验证和脱敏导出；隔离重放属于阶段 26。

## 数据位置

```text
<project-workspace>/.miniclaw/research-capsules/
└── capsule_<id>/
    ├── capsule.json
    ├── README.md
    ├── verification.json
    └── exports/
        └── research-capsule-<id>.zip
```

`.miniclaw` 不会进入自身文件清单。每个普通文件按真实字节流计算 SHA-256；清单按相对路径排序，`manifest_sha256` 只覆盖路径、大小和文件哈希。

## 捕获内容

- 项目标题、目标、项目 ID 与 chat ID。
- 会话消息数量和角色计数，不复制完整对话正文。
- 同一 chat ID 最近一条 `EvolutionTrace` 的运行指标与脱敏工具参数。
- Python、操作系统、机器架构、关键依赖版本。
- 当前 MiniClaw Git commit、分支和 dirty 状态。
- 工作区文件清单，以及被排除路径和原因。

## 安全边界

- 只允许隔离的项目工作区；使用全局共享工作区的旧项目会返回 409。
- 项目仍在运行时返回 409，避免文件在散列期间变化。
- 跳过 `.env`、常见凭据文件、私钥、符号链接和工作区外路径。
- 工具参数中的密钥字段、内联 `API_KEY=...` 和 Bearer token 会被替换为 `[REDACTED]`。
- 所有核验与导出都要求项目停止；核验发现修改、缺失或不可读文件时，实际文件导出返回 409。
- 实际文件 ZIP 要求精确确认 `EXPORT <capsule_id>`。文本文件在入包前再次脱敏，`export.json` 同时记录源哈希和导出哈希。
- 单文件超过 8 MiB 时，实际文件导出会拒绝并提示改用“仅清单”。仅清单 ZIP 不包含 `files/`；任何导出都不会上传，也不会执行重放。

## 完整性状态

- `verified`：清单内文件全部匹配，且没有新增普通文件。
- `changed`：文件内容或大小变化，或工作区出现新增普通文件。
- `missing`：清单文件已经不存在；优先于 `changed`。
- `unreadable`：路径不安全、文件无法读取或核验期间发生变化；优先级最高。

最新报告保存在 `verification.json`，列出未变化、修改、缺失、不可读、新增和跳过路径。它是独立证据，不会改写原始 `capsule.json`。

## API

```text
POST /api/projects/{project_id}/capsules
GET  /api/projects/{project_id}/capsules
GET  /api/projects/{project_id}/capsules/{capsule_id}
POST /api/projects/{project_id}/capsules/{capsule_id}/verify
POST /api/projects/{project_id}/capsules/{capsule_id}/export
```

导出请求体：

```json
{
  "confirmation": "EXPORT capsule:abc123",
  "include_files": true
}
```

WebChat 的“胶囊”标签可生成、核验、仅清单导出或下载脱敏 ZIP。核心实现位于 `miniclaw/research_capsule.py`；HTTP 路由位于 `miniclaw/channels/web/capsule_routes.py`。后续计划见 `docs/RESEARCH_CAPSULE_TASKBOOK.md`。
