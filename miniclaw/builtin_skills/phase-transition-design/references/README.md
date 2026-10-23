# 合金相变研究知识库

版本：0.3 Skill 封装版 · 整理日期：2026-10-21 · 范围：UNEP-v1、Pd–Cu–Ni–P、恒温恒压/温度路径及拉伸诱导结构变化。

这套知识库的首要任务是让研究 agent 给出完整的关键参数包：数值、单位、依据、状态、依赖及有限验证。默认用四页完成首轮设计，原始证据与背景按需展开。

**Agent 从[短入口](agent-entry.md)开始 → [参数决策表](decisions/parameter-table.md) → [NPT](decisions/npt.md)或[拉伸](decisions/tension.md) → [UNEP](decisions/unep-v1.md)或[Pd](decisions/pdcunip.md)。**

v0.1 的三名 agent 首轮评审发现：六道任务均未能形成完整参数交接，且读取成本较高。v0.2 为这些缺陷补上候选数值、预算、依赖计算、拉伸约束和任务路由；[行为评测记录](review/agent-tests/README.md)区分行为结果与物理验证。

## 从这里开始

| 你想看什么 | 入口 |
| --- | --- |
| 按问题寻找知识 | [分级总目录](index.md) |
| 两套势的身份与适用依据 | [UNEP-v1 档案](potentials/unep-v1/README.md)、[Pd–Cu–Ni–P 档案](potentials/pdcunip/README.md) |
| 快速给出温压、步长、耦合、盒子与运行预算 | [参数决策表](decisions/parameter-table.md)、[NPT 决策](decisions/npt.md) |
| 拉伸的轴、应变、速率、横向约束与参考盒长 | [拉伸决策](decisions/tension.md)、[计算契约](decisions/calculation.md) |
| “相比例”“相内成分”“相稳定性”分别需要什么 | [研究问题](common/research-questions.md)、[观测量与结论](common/observables.md) |
| 一个具体研究方案长什么样 | [UNEP-v1 结构演化专题](topics/unep-npt-structure.md)、[Pd–Cu–Ni–P 玻璃转变专题](topics/pdcunip-glass.md) |
| 哪些内容已经核实 | [证据卡片目录](evidence/README.md)、[来源与阅读状态](sources/README.md) |
| 如何提交完整研究方案 | [研究方案模板](templates/research-plan.md) |
| 检查每个字段的来源状态和引用 | [逐字段交接契约](decisions/handoff-contract.json)、[引用索引](decisions/reference-index.json) |
| 如何检查这套知识是否可靠 | [审阅问题与参考答案](review/questions.md) |

## 当前包含的实质内容

- 本地两套势及模型集合的身份、校验值、重复文件关系；Pd–Cu–Ni–P 训练和测试文件的构型统计。
- 两篇原始论文中已核对的研究范围、方法和部分参数；每个数值保留所在章节或图号。
- GPUMD 5.5 的单位、耦合参数含义、盒子控制和输出要求。
- 可交接的通用数值起点、CuNi/Ti/Pd 任务候选、加载与热路径计算、输出分辨率、最大追加预算。
- 结构演化、玻璃转变、化学有序与元素分配的研究边界，以及对应的工具缺口。
- 两份专题设计指南和一份完整交付模板。专题是设计起点，尚未在本项目执行物理验证。

## 阅读时怎样判断证据

**本地核实**表示实际读取或计算过文件；**论文报告**表示作者在指定条件下的结果；**方法依据**表示通用方法或软件约定；**设计建议**表示本样稿提出的安排；**待补充**表示目前没有足够材料。它们的含义见[证据说明](evidence/README.md)。

论文给出的条件保存在证据卡中；专题通过链接引用。这样可以避免同一个参数在不同页面逐渐变成不同版本。

## 使用范围

这套分级知识已整体迁入 [phase-transition-design Skill](../SKILL.md) 的 references；计算与维护入口在同一 Skill 的 scripts。项目中的 researcher 可发现该 Skill，已运行进程是否重新加载尚未核实。v0.3 只调整封装、导航和计算入口，不改变候选参数政策；此前 agent 评测对应原知识版本，不证明新封装已通过行为盲测。迁移前后文件映射与哈希见[迁移指纹](review/skill-migration.json)。尚未建立向量索引或运行相变模拟。

两篇论文的补充材料尚未逐项核对；本地没有对应温压条件的构型元数据时，不从坐标推测训练温度。当前没有为任意合金提供“保证发生相变”的统一温度和时长。

实际使用前应核对势文件身份、目标成分、研究类型和所需工具。CAND 规则是明确标记的探索设计政策，不能称为材料验证值；缺少材料专用验证不应让所有参数空缺。行为评测通过也不能证明势精度或相变结论正确。

[变更记录](CHANGELOG.md) · [机器可读目录](catalog.json) · [本地资产快照](assets/local-inventory.json) · [内容自检记录](review/validation.json)
