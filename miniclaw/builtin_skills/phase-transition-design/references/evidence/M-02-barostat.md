# M-02 · 压力控制与涨落

[证据目录](README.md) · [NPT 参数指南](../common/npt-parameters.md)

**状态：方法依据。** 来源 S12：Bernetti、Bussi，Pressure control using stochastic cell rescaling，J. Chem. Phys. 153, 114107 (2020)，[作者预印本](https://arxiv.org/abs/2006.09250)，DOI 10.1063/5.0020514。本次核对摘要；未在本项目重现其基准。

作者说明 Berendsen 压控可有效松弛体积，但不能生成正确体积涨落；随机盒缩放通过适当噪声项改善这项采样性质。GPUMD 5.5 的 npt_ber、npt_scr 定义见[C-01](C-01-gpumd55.md)。

**设计推论：** 当研究需要涨落统计时，算法选择与验证应成为方案的一部分。温压均值达到设定值，只证明均值的一项性质，不能替代采样分布的检查。

**待验证：** 当前体系的实际采样质量、耦合选择及运行实现。这里没有给出“任意相变都用某一种压控”的规则，也没有覆盖其他算法的全部理论。
