# HNEMD：沿 x 驱动的热导率示例

适用任务：三维周期块体，在已确定密度和体积下用均匀非平衡分子动力学计算原子体系热输运。输入 `model.xyz` 与兼容该计算的可替换势 `nep.txt`；使用 [run.in](run.in)。模型密度未知时先做独立 NPT 平衡。

示例参数：300 K、1 fs；NVT 平衡 100000 步（100 ps），HNEMD 生产 1000000 步（1 ns），`Fe_x=1e-5 Å^-1`，其余分量为零，每 1000 步输出。使用 `nvt_nhc` 去除驱动力产生的热；GPUMD 官方说明不应为 HNEMD 使用 Langevin 热浴。

输出：`kappa.out`、`thermo.out`、生产末态 `restart.xyz`。x 驱动可得到热导率张量 xx、yx、zx 分量；要测另一驱动方向，应在独立计算中改变 Fe，而不是一次令所有方向非零。

局限：示例 Fe 与时长没有经过该材料收敛验证；需用不同较小驱动力检查线性响应，评估统计误差、有限尺寸效应并做独立重复。对 CuNi 等金属，普通经典原子势得到的是原子/晶格热输运，不包含电子热导率，不能直接视为金属总热导率。

命令依赖：[potential](../../commands/potential.md)、[velocity](../../commands/velocity.md)、[time_step](../../commands/time_step.md)、[ensemble](../../commands/ensemble.md)、[ensemble_standard](../../commands/ensemble_standard.md)、[compute_hnemd](../../commands/compute_hnemd.md)、[dump_thermo](../../commands/dump_thermo.md)、[dump_restart](../../commands/dump_restart.md)、[run](../../commands/run.md)。
