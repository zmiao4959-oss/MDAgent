# P-01 · Pd–Cu–Ni–P 本地模型与数据

[证据目录](README.md) · [势函数档案](../potentials/pdcunip/README.md)

**状态：本地核实。** 核查日期：2026-10-20。

| 项目 | 事实 |
| --- | --- |
| 主文件 | `PdCuNiP/nep4.txt` |
| 文件大小 | 221651 字节 |
| 格式与元素顺序 | `nep4_zbl`；Pd Cu Ni P |
| ZBL / cutoff | `zbl 0.9 1.8` / `cutoff 6 4 127 42` |
| n_max / basis_size / l_max / ANN | `8 8` / `8 8` / `4 2 0` / `50 0` |
| SHA-256 | `aec2bc972783f58e523c90574a96ebaad6b37a29bc3853e16099d4d43af96ad8` |
| 本地同内容文件 | 作者包中 `nep.population160.generation1013200.txt` |

作者包的 README 描述与 [S04 作者仓库](https://github.com/grtheaory/PdCuNiP_NEP)一致，且附 nep.in、restart、train.xyz、test.xyz。本地与当前在线压缩包尚未逐文件比对，因此不把它标成已确认的最新在线版本。

[训练/测试数据审计](../potentials/pdcunip/dataset-audit.md)记录构型数、元素计数及元数据。数据文件没有显式逐帧温压标签。本次只核实文件事实，没有运行训练、预测误差或相变模拟。

**允许结论：** 主文件和本地分发包模型一致，数据可被完整逐帧读取。

**尚未确认：** 各成分误差、数据独立性、晶态/液态/玻璃态的逐构型分类及温压覆盖。这些不能由文件名或目录直接确定。
