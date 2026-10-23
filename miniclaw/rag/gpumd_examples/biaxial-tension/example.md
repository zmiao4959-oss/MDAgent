# NPT 后 xy 双轴拉伸：编写与短试跑示例

适用：三向周期、轴对齐正交块体，x/y同时加载、z保持目标零外压。模型、势、原子数和配比由任务提供。以下展示300 K、1 fs、NPT100000步后，加载30000步、工程应变30%；都是可替换参数。

## 编写任务：一次生成完整项目

```text
python "SKILL_DIR/examples/tension_workflow.py" create --model MODEL_PATH --potential POTENTIAL_PATH --output PROJECT_DIR --axes xy --strain 0.30 --load-steps 30000 --relax-steps 100000 --temperature 300 --time-step 1
```

`SKILL_DIR`由read_skill返回路径定位，其他大写路径按任务填写。生成器只准备文件，不启动模拟；JSON返回正式/试跑输入及项目内入口。项目包含实际模型和势、`relax/run.in`、加载模板、配置及自包含`workflow.py`和支持文件，复制整个项目到其他位置仍可用。

**这些文件和运行方法齐备，就已完成“写脚本”。** 正式restart尚不存在时保留模板，不能用初始盒长或试跑结果伪造正式参考，也无需为把加载模板填成数值而先跑正式NPT。CLI契约已给出，正常使用无须读取helper实现。

## 短试跑：只验证衔接

1. 用`run_gpumd`执行生成项目的`trial/relax/run.in`，核对完成日志。
2. 在项目目录执行`python workflow.py prepare-load --trial`。
3. 用`run_gpumd`执行上一步JSON返回的`run_in`，核对短加载完成日志。

只用trial的restart，按正式加载步数计算每步增量，再缩短实际加载run；工程应变率保持一致，试跑应变按实际步数比例缩减。试跑参考不进入正式加载目录。默认两段各200步且不超过正式步数，可在生成时通过CLI试跑步数选项调整。完成这条链即可交付编写任务。

## 正式执行：任务要求运行时使用

先执行`relax/run.in`；完成并确认该NPT结果可用后，在项目目录执行`python workflow.py prepare-load`，再执行返回的`run_in`。它从真实`relax/restart.xyz`生成数值加载输入，dx=0.30*Lx0/30000、dy=0.30*Ly0/30000，分别取真实参考盒长，单位Å/步。保留restart中的速度，不重新初始化。

衔接入口根据参考、势和参数指纹选择加载目录；参考改变时生成新目录，避免沿用旧的增量。相同输入可复用未经改动的已生成输入，不覆盖旧结果。正式执行要求与停止状态来自用户任务，不能把完整正式运行当作脚本编写或短试跑的补充步骤。

输出：项目内配置、输入与模板；运行后有thermo.out、dump.xyz、restart.xyz及张拉参考JSON。物理验证需另评估弛豫充分性、势的适用范围与稳定性；零外压是设定目标，不等于每一时刻应力为零。

## 按需参考

原始模板：[弛豫](relax.run.in)、[加载](load.run.in.template)。变更语法或遇到疑问时查：[deform](../../commands/deform.md)、[标准系综](../../commands/ensemble_standard.md)、[restart格式](../../files/output/restart_xyz.md)、[NEP元素对应](../../potentials/nep_element_mapping.md)。
