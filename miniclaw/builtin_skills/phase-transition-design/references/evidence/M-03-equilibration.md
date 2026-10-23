# M-03 · 初始过渡与有效采样

[证据目录](README.md) · [采样指南](../common/sampling.md)

**状态：方法依据。** 来源 S13：Chodera，A Simple Method for Automated Equilibration Detection in Molecular Simulations，J. Chem. Theory Comput. 12, 1799–1805 (2016)，[DOI 10.1021/acs.jctc.5b00784](https://pubs.acs.org/doi/10.1021/acs.jctc.5b00784)。本次核对摘要及[作者说明](https://www.choderalab.org/publications/2015/6/30/a-simple-method-for-automated-equilibration-detection-in-molecular-simulations)。

该方法针对平衡性质估计中的初始过渡，以保留生产区间的有效不相关样本数为准则选择丢弃区间。

**设计推论：** 报告平均值时需要考虑初始过渡与相关性；“运行到某个步数”并非通用的平衡证明。转变动力学轨迹不应未经判断就按平衡统计自动截掉变化阶段。

**当前能力：** 本项目已有等权摘要不等于已实现该方法。自相关、有效样本数和不确定度工具尚未在本次确认；设计中需要时应标注缺口。
