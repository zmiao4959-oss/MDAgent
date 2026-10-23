# GPUMD 文件知识库与脚本示例

## 当前实现

GPUMD 专用资料固定为 5.5。Skill 负责使用方法和任务目录，完整正文位于同一个 Skill 文件夹。RAG 索引直接读取这些正文，搜索只返回路径与简短片段；agent 使用现有 read 阅读需要的文件。read_gpumd_command 已移除工具注册和 gpumd-generator 允许列表，没有新增全局规则。

运行时入口：
`C:/Users/35059/.miniclaw/workspace/skills/gpumd-script/SKILL.md`

```text
gpumd-script/
├── SKILL.md
├── index.md                     # 中文任务 → 相似例子 + 参考页
├── catalog.json                 # 名称、别名和路径
├── commands/                    # 71个命令参考页及栏目概览
├── files/input/                 # model.xyz、run.in等
├── files/output/
├── potentials/                  # 势模型、nep.txt、元素对应检查
└── examples/
    ├── index.md
    ├── manifest.json
    ├── prepare_tension.py
    ├── tension_workflow.py
    ├── npt-relaxation/
    ├── uniaxial-tension/
    ├── biaxial-tension/
    ├── annealing/
    ├── msd-rdf/
    └── hnemd/
```

## 示例怎么用

先从 index.md 找最接近的任务，读对应 example.md 和脚本；按需读关联命令。六组示例分别覆盖 NPT 弛豫、单轴拉伸、xy 双轴拉伸、升温保温降温、NVT 平衡后 NVE 计算 MSD/RDF、HNEMD 晶格热导率。

每例写明输入、默认参数、阶段、输出、命令来源和适用边界。示例不生成用户模型或势，不把温度、时长、驱动力等示范值视为用户要求。

拉伸使用两阶段输入：tension_workflow.py create 一次生成正式弛豫、短试跑、加载模板和项目内 workflow.py。项目无需依赖原 Skill 路径。NPT 完成后，在项目内运行 workflow.py prepare-load，从真实 restart.xyz 分别读取 Lx/Ly，通过 prepare_tension.py 计算增量并生成加载目录 run.in。它原样保留结构与速度，记录参考和目标盒长，不重新初始化速度。仅支持三向周期、轴对齐正交盒；不会判断弛豫是否充分，也不启动 MD。

缺少正式 restart 时返回 pending，不以初始模型或短试跑结果替代正式参考。参考、势或设置变化生成新的加载目录；相同输入可复用，但已被修改的受管理输入拒绝覆盖。

以弛豫盒长 35/36/37 Å、xy 各30%、100000步为例，增量是 0.000105/0.000108 Å每步，目标 x/y 为45.5/46.8 Å。横向未指定的最终盒长记录为 null，按压控响应决定。

## 语料与搜索

当前包含131份固定官方原文：117份gpumd手册页、12份势模型页、nep.txt和训练type两份相关说明。另有1份依据官方源码编写的NEP元素对应FAQ、6份示例说明，共138条向量记录，2048维，沿用原配置的doubao-embedding-vision-251215。

官方RST只保守转换标题、代码块和已解析引用；未知表格、数学和指令保留。完整命令行、单位、示例和Caveats保留在同一文件。RAG content与磁盘Markdown逐字一致，记录来源正文哈希及发布正文哈希。官方原始文件缓存用于再生成和审计。

search_gpumd_docs默认定位3项、最多5项，总预算5000字符，每项最多320字prose片段；不倾倒完整页面。纯名称可跳过embedding；含potential/model.xyz的自然语言关系问题继续语义检索。NEP元素对应查询优先专门FAQ，缺失时明确未覆盖，普通命令页不充当关系答案。类似脚本查询按示例任务标签排序；NPT弛豫查询优先专门弛豫例子，避免被含NPT前段的拉伸例子抢占。

## 维护

仓库资源：

| 路径 | 作用 |
| --- | --- |
| miniclaw/rag/sync_gpumd.py | 固定源同步、安装示例资产、生成正文和语料 |
| miniclaw/rag/library.py | Markdown、任务/别名目录与catalog发布 |
| miniclaw/rag/gpumd_examples/ | 六组示例及衔接helper的维护源 |
| miniclaw/rag/gpumd_knowledge/ | 源码依据FAQ与Skill入口模板 |
| miniclaw/tools/rag_tool.py | 紧凑文件定位 |
| miniclaw/agents/profiles/gpumd-generator/ | agent流程及工具范围 |

