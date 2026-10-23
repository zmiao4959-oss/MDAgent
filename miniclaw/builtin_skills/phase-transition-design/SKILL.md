---
name: phase-transition-design
description: 为 UNEP-v1 和 Pd–Cu–Ni–P 的 NPT、升降温和周期 bulk 拉伸研究设计关键参数，给出有依据的候选方案、闭合计算和交接条件。用于模拟前的研究设计，不承担模型生成、MD 提交或轨迹后处理。
---

# 相变研究设计

给出研究参数、单位、直接依据、来源状态、有限验证预算与上游依赖。保持用户给定的初态、成分、制备历史和物理约束；文献值、候选值和本次计算分开。研究方案中的具体参数由本 Skill 的知识决定。

## 定位与按需阅读

以 `read_skill` 返回的 `File:` 路径的父目录作为本 Skill 根目录。下面路径都相对此目录；它不一定是当前项目或工作目录。使用实际绝对路径读文件和启动脚本。

先读 [研究入口](references/agent-entry.md)，再按入口选择参数表、NPT/拉伸分支及 UNEP/Pd 决策包。遵守首轮阅读预算；缺少必需字段或存在冲突时，才沿该问题补读证据。大量文献、资产和历史评测不要默认通读。

每个参数叶字段单独标记 `USER / FILE / LIT / CAND / CALC / RUNTIME / BLOCK`。待执行检查和工具可用性另列；不可用混合字典的单一标签代替逐字段来源。具体口径见 [交接契约](references/decisions/handoff-contract.json)。

## Skill 内参数计算入口

已有的 `execute` 用于调用 **本 Skill 已提供的** `scripts/calculate_plan.py`；不需要新注册工具、临时 JSON 文件或现场编程。这个脚本只做标量计算，向标准输出返回 JSON，不写模型、输入、报告或结果资产，不启动 MD。

以已配置 Python 解释器替换下列 `python`，将 `<skill_dir>` 替换为上述实际目录。`execute.working_dir` 保持为当前任务项目；无需切换到 Skill 目录。

```text
python "<skill_dir>/scripts/calculate_plan.py" state --duration-ps 100 --dt-fs 0.5 --tau-t-fs 100 --tau-p-fs 1000
python "<skill_dir>/scripts/calculate_plan.py" tension --strain 0.1 --rate 1e8 --dt-fs 1 --axes z --length z=100 --dump-strain 0.0001
python "<skill_dir>/scripts/calculate_plan.py" tension --strain 0.3 --steps 100000 --dt-fs 1 --axes xy --length x=35 --length y=36
python "<skill_dir>/scripts/calculate_plan.py" ramp --start-k 2500 --end-k 300 --rate 2e12 --dt-fs 1 --dump-delta-k 1 --dump-max-ps 1
```

- 输入参考长度必须是用户已确认的正式参考，或正式预平衡 restart 的实际值；不是示例中的 100/35/36 Å。未知时省略 `--length`，接受 `RUNTIME` 和增量公式，按上游依赖交接。
- 固定状态用 `state` 把阶段物理时间和步长换算为步数，可同时核对给定步数；`--tau-t-fs`/`--tau-p-fs` 保留物理耦合时间，输出各自除以 dt 的输入比值。算法、温压和材料适用性仍由知识决定。
- 同时有速率、时长或步数硬条件时，一并传入 `--rate`、`--duration-ps`、`--steps`，让脚本检查一致性。不要先删掉相互矛盾的条件。
- 工程应变是默认计算契约。用户明确真应变时传 `--strain-definition true`，保留其拒绝原因和指数路径需求；不静默改工程应变。
- 检查退出状态、JSON 的 `status`、参考状态与输出的 `resolution_satisfied`。失败可在原用户约束内纠正输入；缺功能或环境时明确交接，不手算填补失败、不现场写替代脚本。
- 更细的参数契约与单位见 [计算说明](references/decisions/calculation.md)；本目录其它维护脚本用于离线知识审计，不是 researcher 的业务入口。

## 交接

按研究入口输出紧凑参数包：目标/假设、逐字段参数与依据、阶段时间/步数、各轴约束、正式参考绑定、输出与验证预算、完成状态和能力缺口。角色已有文件读取能取得的信息不要重新问用户。候选方案完成不等于运行检查或物理验证完成。

由协调者将参数包交给已有 model-worker、gpumd-generator、simulation-runner 和 postprocessor。此 Skill 的计算器支持某个轴的标量运算，不代表现有 GPUMD 拉伸 helper 已实现该轴或恒真应变路径；执行能力按知识中的实际边界判断。
