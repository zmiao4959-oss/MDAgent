# U-01 · UNEP-v1 本地模型身份

[证据目录](README.md) · [势函数档案](../potentials/unep-v1/README.md)

**状态：本地核实。** 核查日期：2026-10-20。对象：用户指定 NEP 文件夹中的 16 元模型。

| 项目 | 事实 |
| --- | --- |
| 本地文件 | `16元/UNEP-v1-main.txt`，绝对路径见资产快照 |
| 文件大小 | 1127108 字节 |
| 格式 | `nep4_zbl` |
| 元素 | Ag Al Au Cr Cu Mg Mo Ni Pb Pd Pt Ta Ti V W Zr |
| ZBL / cutoff | 文件头 `zbl 1 2` / `cutoff 6 5 200 134` |
| n_max / basis_size / l_max / ANN | `4 4` / `8 8` / `4 2 1` / `80 0` |
| SHA-256 | `8f97c30cc366c30e687a8672bd8d76e624a16caf4337b1277a44aa166c5bceb4` |
| 本地及作者发布 MD5 | `337669f3a94bd42ebb765e773deaf05e`，一致 |
| 同内容文件 | `16元/ensemble-nep-models/ensemble-nep-models/nep-44-80.txt` |

校验依据：[本地 JSON](../assets/local-inventory.json)与 [S02 作者发布记录](https://zenodo.org/records/11533864)。MD5 用于与发布记录比对，SHA-256 用于本地版本跟踪。

本地集合另有 nep-44-30、44-50、64-50、64-80、66-30、66-50、66-80；八个集合成员中包含与主模型相同的 nep-44-80。文件头及各自 SHA-256 保存在快照。

**允许结论：** 本地主文件与该发布文件内容一致，当前明确支持的元素表可核对。

**尚未确认：** 目标成分的相变精度、指定温压的有效性、模型集合误差校准及本地运行表现。文件一致不能替代这些验证。
