# 包格式与发布

在 `list_skill_requests` 返回的 `draft_dir` 下直接写入：

```text
package/
  SKILL.md
  skill.yaml
  scripts/       # 有确定性实现时才需要
  references/    # 按需资料
  tests/         # 含 Python 实现时必须提供实际 unittest
```

`SKILL.md` 使用标准 YAML frontmatter，包含 `name` 与 `description`。名称使用不超过 64 字符的小写英文、数字和连字符，不能覆盖已有全局 Skill。正文注明输入、输出、单位、已有工具如何调用脚本、适用范围和失败条件；使用该包的目录作为脚本路径基准。

`skill.yaml` 示例：

```yaml
schema_version: 1
name: summarize-phase-labels
version: "1.0.0"
required_tools: [read_skill, read, execute]
```

只声明实际需要的工具，必须是请求者已有的工具。若没有脚本且只提供说明，可使用更小的工具清单。含 Python 实现时必须声明 `execute`；没有此权限就不能发布执行型包。

第一版支持 Python 和文本资源（`.md/.py/.txt/.json/.yaml/.yml/.csv`），最多 100 文件、合计 4 MiB。不支持符号链接、junction、依赖安装和其他语言的可执行文件。

## 测试

将测试放在 `tests/test_*.py`，使用 Python `unittest`。实现可以通过 `from scripts.example import ...` 导入，脚本使用标准库或当前环境已具备的依赖。禁止在测试中安装包、调用模型、访问线上服务、修改项目资产、运行正式模拟或执行原始任务。测试输出使用 `tempfile.TemporaryDirectory`，不要修改包内容。

`publish_skill` 会冻结草稿副本，检查元数据、声明的权限和 Python 语法，再在临时工作目录中运行测试。测试进程不接收模型凭证，默认最多运行 30 秒。至少一个测试必须实际执行成功，不能全部跳过。失败时完整保留草稿以便修复；通过后生成带内容哈希的版本目录，仅挂载给请求者。

临时工作目录和最小环境变量不是操作系统沙箱。测试必须遵守上面的开发范围；框架不会把“运行成功”当作代码安全或物理正确性的证明。

无 Python 实现且未提供测试时只进行结构校验，发布记录为 `structure_only`；不可宣称做过行为验证。

## 使用与撤销

请求者下一次被激活时会看到该 Skill，通过已有的 `read_skill` 加载；需要执行脚本时仍使用已有工具。说明中使用 `python -B`，避免在已发布包内生成字节码缓存，所有任务输出写到项目工作目录。临时挂载不写入 `agent.yaml`，不影响其他项目和角色。请求者可调用 `revoke_skill(name=...)` 撤下；已发布版本保留用于追溯。
