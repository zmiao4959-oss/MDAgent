# MDSynth / LAMMPS 脚本生成

MiniClaw 通过 `generate_lammps_script` Tool 调用独立的 MDSynth 项目，并通过内置 `lammps-script-gen` Skill 指导 Agent 收集参数和解释证据。MDSynth 仍是编译器的唯一源码仓库，MiniClaw 不复制其代码。

## 为什么同时使用 Tool 和 Skill

- Tool 负责确定性的意图编译、IR、物理规则检查、脚本生成、可选预检和证据包写入。
- Skill 负责何时调用、需要向用户确认哪些科学参数、如何解释假设与风险。
- MDSynth 和 LAMMPS 原生库在独立子进程运行，不会把崩溃或全局模块状态带入 WebChat 主进程。

## 配置

默认源码目录：

```text
C:\Users\35059\Desktop\MDAgent\组会\周五\MD编译器开发\lammps-script-gen
```

移动仓库后设置：

```text
MDSYNTH_PROJECT_DIR=<lammps-script-gen 仓库路径>
```

可选运行环境：

```text
MDSYNTH_LAMMPS_EXECUTABLE=<lmp 或 lmp.exe 路径>
LAMMPS_POTENTIALS=<势函数目录>
MDSYNTH_RAG_API_URL=http://localhost:3000
MDSYNTH_RAG_REQUIRED=true
```

Tool 使用 MiniClaw 当前 Python 启动桥接子进程，并把 MDSynth 源码目录加入该进程的 `PYTHONPATH`。依赖由根目录 `requirements.txt` 提供。只有 `use_current_llm=true` 时，才把当前 LLM 密钥放入最小化的子进程环境；结果、日志和工具返回值都不包含密钥。

## Tool 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `request` | 必填 | 完整的中文或英文 MD 研究目标 |
| `output_directory` | `generated/lammps-script` | 当前项目工作区内的新目录 |
| `use_current_llm` | `true` | 用当前 MiniClaw LLM 提取结构化意图；测试可使用 Mock |
| `preflight` | `false` | 执行有界 LAMMPS 预检，不执行最终模拟 |
| `allow_direct_generation` | `false` | 允许模板外的 LLM 直接脚本路径 |
| `overwrite` | `false` | 写入已有非空目录 |
| `confirmation` | 空 | 高级模式精确确认 |

启用 `preflight`、`allow_direct_generation` 或 `overwrite` 时，必须精确提供：

```text
ALLOW MDSYNTH ADVANCED
```

## 输出

生成目录通常包含：

```text
in.main.lammps
intent_spec.yaml
md_ir.yaml
validation_report.json
preflight_report.json
provenance.lock
assumptions.md
README.md
```

## 模板外任务的 RAG 增强

当意图不匹配确定性模板、并且用户批准
`allow_direct_generation=true` 时，直接生成路径必须先调用 LAMMPS RAG：

1. 以完整研究目标检索 `kind=script`，把相似脚本及中文解释加入首次生成提示。
2. 使用 LLM 生成完整脚本。
3. 开启 `preflight` 时，用 LAMMPS `run 0` 做有界检查。
4. 检查失败后，以 LAMMPS 错误和失败脚本中的命令为查询检索 `kind=docs`。
5. 把官方命令语法、限制和来源链接加入修复提示，最多执行五轮检查。

MiniClaw 默认连接 `http://localhost:3000`，也可通过
`MDSYNTH_RAG_API_URL` 或 `LAMMPS_RAG_API_URL` 改写。默认
`MDSYNTH_RAG_REQUIRED=true`：RAG 不可用时模板外生成会明确失败，不会静默进行
无检索依据的生成。模板内的确定性编译不依赖 RAG 服务。

这些文件位于项目工作区，因此会自动出现在产物浏览器中，也能进入科研胶囊。

## 安全边界

- 输出必须位于当前项目工作区，不能写入 `.git` 或 `.miniclaw`。
- 非空目录默认拒绝写入，不删除已有文件。
- 最终 LAMMPS 模拟永远不会由这个 Tool 自动启动。
- 预检的相对输出落在一次性目录中。
- 直接生成默认关闭；开启后仍拒绝 `shell`、`python`、`jump`、`include` 和逃逸预检目录的输出路径。
- 外部仓库的 commit 和 dirty 状态会写入 Tool 返回结果。当前接入版本为 commit `8caeee966eb5cea5ce62433205bb43001747cf40`，且接入时工作树包含用户未提交修改；MiniClaw 不会改写它。

## 验收

```powershell
python -m pytest tests/test_lammps_script_tool.py -q
python scripts/acceptance_mdsynth_integration.py
```

外部项目自己的基线：`72 passed`。
