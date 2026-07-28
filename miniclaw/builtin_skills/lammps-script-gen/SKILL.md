---
name: lammps-script-gen
description: >-
  将中文或英文分子动力学研究目标编译为经过物理规则检查、带证据包的
  LAMMPS 输入脚本；适用于结构弛豫、NVT、NPT、单轴拉伸和热膨胀。
---

# LAMMPS 实验编译器

使用 `generate_lammps_script` Tool 调用 MDSynth。它生成脚本与证据包，但不执行最终模拟。

## 工作流

1. 从用户目标中确认材料、任务类型、温度；拉伸任务还要确认方向。缺少阻断参数时先询问，不要自行猜测。
2. 为每次生成选择新的项目内目录，例如 `generated/cu-npt-300k`。不要覆盖已有结果，除非用户明确要求。
3. 默认保持 `preflight=false`。用户要求验证脚本、且本机 LAMMPS 与势函数可用时，才启用有界预检。
4. 默认保持 `allow_direct_generation=false`，使用确定性模板。多阶段或模板外任务只有在用户明确接受较弱验证路径后才能开启直接生成。
   模板外生成会先检索已收录脚本；每次 LAMMPS 预检失败后会检索相关官方命令文档，再让 LLM 修复。
5. 启用预检、直接生成或覆盖前，让用户输入精确确认 `ALLOW MDSYNTH ADVANCED`，并原样传给 Tool。
6. 生成后检查 `validation_report.json`、`preflight_report.json`、`assumptions.md` 和 `provenance.lock`，向用户说明假设、风险与是否经过真实预检。
7. 不要自动运行 `in.main.lammps`。最终模拟仍需用户确认执行环境、势函数和资源预算。

## 确定性模板范围

- `structure_relaxation`
- `equilibration_nvt`
- `equilibration_npt`
- `uniaxial_tension`
- `thermal_expansion`

内置材料为 Cu、Al、Fe、Au、W、Ni。其他材料或组合流程属于直接生成路径，必须显式提示其验证等级更低。

## 安全边界

- Tool 只向当前项目工作区写入输出。
- MDSynth 在独立子进程运行，LAMMPS 预检产生的临时文件位于一次性目录。
- 直接生成会拒绝 `shell`、`python`、`jump`、`include`，以及逃出预检目录的输出路径。
- MDSynth 源码目录由管理员通过 `MDSYNTH_PROJECT_DIR` 配置；未设置时使用本机已接入的默认仓库。
