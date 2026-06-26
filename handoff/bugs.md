# 近期 Bug 修复记录

这里记录了 2026-06 期间修复的 Bug。**修改前务必阅读**，避免踩坑或撤销已有的修复。

---

## 1. Markdown 渲染失效

**症状**: Agent 输出显示原始 `## 标题` `|表格|` 等 Markdown 源码

**原因**: `marked.js` / `highlight.js` 从 jsdelivr CDN 加载，国内网络阻断。`typeof marked === "undefined"` 时走到 `textContent` 分支显示原始文本。

**修复**:
- 下载 `marked.min.js` (35KB)、`highlight.min.js` (119KB)、`highlight.css` 到 `static/`
- `index.html` 改为本地 `<script src="/static/...">`
- 同时增强了 fallback 渲染器支持标题/表格/列表/水平线

**影响文件**: `index.html`, `app.js` (renderMarkdown fallback)

---

## 2. SSE 流断开导致最终回复丢失

**症状**: 多轮工具调用后最终答复不显示，需要刷新页面

**原因**: 工具执行期间无 SSE 事件超 10s → `is_disconnected()` → `subscribed=False` → 后续 `done` 事件丢失

**修复** (`app.js`):
- 添加 `receivedDone` 标志位追踪
- `done` handler 始终优先用 `payload.full`（服务端完整结果），不再依赖不完整的 `rawContent`
- 残量 buffer 解析补上 `done` 事件处理
- **兜底**: 流结束后 `!receivedDone` → 自动调 `/api/history` 拉取最后一条 assistant 消息

**影响文件**: `app.js` (send 函数)

---

## 3. OVITO GIF 在 SSE 断开后丢失

**症状**: GIF 已落盘但浏览器不显示，刷新也不恢复

**原因**: SSE 断开 → `subscribed=False` → `_emit_media("gif")` 被丢弃 → `localStorage` 未更新。`session.metadata["viz_done"]` 虽有记录但客户端从未读取。

**修复**:
- `/api/history` 响应新增 `viz_done` 字段 (`webchat.py:513`)
- 客户端新增 `restoreVizFromServer()` — 从服务端补漏 GIF/PNG/CSV
- `loadHistory()` 和 SSE 兜底恢复都调用它
- `removeStructureCard` 记入 `localStorage("miniclaw_viz_removed")`，防手动叉掉后复活

**影响文件**: `webchat.py`, `app.js` (restoreVizFromServer, loadHistory, removeStructureCard)

---

## 4. 结构 3D 黑屏（三次修复）

### 4a. IntersectionObserver 导致首帧跳过

**原因**: `createStructureScene` 用 `requestAnimationFrame` 延迟渲染，Observer 在首帧前触发 `animPaused=true` → 跳过首帧 → 永远黑屏

**修复**: 同步渲染首帧 + Observer 恢复时重绘 + `onResize` 后重绘 + `switchTab` 延迟 resize

### 4b. CDN integrity hash 不匹配

**原因**: `three.min.js` 从 unpkg 下载，但 HTML 中 `integrity` hash 是 jsdelivr 的 → 浏览器阻止加载 → THREE 未定义

**修复**: 删除本地文件的 `integrity` / `crossorigin` 属性

### 4c. ES Module 迁移后 import map 失败

**原因**: `<script type="importmap">` 映射 `"three"` 到本地路径不生效

**修复**: 改用直接 import 路径 `import * as THREE from '/static/three.module.js'`

**影响文件**: `app.js` (createStructureScene, switchTab), `index.html`

---

## 5. Three.js UMD → ES Module

`three.min.js` (UMD) 在 r150+ 标记废弃。迁移到 `three.module.js` (ESM):
- `app.js` 从 IIFE 改为 module (`<script type="module">`)
- 顶部 `import * as THREE from '/static/three.module.js'`
- `typeof marked` / `typeof hljs` 在 strict mode 下安全（`typeof` 不抛异常）

---

## 6. 辅助功能 / 安全

- `<select>` / `<input>` 添加 `aria-label` + `title`
- `<label for>` 修复指向错误的 `id`（`for="project-objective"` → `for="project-objective-input"`）
- 添加 `X-Content-Type-Options: nosniff` 响应头 middleware
- CSS 添加 `-webkit-user-select` / `-webkit-backdrop-filter` 前缀
