# 开发备忘

> 自我进化阶段 1–20 的开发与验收说明见 [`evolution.md`](evolution.md)。当前回归基线为 134 项测试通过，阶段 1–20 验收脚本全部通过。修改自进化门禁、治理逻辑或执行工具时应视为高风险变更，必须补测试并保留 Git 可回滚节点。

## 调试

| 场景 | 方法 |
|------|------|
| 前端 console | F12 → 结构加载错误会 `console.error("结构加载失败:", path, e)` |
| 后端日志 | `logger = get_logger(__name__)` → Agent 循环、工具调用、viz 渲染均有日志 |
| SSE 流调试 | Network 面板 → `/api/chat` → EventStream tab，观察 delta/viz/done 时序 |
| 结构解析失败 | `parseStructureFile` 回退到 `parseDumpFirstFrame`，检查文件内容是否匹配已知格式 |
| 黑屏 | `typeof THREE` 是否为 undefined、WebGL 上下文数（Chrome ~8-16 上限） |
| Token 用量 | `session.metadata["total_tokens"]` + `last_run_tokens` |
| GPUMD 检索 | 调用 `search_gpumd_docs`；返回首行会标明“向量检索”或“关键词检索” |
| RAG 索引状态 | 查看 `~/.miniclaw/workspace/rag/gpumd-script/index.json` 的 `model`、`count`、`schema_version` |

## 兼容性注意事项

| 项目 | 说明 |
|------|------|
| **Windows 路径** | `resolve_workspace_path()` 统一处理绝对/相对路径，强制限制在 workspace 内。前端 `assetUrl` 用 `encodeURIComponent` |
| **OVITO** | 需要 `ovito` Python 包 + 有效 license（学术免费）。未安装则 GIF 渲染失败，不影响文本对话 |
| **CNA 分析** | `OVITO_APPLY_CNA_ON_DUMP = True`（`constants.py`），仅对 LAMMPS dump 文件生效 |
| **marked.js 全局** | 通过普通 `<script>` 加载为全局变量。ES module (`app.js`) 用 `typeof marked` 检查，strict mode 安全 |
| **Three.js ESM** | `import * as THREE from '/static/three.module.js'` — 硬编码路径，勿改 import map |
| **SSE vs WebSocket** | WebChat 用 SSE（单向流），Gateway 用 WebSocket（双向）。两者独立，不共享连接 |
| **asyncio.create_task** | viz 渲染用 `create_task` 启动，不阻塞 agent 循环。`finally` 中 `gather` 等待 |
| **火山 vision embedding** | 带日期的 `doubao-embedding-vision-*` 必须走 `/embeddings/multimodal`，文本输入格式为 `[{"type":"text","text":"..."}]` |
| **embedding 密钥** | 优先使用用户配置 `~/.miniclaw/config.yaml` 的 `rag.api_key`；也支持 `MINICLAW_EMBEDDING_API_KEY`、`ARK_API_KEY`，不要提交到 Git |

## 常见修改场景

### 添加新的可视化文件类型

1. `viz/constants.py` — 添加扩展名到 `STRUCTURE_EXT` / `MEDIA_IMAGE_EXT` / `VISUAL_EXT`
2. `viz/auto.py` — `_classify_new_file()` 添加新类型分支
3. `web/files.py` — `VISUAL_EXT` 引用相同常量
4. 前端 `app.js` — `STRUCTURE_EXTS` / `VISUAL_FILE_EXTS` 同步更新

### 修改 Agent 行为

1. `agent.py` — `_process_message()` 主循环
2. `config.yaml` — `max_tool_rounds`, `temperature`, `max_tokens`
3. `hooks.py` — 在 `before_agent` / `after_tool` / `after_llm` 等注入逻辑

### 修改 WebChat UI

1. `static/index.html` — 结构
2. `static/style.css` — 样式
3. `static/app.js` — 逻辑

### 添加新 Channel

1. 新建 `channels/xxx.py`，继承 `BaseChannelAdapter`
2. 实现 `start()` / `stop()` / `send_message()`
3. `main.py` 中注册 adapter

### 更新 GPUMD 知识库

```powershell
# 1. 全量同步语料；这一步不调用 embedding API
python -m miniclaw.rag.sync_gpumd

# 2. 增量更新索引；只向量化新增和正文变化的块
python -m miniclaw.rag.cli build
```

构建结果会报告 `总计 / 复用 / 新增或更新 / 删除失效`。只有更换模型或显式排障时使用：

```powershell
python -m miniclaw.rag.cli build --full
```

增量键由 `文档 ID + SHA-256(标题、章节、正文) + embedding 模型名` 组成。仅来源或 metadata 变化时会更新索引记录但复用向量。索引采用临时文件原子替换，API 失败后旧索引仍可用。

### 修改 GPUMD Skill

- Skill 文件：`~/.miniclaw/workspace/skills/gpumd-script/SKILL.md`
- 语料文件：`~/.miniclaw/workspace/skills/gpumd-script/references/corpus.jsonl`
- 修改后运行 Skill 校验，并重启长期运行的 MiniClaw 进程以刷新工具和技能缓存。
- 不要手工维护大批官方正文；优先修改 `rag/sync_gpumd.py` 后重新同步。

## Workspace 安全模型

`tools/paths.py` 的 `resolve_workspace_path()` 是所有文件操作的安全入口：
- 相对路径 → 相对于 `active_workspace_dir()` 解析
- 绝对路径 → 验证是否在 workspace 内
- 项目隔离 → `workspace_scope(dir)` 上下文管理器临时切换 workspace 根
- 路径穿越检测 → `resolved.relative_to(root)` 若不在则 raise
