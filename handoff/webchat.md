# WebChat 通道

前后端结合的 Web 聊天界面：左对话 + 右可视化面板。

## 后端

**文件**: `miniclaw/channels/webchat.py`

FastAPI 应用，挂载在 `http://127.0.0.1:8000`。所有 API 端点定义为 `event_stream()` 内的闭包。

### SSE 流核心 (`POST /api/chat`)

```python
async def event_stream():
    subscribed = True     # 客户端连接状态
    out_q = asyncio.Queue()
    result = {"response": None}

    # 三个回调，注入给 Agent
    async def collect_delta(chunk):   # LLM 流式文本 → out_q
        if subscribed: await out_q.put({"kind": "delta", "text": chunk})
    async def on_viz(event):          # 可视化事件 → out_q
        if subscribed: await out_q.put({**event, "viz": True})
    async def on_progress(data):      # LAMMPS 进度 → out_q
        if subscribed: await out_q.put({"kind": "progress", "data": data})

    async def run_agent():
        try:
            result["response"] = await asyncio.wait_for(
                adapter._message_handler(ctx, on_stream_chunk=collect_delta,
                                         on_viz_event=on_viz, on_progress=on_progress),
                timeout=agent_timeout)
        finally:
            pending = ctx.metadata.get("viz_tasks") or []
            if pending:
                await asyncio.gather(*pending)   # ← 等待所有 OVITO 渲染完成
            await out_q.put(None)                # ← 终止 while 循环

    task = asyncio.create_task(run_agent())

    # 主循环：从 out_q 读取，yield SSE 事件
    while True:
        item = await asyncio.wait_for(out_q.get(), timeout=10.0)
        if item is None: break
        yield f"data: {json.dumps(item)}\n\n"

    yield f"data: {json.dumps({'done': True, 'full': result['response']})}\n\n"
```

### `subscribed` 标志位

当 10s 超时且 `await request.is_disconnected()` 返回 True 时，`subscribed = False`，函数 return。**此后所有 `collect_delta` / `on_viz` 回调静默丢弃事件**。

对于 **project 聊天**（`project is not None`），客户端断开后 agent 任务继续运行，响应保存到 session。客户端刷新或切换对话后从 `/api/history` 恢复。

## 前端

**文件**: `miniclaw/channels/web/static/app.js` (~1700 行)

详见 [`frontend.md`](frontend.md)。

### 静态资源

| 文件 | 来源 | 说明 |
|------|------|------|
| `marked.min.js` | npm marked@12 | Markdown → HTML |
| `highlight.min.js` | npm @highlightjs/cdn-assets@11.9 | 代码语法高亮 |
| `highlight.css` | atom-one-dark 主题 | 代码块样式 |
| `three.module.js` | npm three@0.160 (ESM) | 3D 结构渲染 |
| `style.css` | 手写 | 全局样式 |

所有库已**本地化**到 `static/` 目录（不再依赖 CDN）。

### 关键 API 端点

详见 [`api.md`](api.md)。

## 项目模式 vs 普通模式

- **普通聊天**: 无 project_id，agent 执行期间断开 SSE = cancel 任务
- **项目聊天**: 有 project_id，agent 在后台继续运行，浏览器断开不影响。项目状态（queued/running/active/paused/completed/archived）由服务端管理
