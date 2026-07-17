# GPUMD Skill 与 RAG

## 当前状态

| 项目 | 当前值 |
|------|--------|
| GPUMD 手册版本 | 5.5 稳定版 |
| 官方手册 | 117 页，408 个文档块 |
| 官方 Tutorials | 109 个文件，295 个文档块 |
| 语料总量 | 703 块，225 个独立来源 |
| embedding 模型 | `doubao-embedding-vision-251215` |
| API 类型 | 火山方舟 `/api/v3/embeddings/multimodal` |
| 向量维度 | 2048 |
| 索引格式 | JSON schema v2，703 条 |
| Skill | `gpumd-script` |
| Agent 工具 | `search_gpumd_docs` |

## 文件位置

项目代码：

| 文件 | 职责 |
|------|------|
| `miniclaw/rag/sync_gpumd.py` | 从官网和 Tutorials 同步、解析、切分语料 |
| `miniclaw/rag/embedding.py` | OpenAI-compatible 与火山多模态 embedding 客户端 |
| `miniclaw/rag/store.py` | JSON 索引、增量构建、余弦检索、关键词降级 |
| `miniclaw/rag/cli.py` | `build` / `--full` 命令入口 |
| `miniclaw/tools/rag_tool.py` | Agent 工具、中文查询扩展、来源重排 |
| `miniclaw/skills/loader.py` | 标准 YAML 和旧 XML Skill 元数据兼容 |

运行时数据：

```text
~/.miniclaw/workspace/skills/gpumd-script/
├── SKILL.md
├── agents/openai.yaml
└── references/corpus.jsonl

~/.miniclaw/workspace/rag/gpumd-script/
└── index.json
```

Windows 默认展开为 `C:\Users\<用户名>\.miniclaw\...`。

## 配置

推荐把密钥放入用户本地 `~/.miniclaw/config.yaml`，不要写入仓库配置：

```yaml
rag:
  enabled: true
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model: doubao-embedding-vision-251215
  api_type: auto
  api_key: "本地密钥"
  dimension: 2048
  batch_size: 16
  max_concurrency: 4
```

密钥优先级：`rag.api_key` → `MINICLAW_EMBEDDING_API_KEY` → `ARK_API_KEY`。

`api_type: auto` 的选择逻辑：

- `doubao-embedding-vision-六位日期` → 火山 `/embeddings/multimodal`
- 其他模型 → OpenAI-compatible `/embeddings`
- 可显式设置 `volcengine_multimodal` 或 `openai`

## 语料同步

```powershell
python -m miniclaw.rag.sync_gpumd
```

同步器执行全量语料刷新，但不调用 embedding：

1. 下载 `gpumd.org/sitemap.xml`，发现 GPUMD 5.5 页面并修复当前 sitemap 的缺失斜杠。
2. 默认 8 线程下载页面，每页最多重试 3 次。
3. 从 `div[role="main"]` 提取 `h1-h4`、段落、代码块和表格。
4. 按章节切块，最大约 2800 字符，相邻长块重叠 240 字符。
5. 下载 GPUMD-Tutorials `main` 分支 ZIP，在内存中筛选 `.md`、`.in`、`.sh` 和小型 `model.xyz`。
6. 按唯一 ID 排序后覆盖写入 `corpus.jsonl`。任一页面失败时不会覆盖原语料。

当前限制：手册版本固定为 5.5，Tutorials 跟随 `main`，官网正文解析依赖 Sphinx 的 `div[role="main"]` 结构。

## 增量向量索引

默认运行：

```powershell
python -m miniclaw.rag.cli build
```

每个文档块使用以下增量键：

```text
embedding 模型名
文档 ID
SHA-256("标题\n章节\n正文")
```

处理规则：

| 情况 | 行为 |
|------|------|
| 模型、ID、内容哈希相同 | 复用旧向量，刷新 document metadata |
| 新 ID | 调用 API 生成向量 |
| ID 相同但标题/章节/正文变化 | 重新向量化 |
| 旧 ID 在新语料中消失 | 从新索引删除 |
| 模型名变化 | 自动全量重建 |
| `--full` | 忽略旧索引，全部重新向量化 |

强制全量：

```powershell
python -m miniclaw.rag.cli build --full
```

输出会报告：总计、复用、新增或更新、删除失效。索引先写同目录临时文件、`fsync` 后通过 `os.replace` 原子替换；API 或进程失败不会破坏旧索引。

## 检索路径

Agent 调用：

```text
search_gpumd_docs(query="GPUMD HNEMD 热导率怎么写", top_k=5, version="5.5")
```

模式选择：

1. `rag.enabled=true`、密钥存在且索引存在：远程生成查询向量，执行余弦检索。
2. 任一条件缺失：从 `corpus.jsonl` 执行关键词检索。
3. 中文概念先扩展为 GPUMD 英文术语和命令。
4. 召回候选做来源权重调整，同一来源最多保留两个结果。

索引必须与当前模型名一致，否则检索会提示重建。语料同步后应紧接着运行增量 `build`。

## 验证

```powershell
python -m pytest -q
```

当前回归基线为 56 项测试，覆盖：

- 多模态 API 路径、payload 与响应解析
- vision 模型自动路由
- 中文关键词扩展与结果重排
- 文档同步解析和切块
- 增量复用、新增、修改、删除
- 模型变化强制全量
- embedding 失败时旧索引保持不变

修改 Skill 后另外运行：

```powershell
$env:PYTHONUTF8='1'
python C:\Users\<用户名>\.codex\skills\.system\skill-creator\scripts\quick_validate.py C:\Users\<用户名>\.miniclaw\workspace\skills\gpumd-script
```
