# 参数算术契约

本 Skill 提供只读计算器 [calculate_plan.py](../../scripts/calculate_plan.py)，接受直接参数或 JSON 请求，输出推导值和一致性检查；不启动模拟，不写模型或输入。它不决定科学参数，不填未测的 L0。

以 `read_skill` 返回的 `File:` 路径的父目录确定实际 Skill 根目录，将下列 `<skill_dir>` 替换为该绝对路径，并使用已配置的 Python 解释器。通过研究角色已授权的 execute 入口调用，`execute.working_dir` 保持为活动任务项目，脚本使用绝对路径；无需切换到 Skill 目录，也不依赖原 knowledge 目录。输出为标准 JSON：成功退出码 0；非法、缺参、非有限数值或冲突返回结构化 error，退出码 2。调用不要求阅读实现源码。

## 固定状态的步数与耦合比值

`python "<skill_dir>/scripts/calculate_plan.py" state --duration-ps 100 --dt-fs 0.5 --tau-t-fs 100 --tau-p-fs 1000`

输出 `mode=fixed_state`、200000 步、物理 tau 以及 `tau_t_over_dt=200`、`tau_p_over_dt=2000`。可用 `--steps` 指定步数，或与 `--duration-ps` 同时传入检查一致性。只对已给定物理时间进行换算，不推荐时长或耦合参数。

## 工程拉伸

请求例：`{"mode":"engineering_tension","strain":0.1,"rate_s_inv":1e8,"dt_fs":1,"axes":"xy","reference_lengths_A":{"x":35,"y":36},"dump_strain":1e-4}`。

直接调用：`python "<skill_dir>/scripts/calculate_plan.py" tension --strain 0.1 --rate 1e8 --dt-fs 1 --axes xy --length x=35 --length y=36 --dump-strain 1e-4`。`--length` 可重复；可传 `--steps`、`--duration-ps` 与 `--dump-max-ps`。计算器接受各轴的独立初长，现有模型/加载 helper 的轴支持仍按[拉伸决策](tension.md)核实。

给 strain 和 dt，并给 rate_s_inv、steps 或 duration_ps 中至少一项；多项必须一致。输出 duration、steps、实际速率、每轴 dL 和输出间隔。缺参考返回 RUNTIME，不伪造长度；已给参数矛盾返回 error。

`strain_definition="true"` 不会静默转成恒工程率；返回不支持当前路径。公式需求见拉伸决策页。

## 冷却/升温

请求例：`{"mode":"temperature_ramp","start_K":1500,"end_K":300,"rate_K_s":2e10,"dt_fs":1}`。

直接调用：`python "<skill_dir>/scripts/calculate_plan.py" ramp --start-k 1500 --end-k 300 --rate 2e10 --dt-fs 1 --dump-delta-k 1 --dump-max-ps 1`。

输出时间、步数与实际速率。可额外给 `dump_delta_K` 与 `dump_max_ps`，计算同时满足两者的最大整数步间隔。固定步长取整会报告实际值与取整量。

旧 JSON 文件方式继续支持：`python "<skill_dir>/scripts/calculate_plan.py" --input REQUEST.json`，请求文件相对路径按活动任务项目解析。直接参数方式无需创建临时请求文件。

算术正确不证明起止温度、速率、步长或初态适用。它只保证给定方案内部的数值关系准确。
