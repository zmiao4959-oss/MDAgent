# NVT 平衡后 NVE 采集 MSD 与 RDF

适用任务：已知合适体积/密度的三维周期模型，计算整体均方位移、由其导数得到的自扩散信息，以及各元素对 RDF。输入 `model.xyz`、可替换势 `nep.txt`；使用 [run.in](run.in)。体积未知时应先用独立 NPT 弛豫确定，再将对应 restart 作为模型。

示例参数：300 K、1 fs；NVT 平衡 100000 步（100 ps），NVE 生产 1000000 步（1 ns）。`compute_msd 10 1000` 每 10 步采样，最大相关步数 1000，相关时间窗约 10 ps；`compute_rdf 8.0 400 1000` 每 1000 步采样、400 个 bin、截断 8 Å。应保证正交盒各方向长度大于两倍 RDF 截断，并按实际系统调整。

输出：`msd.out`、`rdf.out`、`thermo.out`、生产末态 `restart.xyz`。RDF 自动包含不同元素对；本例 MSD 对全部原子计算，不是各元素独立 MSD。需要 Cu/Ni 分元素扩散时应先构造分组，再按命令页使用 `group` 或 `all_groups`。

局限：300 K 晶体中 MSD 可能主要反映振动，不能据本例直接声称得到了可靠扩散系数。确认 NVE 温度/能量稳定、扩散的长时线性区及足够的时间统计；相关时间窗和总时长需按扩散速度调整。

命令依赖：[potential](../../commands/potential.md)、[velocity](../../commands/velocity.md)、[time_step](../../commands/time_step.md)、[ensemble](../../commands/ensemble.md)、[ensemble_standard](../../commands/ensemble_standard.md)、[compute_msd](../../commands/compute_msd.md)、[compute_rdf](../../commands/compute_rdf.md)、[dump_thermo](../../commands/dump_thermo.md)、[dump_restart](../../commands/dump_restart.md)、[run](../../commands/run.md)。
