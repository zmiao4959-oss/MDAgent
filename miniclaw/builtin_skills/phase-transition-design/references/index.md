# 分级总目录

[返回首页](README.md)

## 研究 agent 的优先路径

[短入口](agent-entry.md) → [参数决策表](decisions/parameter-table.md) → 按任务选 [NPT](decisions/npt.md) / [拉伸](decisions/tension.md) → 按势选 [UNEP-v1](decisions/unep-v1.md) / [Pd–Cu–Ni–P](decisions/pdcunip.md)。通常读四页即可提出完整首轮参数，不必依次通读下面所有背景层级。

## 第一级：先明确研究问题

读[研究问题与证据](common/research-questions.md)，把任务写成“在什么条件下，观察或估计什么”。相变温度、有限时间内的结构变化、相内元素含量和玻璃转变需要不同的证据。

## 第二级：匹配势与体系

| 条件 | 去哪里 |
| --- | --- |
| 使用本地 `UNEP-v1-main.txt` 或其模型集合 | [UNEP-v1](potentials/unep-v1/README.md) → [本地身份 U-01](evidence/U-01-local-model.md) |
| 使用本地 `PdCuNiP/nep4.txt` | [Pd–Cu–Ni–P](potentials/pdcunip/README.md) → [本地身份 P-01](evidence/P-01-local-model.md) |
| 势文件相同但成分或初态不同 | 沿用身份，重新查适用证据；不要沿用另一成分的转变温度 |
| 文件名相同但校验值不同 | 建立新的版本记录，原定量证据的适用性需要重查 |
| 不是这两套势 | 当前资料不覆盖其具体适用性，通用方法页仍可参考 |

## 第三级：选择研究专题

| 研究目标 | 阅读路线 |
| --- | --- |
| NPT 下 FCC/HCP/BCC 等结构随时间或温度变化 | [UNEP-v1 结构演化](topics/unep-npt-structure.md) |
| 单轴/双轴拉伸诱导结构变化 | [拉伸参数决策](decisions/tension.md) → 对应势决策包 |
| Pd–Cu–Ni–P 的玻璃制备或玻璃转变 | [玻璃转变专题](topics/pdcunip-glass.md) → [论文参数 P-02](evidence/P-02-paper-protocols.md) |
| 化学有序、析出、相内元素分配 | [观测量](common/observables.md) → [能力边界](common/capabilities.md) → 查方法与工具缺口 |
| 要求平衡相图或精确相界 | [研究问题](common/research-questions.md)；当前专题只能作为方法论证和探索起点 |

## 第四级：补齐设计细节

1. [NPT 参数设计](common/npt-parameters.md)：单位、物理时间、控制方式和参数出处。
2. [平衡、采样与停止条件](common/sampling.md)：哪些量需要统计，哪些验证应预先列入预算。
3. [观测量与结论](common/observables.md)：输出究竟能说明什么。
4. [当前执行能力](common/capabilities.md)：对照已有专业角色是否能完成。
5. [研究方案模板](templates/research-plan.md)：形成可交接的完整方案。

## 第五级：回到证据

[本地与文献证据卡](evidence/README.md) → [来源清单](sources/README.md)。遇到冲突，记录各自势文件、体系、路径、采样与计算方法；先判断是否在比较同一件事。

一次任务只读相应分支。默认不把训练轨迹、全部原文或所有专题加入上下文。
