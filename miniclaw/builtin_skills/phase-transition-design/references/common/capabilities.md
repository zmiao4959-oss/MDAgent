# 当前执行能力与研究需求

[总目录](../index.md) · 核对日期：2026-10-20

本页依据仓库当前的 [postprocessor 声明](../../../../agents/profiles/postprocessor/agent.yaml)及 [SYSTEM.md](../../../../agents/profiles/postprocessor/SYSTEM.md)整理。它记录公开的职责与工具口径；没有在本次运行这些分析，不能当作运行验收记录。

| 需求 | 当前角色/工具 | 首版状态 |
| --- | --- | --- |
| 原子模型与替换 | model-worker / Atomsk 与已有 Skill helper | 已有角色职责 |
| GPUMD 5.5 输入及短试跑 | gpumd-generator | 已有角色职责 |
| 本地/已配置远端正式运行和等待 | simulation-runner | 已有角色职责；资源是否可用需实际查询 |
| FCC/HCP/BCC/ICO/Other 原子比例 | analyze_phase_fraction | 已有专用工具 |
| 势能、盒子体积、逐帧总 RDF、固定参考 MSD | 对应分析工具 | 已有专用工具 |
| 首末值、最小值、最大值、均值；已有 CSV 摘要 | summary / summarize_postprocessing | 当前声明已提供 |
| 指定径向区间的 RDF 选峰 | RDF 分析或已有 CSV 摘要 | 当前声明已提供；需给出区间与规则 |
| 图像与 Markdown 报告 | plot_postprocessing / write | 已有工具；生成成功不代表人工看过图像 |
| x/xy NPT 后恒工程率拉伸项目 | 现有 tension_workflow helper | 支持正式 restart 后绑定参考；仅 x/xy、三向周期正交盒 |
| z 拉伸输入 | GPUMD deform 支持；现有 helper 不支持 axes=z | generator 可依据文档设计输入，运行时参考代入须明确入口 |
| 恒真应变率、三斜剪切或表面拉伸项目 | 未确认完整 helper | 不能套用当前恒增量 x/xy helper |
| 应力–应变/温度–轨迹联合映射、局部塑性或剪切带 | 未列出专用分析工具 | 原始数据可保存；完整分析尚缺能力 |
| 按物理相联合统计元素成分 | 未列出专用工具 | 尚未提供 |
| 化学有序参数、元素对分 RDF、Voronoi 指标 | 未列出对应专用工具 | 尚未提供 |
| Tg 自动拟合、相界拟合、扩散系数拟合 | 未列出对应专用工具 | 尚未提供 |
| 自相关、有效样本数、置信区间 | 未列出对应专用工具 | 尚未提供 |
| MC/MD 交换、共存制备、自由能工作流 | 没有本次可确认的完整角色交付契约 | 尚未建立已验证流程 |
| UNEP 模型集合分歧评估 | 有模型文件；没有对应已确认工具 | 尚未建立能力 |

## 使用摘要的口径

当前均值对应所选样本的等权均值。RDF 选峰需要明确半径区间；局部峰、区间最大 bin 和所有 bin 的统计是不同量。PTM 原生 CSV 没有时间列时，应只报告实际保存的帧号，或由明确的阶段映射恢复时间。

## 遇到缺口时

研究设计可以保留未来方法的依据，但执行交接必须标明尚不能完成的部分。不要指示现有角色通过临时编程、LLM 手算、改写 CSV 或更换执行角色来冒充缺失功能。

本页随项目功能变化更新；已存在的工具还要在实际环境中确认依赖、输入与运行结果。知识内容和运行能力分别核实。

Skill 附[只读参数计算器](../../scripts/calculate_plan.py)，用于固定状态、热路径和工程拉伸的算术与冲突检查。researcher 可发现本 Skill，并按[计算契约](../decisions/calculation.md)通过已授权的 execute 入口调用 scripts；已运行进程是否重新加载尚未核实。计算器不选择科学参数，不表示分析或模拟已经完成。
