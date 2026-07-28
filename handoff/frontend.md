# 前端代码地图

**文件**: `miniclaw/channels/web/static/app.js`（主模块）、`evolution.js`（经验/反思面板）和 `capsules.js`（科研胶囊面板），均为 ES Module。

## 架构

```
import * as THREE from '/static/three.module.js';  // 行1
import { createEvolutionPanel } from '/static/evolution.js';
import { createResearchCapsulePanel } from '/static/capsules.js';

// 全局状态 (~60 行)
// marked.js 配置 (~20 行)
// 工具函数: sanitizeHTML, renderMarkdown, addMsg, assetUrl, ...
// 可视化: appendMedia, appendCsvChart, appendStructure, createStructureScene
// 侧栏: loadSidebar, selectChat, createNewChat
// 对话: loadHistory, send, parseSseBuffer
// 项目: refreshPlan, refreshArtifacts, refreshTimeline
// 进化面板: evolution.js，通过 getProjectId 依赖注入获取当前项目
// 科研胶囊: capsules.js，通过依赖注入刷新项目、时间线和文件大小显示
// 事件绑定 + init()
```

## 全局状态变量

```js
currentChatId        // localStorage: miniclaw_webchat_chat_id
currentProjectId     // localStorage: miniclaw_webchat_project_id
chatRunning          // Agent 是否正在运行
planMode             // 计划模式开关
receivedDone         // SSE done 事件是否收到
savedMedia[]         // 媒体文件路径 → localStorage
savedStructures[]    // 结构文件路径 → localStorage
structureScenes[]    // Three.js Scene 对象引用
_mediaCardsByPath    // Map<路径, DOM> 去重
_structureCardsByPath
```

## localStorage 键

| Key | 用途 |
|-----|------|
| `miniclaw_webchat_chat_id` | 当前对话 |
| `miniclaw_webchat_project_id` | 当前项目 |
| `miniclaw_viz_{chatId}` | viz 状态 `{media, structures}` |
| `miniclaw_viz_removed` | 手动移除的路径（防复活） |
| `miniclaw_sidebar_collapsed` | 侧栏折叠 |
| `miniclaw_viz_collapsed` | viz 面板折叠 |

## 关键函数索引

| 行号 | 函数 | 用途 |
|------|------|------|
| 1 | `import * as THREE` | Three.js ESM 导入 |
| 111 | `sanitizeHTML(html)` | XSS 清洗（去除 script/iframe/on*） |
| 134 | `renderMarkdown(text)` | marked.js 渲染 + 代码复制按钮 + 安全清洗 |
| 250 | `addMsg(role, text)` | 添加消息到对话区（agent→markdown, user→纯文本） |
| 265 | `setChatProgress(active, text, pct)` | 进度条 + 发送按钮状态 |
| 552 | `appendMedia(type, path)` | 追加媒体卡片（GIF/图片）到画廊 |
| 648 | `appendCsvChart(path)` | CSV → Canvas 应力应变曲线 |
| 716 | `parseStructureFile(text, ext)` | 多格式结构解析（路由） |
| 821 | `parseStandardXyz(text)` | 标准 XYZ 格式 |
| 847 | `parseLammpsData(text)` | LAMMPS data 格式 |
| 787 | `parseDumpFirstFrame(text)` | LAMMPS dump 第一帧 |
| 846 | `parseXsf(text)` | XCrySDen XSF 格式 |
| 920 | `createStructureScene(container, atoms)` | Three.js InstancedMesh 3D 渲染 |
| 1000 | `removeStructureCard(card)` | 移除结构卡片 + 记入 removed 列表 |
| 1126 | `loadStructureFromPath(path, ext)` | 从服务端加载结构文件 → 解析 → 渲染 |
| 1170 | `handleVizEvent(data)` | SSE viz 事件分发（status/media/error） |
| 1297 | `restoreVizFromServer(paths)` | 从服务端 viz_done 恢复媒体文件 |
| 1330 | `loadHistory()` | 从 /api/history 恢复消息 + viz 状态 |
| 1348 | `selectChat(projectId)` | 切换项目（加载历史 + viz + 文件列表） |
| 1380 | `send()` | **核心**: POST /api/chat → SSE 流读取 → 渲染 |
| 1396 | `finalizeAgentDiv(text)` | 统一最终渲染 |
| 1350 | `parseSseBuffer(buffer, cb)` | SSE 行解析器 |

