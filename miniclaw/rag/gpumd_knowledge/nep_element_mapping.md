# NEP 势文件与 model.xyz 的元素对应

适用：GPUMD 5.5，使用元素符号的扩展 XYZ 模型和 NEP 势。检索词：NEP、nep4_zbl、元素顺序、元素映射、species、type、element mapping。

## 直接结论

GPUMD 从势文件第一行读取元素列表，再把 `model.xyz` 每个原子的 `species` 字符串与该列表逐一匹配。内部类型编号是元素在势文件列表中的位置，从 0 开始。模型原子在文件中的排列顺序不决定类型编号，也不需要把势文件或原子按原子序数排序。

例如势文件开头为：

```text
nep4_zbl 4 Pd Cu Ni P
```

则 Pd、Cu、Ni、P 对应内部 type 0、1、2、3。模型中的 `Cu` 自动匹配 type 1，`Ni` 自动匹配 type 2。模型可以只含势文件支持元素的子集；不要为了匹配势头部而添加不存在的元素或修改已训练势的元素顺序。

## 生成脚本前检查

1. 读取势文件第一行，确认声明的元素数与后续元素符号数量一致。
2. 读取模型第二行的 `Properties`，定位 `species` 字段；其常见形式为 `species:S:1:pos:R:3`。按实际字段顺序检查，不能把速度、坐标或 type 编号当作元素。
3. 统计模型元素及数量，确认每一种模型元素都在势文件的元素列表中。对未被势支持的元素，GPUMD 初始化会报错。
4. 试跑日志会报告势的 `type n (元素 with Z = 原子序数)` 和各 type 的原子数量，可据此核对。势文件覆盖元素只证明接口支持，不能证明该势对当前成分、温度和变形范围已经验证。

已确认头部、模型元素和初始化日志一致时，无须再次检索“是否必须按原子序数排序”。当前问题若涉及训练数据覆盖或势的精度，应查该势的来源和验证资料。

## 相关资料

- [potential 命令](../commands/potential.md)
- [model.xyz 格式](../files/input/model_xyz.md)
- [NEP 原理](nep.md)
- [nep.txt 文件](nep_txt.md)

## 官方依据与核对位置

这是依据固定 v5.5 源码整理的使用说明，区别于官方原理页的逐字原文。

- [src/model/read_xyz.cu](https://github.com/brucefan1983/GPUMD/blob/v5.5/src/model/read_xyz.cu)：`get_atom_symbols` 读取势文件元素列表；`read_xyz_in_line_3` 按元素符号匹配列表并设置 `cpu_type`，找不到对应符号时报错。
- [src/force/nep.cu](https://github.com/brucefan1983/GPUMD/blob/v5.5/src/force/nep.cu)：`NEP::NEP` 校验势文件首行元素数，并按列表位置打印各 type 的元素及原子序数。
- [model.xyz 官方格式](https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/input_files/model_xyz.rst)。
