# 前端代码地图

**文件**: `miniclaw/channels/web/static/app.js` (~1700 行，ES Module)

## 架构

```
import * as THREE from '/static/three.module.js';  // 行1

// 全局状态 (~60 行)
// marked.js 配置 (~20 行)
// 工具函数: sanitizeHTML, renderMarkdown, addMsg, assetUrl, ...
// 可视化: appendMedia, appendCsvChart, appendStructure, createStructureScene
// 侧栏: loadSidebar, selectChat, createNewChat
// 对话: loadHistory, send, parseSseBuffer
// 项目: refreshPlan, refreshArtifacts, refreshTimeline
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

响应式断点: 1100px / 900px / 700px。