在仓库目录执行：

```powershell
python -X utf8 -m miniclaw.rag.sync_gpumd
python -X utf8 -m miniclaw.rag.cli build
```

默认使用缓存；加--refresh重新下载固定版本原文。同步会安装manifest声明的示例文件，不复制Python缓存；不存在Skill入口时会安装入口模板，已有入口保留。正文和目录自动生成，示例维护源在仓库。已有入口需要变更时编辑源模板并同步到运行时入口。

临时发布：

```powershell
python -X utf8 -m miniclaw.rag.sync_gpumd --output C:/temp/gpumd/corpus.jsonl --skill-dir C:/temp/gpumd/library
python -X utf8 -m miniclaw.rag.cli build --corpus C:/temp/gpumd/corpus.jsonl --index C:/temp/gpumd/index.json
```

语料和索引仍在 `C:/Users/35059/.miniclaw/workspace/rag/gpumd-script/`。本次质量记录为quality_report.json，来源及文件哈希为build_manifest.json。旧Skill、知识库、相关代码及退休的references/scripts已备份到：
`C:/Users/35059/Desktop/test-codex/.codex-backups/gpumd-library-20261017-153424/`。

运行中的MiniClaw需要重启，才会重新加载工具注册、检索代码和profile工具列表；磁盘文档已发布。

## 验证范围

本轮相关pytest套件94项通过，覆盖项目生成与搬迁、未来参考缺失、正式/短试跑数据隔离、应变速率、参考变化、拒绝覆盖，以及正文、目录、检索、CLI、Skill与profile。Skill入口验证通过。

本轮发布验证：138条磁盘/语料/索引正文匹配，所有生成Markdown本地链接可解析，6份修改相关运行时资产与维护源一致。增量构建复用136条、更新2条拉伸示例的embedding。上一轮12个真实查询首项均为预期资料或示例，检索结果约320–1318字符；本轮未重跑该完整查询集。

另进行独立文件生成演练，使用真实4000原子CuNi模型及nep4势，经Skill和本地链接生成NPT后双轴30%、30000步项目。只读7份知识/示例文件及角色SYSTEM，生成正式/试跑输入、模板、项目内衔接入口和说明；缺少正式restart时正确返回pending，即可交付编写任务。本轮未启动GPU模拟，不据此声明物理结果或MiniClaw模型总轮次、token降幅。

## 2026-10-17 完成条件与两阶段衔接优化

最新实际运行的主要额外开销发生在正式模拟和等待：编写脚本任务为获得正式参考盒长，又跑完100000步NPT及30000步加载。知识文件已没有重复检索，因此本轮针对GPUMD专用流程和示例，不调整全局规则或模型协议。

- 编写任务完成于输入/模板、项目内衔接入口和说明齐备；正式盒长是将来运行时读取的数据，不要求提前跑完整段正式计算。用户明确要求运行、提交或完成时，仍沿用授权执行到指定状态。
- 新增自包含项目生成入口。create只准备文件；项目内prepare-load读取对应真实restart，输出实际加载输入路径。项目搬迁后仍可衔接，重跑弛豫不会沿用旧增量。
- 短试跑沿trial/relax → prepare-load --trial → trial/load衔接，默认每段200步且不超过正式步数。保持正式工程应变率，缩短试跑实际应变，不把试跑结构写入正式参考。
- 脚本编写任务短试跑最多追加两次状态查询；正式运行按用户要求跟踪。直接用返回的运行/日志路径读取必要状态与末尾，避免长时间固定休眠、重复列目录和读整份thermo。
- 按示例CLI使用helper，仅在报错或修改helper时读实现；输出说明区分文件就绪、试跑通过、正式启动与正式完成，不把零外压设定或大应变收缩写成未经验证的物理结论。

本轮备份：`C:/Users/35059/Desktop/test-codex/.codex-backups/gpumd-finish-contract-20261017-164227/`。
独立演练与检查：`C:/Users/35059/Desktop/test-codex/gpumd-finish-contract-rebuild/`。
运行时文件和索引已更新，重启MiniClaw后加载新的角色规则。
