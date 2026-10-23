# C-02 · GPUMD 5.5 拉伸与现有入口

状态：软件依据及本地入口核对。日期：2026-10-20。来源 S15：[v5.5 deform 文档](https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/input_parameters/deform.rst)。实际阅读本地同版本正文。

## 可直接采用的格式事实

六参数形式为 `deform dLx dLy dLz flag_x flag_y flag_z`；dLi 单位 Å/步。启用多方向时可以分别给增量；四参数形式把同一 Å/步值用于所有启用方向，不保证不同初长具有相同工程应变率。

5.5 文档要求 deform 与 NPT 联用并独立控制三个方向压力，未加载方向按设置控压。要固定未加载方向盒长，可按文档用大于 2000 GPa 的控制模量关闭相应压控。加载方向由 deform 驱动，不能将输入的压力数值解读成该轴始终定压。

官方例子以 L0=100 Å、dL=10^-5 Å/步、dt=1 fs 运行 10^6 步，得到工程应变 10%、工程应变率 10^8 s^-1。该例子说明单位和运动学，不是某材料的推荐加载速率。

## 本地 helper 的真实支持范围

现有 [tension_workflow.py](../../../../rag/gpumd_examples/tension_workflow.py) 的 main 和参数验证仅允许 `x`、`xy`。本地对应[单轴示例](C:/Users/35059/.miniclaw/workspace/skills/gpumd-script/examples/uniaxial-tension/example.md)与[双轴示例](C:/Users/35059/.miniclaw/workspace/skills/gpumd-script/examples/biaxial-tension/example.md)说明：先生成项目，在正式 relax/restart.xyz 存在后 prepare-load，分别读取实际盒长，保留速度。

不能把“引擎支持 z”写成“这个 helper 接受 --axes z”。z、其他轴组合或真应变率需要明确的对应输入/计算入口，现有 x/xy helper 不直接覆盖。知识库没有修改该 helper。

正式参考未产生时应交付 RUNTIME 绑定方案；不得改用 trial 或初始盒长。试跑只在独立目录验证链路，并保留正式每步增量对应的速率。
