# WebChat 通道

前后端结合的 Web 聊天界面：左侧会话与项目，右侧可视化和协作任务面板。

## 后端结构

WebChat 监听 `http://127.0.0.1:8000`，后端按职责拆分：

| 文件 | 职责 |
|------|------|
| `channels/webchat.py` | 旧导入路径兼容 |
| `channels/webchat_adapter.py` | 组合服务、启动和停止 Uvicorn |
| `channels/web/web_app.py` | 创建 FastAPI、挂载静态资源、统一注册路由 |
| `channels/web/chat_routes.py` | `POST /api/chat` 与 SSE 响应 |
| `channels/web/chat_run.py` | 驱动单次 Agent 请求 |
| `channels/web/chat_events.py` | 编码 delta、进度、媒体和子任务事件 |
| `channels/web/conversation_service.py` | 会话 CRUD、分叉、历史、导出和消息投递 |
| `channels/web/conversation_routes.py` | 会话 HTTP 接口 |
| `channels/web/project_run.py` | 项目后台任务、并发、重试与状态 |
| `channels/web/project_workspace.py` | 项目目录、产物和会话预览 |
| `channels/web/file_routes.py` | 项目范围内的文件与资源访问 |
| `channels/web/knowledge_service.py` | GPUMD 同步和索引任务状态机 |
| `channels/web/knowledge_routes.py` | GPUMD 知识库接口 |
| `channels/web/system_routes.py` | 配置、诊断、任务行为和统计接口 |

## SSE 数据流

```text
POST /api/chat
  └─ chat_routes.py
       ├─ 创建 AgentContext
       ├─ ChatEventStream 建立输出队列
       └─ ChatRunService.run()
            ├─ 调用 Agent message handler
            ├─ delta / progress / viz / task_event → SSE
            ├─ 等待未完成的可视化任务
            └─ done / error → 浏览器
```

当请求断开时，普通聊天任务会取消；项目聊天继续在后台运行，最终响应保存进
Session。浏览器刷新或切换对话后，通过 `/api/history` 恢复消息和 `viz_done`。

Coordinator 委派的子任务以 `pending → running → done/error` 事件进入同一个
SSE 流，前端按任务 ID 更新“协作任务”面板，因此并行任务可同时展示。

## 前端

主逻辑位于 `miniclaw/channels/web/static/app.js`，经验与反思面板位于
`evolution.js`，科研胶囊面板位于 `capsules.js`。详见
[`frontend.md`](frontend.md)。

### 静态资源

| 文件 | 来源 | 说明 |
|------|------|------|
| `marked.min.js` | npm marked@12 | Markdown → HTML |
| `highlight.min.js` | npm @highlightjs/cdn-assets@11.9 | 代码语法高亮 |
| `highlight.css` | atom-one-dark 主题 | 代码块样式 |
| `three.module.js` | npm three@0.160 (ESM) | 3D 结构渲染 |
| `style.css` | 手写 | 全局样式 |

所有库已本地化到 `static/`，不依赖 CDN。完整端点见 [`api.md`](api.md)。

## 项目模式与普通模式

- 普通聊天：无 `project_id`，浏览器断开会取消当前 Agent 请求。
- 项目聊天：携带 `project_id`，断开后继续运行；状态由服务端在
  `queued/running/active/paused/completed/archived` 之间管理。
