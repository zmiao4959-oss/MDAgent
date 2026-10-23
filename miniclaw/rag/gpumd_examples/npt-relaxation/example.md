# NPT 弛豫：300 K、零外压

适用任务：给三维周期正交模型做温压平衡，或为拉伸准备零应变态。使用本例 [run.in](run.in)。

输入：同目录的 `model.xyz` 与 `nep.txt`。`nep.txt` 是可替换的示例势文件名，势必须覆盖模型元素；模型应为 `pbc="T T T"`，正交且原子间距合理。脚本不负责建模、配比或原子数。

示例参数：300 K、各向压力 0 GPa、时间步 1 fs、100000 步（100 ps）。`T_coup=100`、`p_coup=1000` 是以时间步为单位的耦合参数；三个 100 GPa 为压力控制所需的粗略弹性模量估计，不是材料测得值。允许三个盒长独立变化，因此弛豫后不保证严格立方。

使用：将 `run.in`、模型和势放入新的计算目录，使用 GPUMD 执行该目录。若模型已有 `vel`，GPUMD 会保留它们，`velocity 300` 不会覆盖；若需要全新初始化，应在准备模型时去掉速度列。

输出：`thermo.out`、`dump.xyz`、最终 `restart.xyz`。续跑时将 `restart.xyz` 复制为另一目录的 `model.xyz`，保留它的盒长、坐标和速度。

局限：100 ps 仅为模板时长，不能据此宣称温度、压力、体积已经收敛；有表面、真空层或非正交模型时不能直接套用。

命令依赖：[potential](../../commands/potential.md)、[velocity](../../commands/velocity.md)、[time_step](../../commands/time_step.md)、[ensemble](../../commands/ensemble.md)、[ensemble_standard](../../commands/ensemble_standard.md)、[dump_thermo](../../commands/dump_thermo.md)、[dump_exyz](../../commands/dump_exyz.md)、[dump_restart](../../commands/dump_restart.md)、[run](../../commands/run.md)。