## 前端事件流程

### 发送消息 (`send()`)

```
用户输入 → 显示 user bubble → POST /api/chat
  → ReadableStream reader
  → parseSseBuffer 循环:
       payload.delta   → renderMarkdown(rawContent) + 光标
       payload.viz     → handleVizEvent → appendMedia / appendCsvChart
       payload.progress → setChatProgress(percent)
       payload.done    → receivedDone=true → finalizeAgentDiv(payload.full)
  → 兜底: !receivedDone → fetch /api/history 恢复文本 + viz
```

### 页面加载 (`selectChat()`)

```
restoreVizState()        # 从 localStorage 恢复媒体/结构
  ↓
loadHistory()            # 从 /api/history 恢复消息
  └─ restoreVizFromServer(data.viz_done)  # 从服务端补漏
  ↓
refreshFileList()        # 工作区文件浏览器
refreshArtifacts()       # 项目产物面板
refreshTimeline()        # 运行记录
refreshPlan()            # 计划面板
```

## 运行诊断与任务行为（2026-07-16）

- 设置弹窗新增“运行诊断”，通过 `GET /api/diagnostics` 检查 Agent、GPUMD、LAMMPS、NVIDIA GPU、SSH 客户端和向量服务；诊断结果不包含任何 API 密钥。
- “导出诊断”在浏览器中生成 JSON 文件，便于用户提交问题时附带环境状态。
- 任务行为通过 `GET/PUT /api/settings/task-behavior` 读取和保存，持久化文件位于工作区的 `webchat-task-behavior.json`。
- 最大并发数范围为 1–4，使用动态条件队列立即作用于新任务和排队任务，不需要重启 WebChat。
- 自动重试范围为 0–2 次，仅用于后台计划任务；普通流式聊天不自动重试，以免已经输出的内容重复出现。
- 完成通知默认关闭。启用时请求浏览器通知权限，仅当页面在后台且任务从运行/排队状态结束时通知。

## CSS

**文件**: `miniclaw/channels/web/static/style.css` (~1150 行)

暗色主题，CSS 变量定义在 `:root`：

```css
--bg: #1a1a2e;
--panel: #16213e;
--text: #eaeaea;
--accent: #00d4ff;
--agent: #ffd700;
```

## WebChat 设置（2026-07-16）

- 入口：聊天区顶部的“⚙ 设置”按钮。
- 页面文件：`miniclaw/channels/web/static/index.html`。
- 设置逻辑：`miniclaw/channels/web/static/app.js`，配置保存在浏览器本地存储 `miniclaw_webchat_settings_v1`，不写入后端，也不保存 API 密钥。
- 样式：`miniclaw/channels/web/static/style.css`，通过 `document.documentElement.dataset` 应用主题、消息字号和代码换行。
- 当前选项：深色/浅色/跟随系统、消息字号、代码块自动换行、新消息自动滚动、Enter 发送、显示快速开始。
- 发送快捷键：关闭“Enter 发送”后，使用 `Ctrl/Cmd+Enter` 发送；输入框提示会同步变化。
- 默认值：深色、标准字号、代码不换行、自动滚动、Enter 发送、显示快速开始。
- 测试：`tests/test_webchat_settings.py` 覆盖控件、持久化逻辑、主题和响应式样式；已在实际 WebChat 页面验证保存、刷新恢复、恢复默认和控制台无错误。

响应式断点: 1100px / 900px / 700px。

## GPUMD 知识库设置（2026-07-16）

- 设置弹窗新增“GPUMD 知识库”区，显示语料文档块数、索引记录数、当前 embedding 模型和同步状态。
- “同步官方文档”调用 `POST /api/knowledge/gpumd/sync`，复用 `miniclaw.rag.sync_gpumd.sync`；任务在后台线程运行，页面按状态轮询。
- “增量更新向量”调用 `POST /api/knowledge/gpumd/index`，复用现有增量构建器；更新逻辑仍以文档 ID、内容哈希和模型名决定复用、更新与删除。
- `GET /api/knowledge/gpumd` 只返回是否已配置密钥，不返回密钥内容；索引按钮在缺少语料、RAG 已关闭或密钥未配置时禁用。
- 状态接口只读取大型索引文件开头的 `schema_version`、`model`、`count`，不会在每次页面轮询时载入全部向量。
- 后台任务结果会显示复用、新增或变化、删除失效的数量；同一时间只允许一个知识库维护任务。
