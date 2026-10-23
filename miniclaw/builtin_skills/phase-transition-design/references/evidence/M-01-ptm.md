# M-01 · PTM 识别的对象

[证据目录](README.md) · [观测量指南](../common/observables.md)

**状态：方法依据。** 来源 S10：[OVITO PTM 手册](https://www.ovito.org/manual/reference/pipelines/modifiers/polyhedral_template_matching.html)，本次访问页面版本 3.16.1；S11：[Larsen 等原始论文](https://arxiv.org/abs/1603.05143)。本地 OVITO 安装版本未在本次探测。

| 事实 | 原文定位 |
| --- | --- |
| PTM 通过邻域模板匹配识别局部结构 | 手册开头；原论文摘要 |
| RMSD cutoff 会改变未识别与识别原子的分配 | 手册 Root-Mean-Square Deviation |
| Other 是未知/未匹配环境 | 手册 Structure Type 枚举 |
| 几何分类通常忽略元素种类；有序类型需额外考虑元素 | 手册 Ordering types |
| BCC 的 A2/B2 等有序类型与几何结构类型分开 | 同节 |

当前项目工具显式使用 FCC、HCP、BCC、ICO 和 Other 五类并输出原子比例，默认 cutoff 为 0.1。工具支持范围依据[本地声明](../common/capabilities.md)，不能将 OVITO 库的全部功能当成该工具已提供的功能。

**设计推论：** 先选择与候选结构对应的判据，报告模板与阈值。热扰动、界面或缺陷引起的标签变化需要相应证据解释；Other 与液相没有一一对应关系。
