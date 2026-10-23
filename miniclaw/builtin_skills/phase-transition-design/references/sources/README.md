# 来源、定位与阅读状态

[首页](../README.md) · [证据目录](../evidence/README.md) · [机器可读来源表](sources.json)

核查日期：2026-10-20。以下只列实际用于首版的来源；没有把检索到的相关文章全部列为已读。原文保留公开链接，库内保存中文摘要、定位与适用边界，没有复制整篇论文。

## 原始势函数资料

| ID | 来源与定位 | 首版阅读/核查状态 |
| --- | --- | --- |
| S01 | Song 等，General-purpose machine-learned potential for 16 elemental metals and their alloys，Nature Communications 15, 10208 (2024)，[正文](https://www.nature.com/articles/s41467-024-54554-x) | 核对训练数据构建、Fig. 2、Fig. 7、Methods 中 MCMD 段落；补充 PDF 尚未逐项核对 |
| S02 | Fan，Dataset for UNEP-v1，Zenodo v2，[发布记录](https://zenodo.org/records/11533864) | 核对文件目录、主模型发布 MD5；没有下载大型训练/测试压缩包 |
| S03 | Zhao 等，Development of a neuroevolution machine learning potential of Pd-Cu-Ni-P alloys，Materials & Design 231, 112012 (2023)，[论文](https://doi.org/10.1016/j.matdes.2023.112012) | 通过出版方可检索正文核对 §3 开头、§3.2/Fig. 4、§3.3/Fig. 5 及总结；补充 Word 材料尚未核对 |
| S04 | grtheaory/PdCuNiP_NEP，[作者仓库](https://github.com/grtheaory/PdCuNiP_NEP) | 核对 README 与本地文件组织；本地包尚未与当前在线压缩包逐文件比对 |

## 固定版本的软件资料

以下实际阅读了本地已安装的 Markdown 正文，页头标明 GPUMD 5.5 与官方源。JSON 中记录本地正文 SHA-256 及 catalog 的来源校验信息，供后续发现变动；本次没有重新下载并逐字比对官方 RST。

| ID | 固定来源 | 定位与用途 |
| --- | --- | --- |
| S05 | [ensemble.rst，v5.5](https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/input_parameters/ensemble.rst) | Units and suggested parameters：单位、耦合比值、参考参数 |
| S06 | [ensemble_standard.rst，v5.5](https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/input_parameters/ensemble_standard.rst) | npt_ber 的三类盒子控制及 npt_scr 定义 |
| S07 | [time_step.rst，v5.5](https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/input_parameters/time_step.rst) | Syntax / Caveats：时间单位、固定/自适应设置、传播 |
| S08 | [dump_exyz.rst，v5.5](https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/input_parameters/dump_exyz.rst) | Syntax / Caveats：步数间隔、附加字段、追加与传播 |
| S09 | [thermo_out.rst，v5.5](https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/output_files/thermo_out.rst) | File format：热力学与盒子分量；本库采用经典 MD 范围 |

## 方法资料

| ID | 来源 | 已核对范围 |
| --- | --- | --- |
| S10 | [OVITO PTM 手册](https://www.ovito.org/manual/reference/pipelines/modifiers/polyhedral_template_matching.html)，访问页面 3.16.1 | Structure Type、RMSD、Ordering types；没有据此声称本地安装版本相同 |
| S11 | Larsen、Schmidt、Schiøtz，Robust Structural Identification via Polyhedral Template Matching (2016)，[作者预印本](https://arxiv.org/abs/1603.05143)，DOI 10.1088/0965-0393/24/5/055007 | 摘要中的方法目标；具体输出解释主要依据 S10 |
| S12 | Bernetti、Bussi，Pressure control using stochastic cell rescaling (2020)，[作者预印本](https://arxiv.org/abs/2006.09250)，DOI 10.1063/5.0020514 | 摘要中 Berendsen 体积涨落限制及随机盒缩放目标；未重现基准 |
| S13 | Chodera，A Simple Method for Automated Equilibration Detection in Molecular Simulations (2016)，[出版方](https://pubs.acs.org/doi/10.1021/acs.jctc.5b00784)、[作者说明](https://www.choderalab.org/publications/2015/6/30/a-simple-method-for-automated-equilibration-detection-in-molecular-simulations) | 摘要及作者方法说明；未实现该算法 |

## 本地项目与资产

S14 为[当前后处理角色](../../../../agents/profiles/postprocessor/agent.yaml)及其 [SYSTEM.md](../../../../agents/profiles/postprocessor/SYSTEM.md)；用于记录当前声明的分析口径与能力。核对日期的文件校验值保存于 JSON，未来变动时需要更新能力页。

L01 为[本地势与数据快照](../assets/local-inventory.json)。它由[维护工具](../../scripts/refresh_inventory.py)生成，读取用户指定根目录的资产；没有修改输入势或原始数据。

## 明确保留的缺口

- 两篇论文的补充材料尚未逐项核对；未知参数没有补填。
- 还没有选定并审计一个 UNEP-v1 具体合金的完整相变方案。
- Pd–Cu–Ni–P 的 Tg 拟合窗口、完整制备路径和部分控制参数尚未形成完整复现契约。
- 没有运行 NEP 预测、复现文献图或模拟物理过程。
- v0.2 已开展研究设计子 agent 测试，见[行为记录](../review/agent-tests/README.md)；这不等于物理复现或统计工具验收。
- 已有方法依据不自动成为当前工具功能；以[能力清单](../common/capabilities.md)及实际运行结果为准。

补充时应更新来源阅读状态、受影响的证据卡、专题和变更记录。新增案例必须绑定实际势文件与原始输出，不把草稿建议自动晋升为已验证经验。

## v0.2 新增来源

- S15：[GPUMD 5.5 deform](https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/input_parameters/deform.rst)，本地正文已核对；[C-02](../evidence/C-02-deformation.md)。
- S16：本地 GPUMD Skill 的 NPT、单轴/双轴、退火示例及项目 helper 的 CLI/参数验证；示例值按工程候选引用，不当材料验证。
- S17：[NIST-JANAF Ti α 表](https://janaf.nist.gov/tables/Ti-002.html)，核对标准态 0.1 MPa 与 1166 K 标记；作为 Ti 探索先验，不当 UNEP 预测值。
- S03 新核对 §3.4/Fig. 6 力学条件；[P-03](../evidence/P-03-paper-mechanics.md)明确保留未核实的应变口径与控制细节。
