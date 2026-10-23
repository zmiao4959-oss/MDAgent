# 升温—保温—降温退火

适用任务：在零外压下按给定温度程序退火。输入为三向周期、正交的 `model.xyz` 与可替换势文件 `nep.txt`，使用 [run.in](run.in)。

示例参数：时间步 1 fs；300→1200 K 升温 100000 步、1200 K 保温 100000 步、1200→300 K 降温 100000 步，每段 100 ps、合计 300 ps。目标温度在每个 `run` 内线性变化；瞬时温度会涨落。升/降温速率的绝对值为 `9e12 K/s`。耦合参数 100/1000、模量估计 100 GPa。

调整：根据材料、目标相态与势的适用温度范围替换目标温度和每段步数；本例高温不代表任何材料的推荐退火温度或熔点。如果需求为恒体积退火，先确认物理条件，再改用适当 NVT。

输出：三个阶段的 `thermo.out`、追加写入的 `dump.xyz`、最终降温末态 `restart.xyz`。每段都重新声明输出命令，因为这些命令不跨 `run` 传播；`restart.xyz` 会被后面的阶段覆盖。

局限：此流程不保证获得特定晶粒、缺陷密度或平衡相；应自行评估时间尺度、有限尺寸效应和升降温速率。已有速度的模型不会被 `velocity` 重置。

命令依赖：[potential](../../commands/potential.md)、[velocity](../../commands/velocity.md)、[time_step](../../commands/time_step.md)、[ensemble](../../commands/ensemble.md)、[ensemble_standard](../../commands/ensemble_standard.md)、[dump_thermo](../../commands/dump_thermo.md)、[dump_exyz](../../commands/dump_exyz.md)、[dump_restart](../../commands/dump_restart.md)、[run](../../commands/run.md)。
