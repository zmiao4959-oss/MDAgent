# API 端点参考

> `/api/evolution/*` 已包含经验反馈、评估、反思、治理、Skill、可执行策略和源代码候选管理接口。完整端点与启用条件见 [`evolution.md`](evolution.md#web-管理-api)。源代码进化默认返回 403，必须显式启用并配置 Git 仓库。

所有端点定义在 `miniclaw/channels/webchat.py` 的 `event_stream()` 闭包内。

## 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/chat` | **核心**: 发送消息，返回 SSE 流 (`text/event-stream`) |
| `GET` | `/api/history?chat_id=` | 获取对话历史 + `viz_done` |
| `GET` | `/api/conversations` | 会话列表 |
| `POST` | `/api/conversations` | 新建会话 |
| `PATCH` | `/api/conversations/{chat_id}` | 重命名会话 `{alias}` |
| `DELETE` | `/api/conversations/{chat_id}` | 删除会话 |
| `POST` | `/api/conversations/{chat_id}/fork` | 分叉对话 `{at_index}` |

### POST /api/chat

请求体:
```json
{
  "message": "用户输入",
  "chat_id": "webchat:xxx",
  "project_id": "xxx",
  "plan_mode": false,
  "timeout": 300
}
```

SSE 事件类型:
```
data: {"delta": "流式文本片段"}
data: {"viz": true, "event": "status", "text": "OVITO 渲染中…"}
data: {"viz": true, "event": "media", "media_type": "gif", "path": "/abs/path"}
data: {"viz": true, "event": "error", "text": "渲染失败"}
data: {"progress": {"current": 5000, "total": 10000}}
data: {"done": true, "full": "完整响应文本"}
```

### GET /api/history

响应:
```json
{
  "messages": [{"role": "user|assistant|tool", "content": "…"}],
  "chat_id": "webchat:xxx",
  "viz_done": ["/path/file.dump", "/path/file.dump.gif"]
}
```

## 项目

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/projects?include_archived=` | 项目列表 |
| `POST` | `/api/projects` | 创建项目 `{title, objective}` |
| `PATCH` | `/api/projects/{id}` | 更新 `{title, objective}` |
| `GET` | `/api/projects/{id}/plan` | 获取计划 `{tasks, summary}` |
| `POST` | `/api/projects/{id}/tasks` | 添加任务 `{title}` |
| `PATCH` | `/api/projects/{id}/tasks/{task_id}` | 更新任务 `{done, title}` |
| `POST` | `/api/projects/{id}/run-next` | 执行下一未完成任务 |
| `POST` | `/api/projects/{id}/action` | 项目操作 `{action: pause\|resume\|complete\|archive}` |
| `GET` | `/api/projects/{id}/artifacts` | 项目产物（文件列表） |
| `GET` | `/api/projects/{id}/summary` | 项目摘要 + 最新产物 |
| `GET` | `/api/projects/{id}/timeline` | 项目运行时间线 |

## 文件

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/files?work_dir=&project_id=` | 工作区文件浏览 |
| `GET` | `/api/asset?path=&project_id=` | 获取文件内容（FileResponse） |

路径解析: `resolve_workspace_path(path)` — 支持相对/绝对路径，强制限制在工作区根目录内。详情见 `miniclaw/tools/paths.py`。

## 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/config` | 前端配置（workspace_root, visual_ext, skip_dir_names） |
| `GET` | `/api/stats` | Agent 运行统计 |
| `GET` | `/api/export?chat_id=&fmt=markdown\|json` | 导出对话 |

## 安全响应头

`webchat.py` 中有 middleware 为所有响应添加 `X-Content-Type-Options: nosniff`。
