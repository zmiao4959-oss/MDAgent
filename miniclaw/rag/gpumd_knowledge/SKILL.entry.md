---
name: gpumd-script
description: 按任务目录和完整示例编写、修改与验证 GPUMD 5.5 输入；支持可复现的 NPT 后拉伸两阶段衔接。
---

# GPUMD 脚本与示例

固定使用 GPUMD 5.5，不探测或比较版本。先读 [任务目录](index.md)，选最相似示例；条件一致的命令直接沿用，仅核对改动和疑问。依赖链接是按需索引，同一文档不重复加载。

“写脚本”完成于输入/模板、项目内衔接入口和运行说明齐备；短试跑只验证流程。用户要求实际运行或提交时，执行至其指定状态。弛豫后盒长等未来数据由衔接入口在运行时读取，模板未填入未来数值不代表交付不完整，不因此提前启动正式计算。

- [常见示例](examples/index.md)：输入、参数、输出、局限和运行方法；示范值不代替用户要求。
- [双轴拉伸](examples/biaxial-tension/example.md)与[单轴拉伸](examples/uniaxial-tension/example.md)：用 tension_workflow.py create 生成自包含项目，项目内 workflow.py prepare-load 根据真实 restart 生成数值加载输入。
- [完整命令](commands/index.md)；[势函数](potentials/index.md)；[NEP 元素对应](potentials/nep_element_mapping.md)。

用已有 read 阅读目录中的正文；搜索索引与这些文件共用内容。目录不能定位时再用 search_gpumd_docs 返回的路径按需读取。helper 按示例提供的 CLI 契约使用，通常不必读取实现。

短试跑按 trial 弛豫 → prepare-load --trial → trial 加载衔接，只取 trial restart，保持正式加载速率并缩短步数，不污染正式参考。两段流程已验证即可交付编写任务。未执行的正式阶段注明待运行，不用完整正式运行代替短试跑。

日志只读取已知路径的必要状态、末尾与关键输出。保留用户正式参数，核对元素、单位、系综和阶段。资料缺口明确说明，不围绕相同疑问反复改问法；结果说明须区分设定目标、实际观测和未验证的物理解释。
