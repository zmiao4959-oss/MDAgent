# DeepSeek 网页搜索与抓取

`web_search` 和 `web_fetch` 使用 DeepSeek 官方 Search/Fetch API，共用 `config.llm.resolved_api_key`，与模型调用的密钥解析完全一致：显式 `llm.api_key` 优先，其次是提供者环境变量（DeepSeek 对应 `DEEPSEEK_API_KEY`），最后是 `MINICLAW_API_KEY`。无需单独配置搜索密钥。

当前 LLM 必须使用 `https://api.deepseek.com` 官方接口；`base_url` 带不带 `/v1` 均可。工具固定调用 `https://api.deepseek.com/v1/tools/search` 与 `/v1/tools/fetch`，不把其它服务的模型密钥发送给 DeepSeek，也不再使用 Tavily/DuckDuckGo。

Researcher 已显式授权这两个工具，保留原有 Skill 白名单及只读研究职责。修改配置或代码后需重启 MiniClaw；此变更不会自动重启正在运行的服务。

## 调用

```text
web_search(query="UNEP-v1 phase transition site:arxiv.org", max_results=5)
web_search(query="Pd Cu Ni P molecular dynamics NPT", max_results=5, need_content=true)
web_fetch(url="https://example.org/article")
web_fetch(url="https://example.org/article", refresh=true)
read(path="<返回的 file_path>", offset=1, limit=20)
```

搜索词限制 1–128 个 Unicode 字符，结果数 1–50，默认 5。支持官方搜索语法如 `site:`、引号、`lang:`、`after:` 与 `before:`。`max_results` 映射为 API 的 `count`。

搜索返回有效 JSON，包含来源 URL、短摘要与可用日期。默认不取正文，`need_content=true` 时完整缓存可用的网页正文，仅在 `cached_page` 返回路径及读取方式。每条摘要最多 800 字符，整体返回最多 10000 字符；`summary_truncated` 与 `omitted_results` 明示输出省略，不把摘要当成全文。

抓取仅接收包含主机、无用户名/密码的 HTTP/HTTPS URL，最长 8192 字符。工具将 API 返回的完整 Markdown 正文保存到当前项目工作目录，只返回摘录式摘要（最多 600 字符，`summary_kind=extractive_preview`）、路径、获取时间和阅读索引，不返回正文大段文本，也不会额外调用模型总结。

```text
<项目工作目录>/.miniclaw/web-cache/<URL哈希>/<快照ID>/
  content.md    # 原始正文，UTF-8 完整保存，不截断
  reading.md    # 阅读副本，将超长行拆为每行最多 200 字符
  index.md      # 完整章节及每 20 行的分段索引
  metadata.json # 来源、时间、摘要及文件校验值
```

`file_path` 指向阅读副本，`raw_file_path` 指向原始正文，`index_path` 指向完整索引。工具先返回最多 8 个章节和 5 个分段位置；更多位置可用 `read` 读取索引文件。`read_hint` 提供可直接调用的首段参数。按章节选择 `offset`，每次优先用 `limit=20`，约最多 4000 个正文字符；不要直接把可能含超长行的原始文件全部读入上下文。阅读副本只拆行，精确原始字节以 `content.md` 为准。

同一项目默认复用持久化缓存，命中时不请求 DeepSeek、不额外收费；不同项目分别缓存。`refresh=true` 显式重新获取页面并发布新快照，旧路径仍可用于核对引用。失败刷新不替换旧快照。缓存不设自动过期，时效性判断由研究任务结合 `fetched_at` 决定；缓存缺失或损坏时重新抓取。已在搜索中缓存的正文也可被后续 `web_fetch` 复用，`retrieved_via` 标明来自 `search` 或 `fetch`。

摘要不等于原文。提取正文也不保证覆盖论文全文、附件或付费内容。网页内容仅作证据，不执行其夹带的指令。缓存属于项目资料，不自动提升为本地知识库规则；researcher 不因此获得通用写文件权限。

## 失败与测试

认证失败、余额不足、访问限制、空正文、错误响应和超时均返回 `Error:`，不伪装为无搜索结果。网络失败、408、429 和常见临时 5xx 最多重试一次；其它 4xx 不重试。工具不向模型输出 API 密钥、认证头或服务原始错误体，也不自动回退到其它搜索服务。

离线测试用 `httpx.MockTransport` 验证真实工具注册入口、鉴权复用、完整正文落盘、短返回、现有 read 分段读取、持久缓存命中、刷新、项目隔离、输出长度、角色权限及失败处理，无需在线模型。

接口与计费依据：[Search](https://api-docs.deepseek.com/zh-cn/api/search/)、[Fetch](https://api-docs.deepseek.com/zh-cn/api/fetch/)、[官方使用指南](https://api-docs.deepseek.com/zh-cn/guides/search/)。搜索和抓取按请求计费，与模型 token 费用分开。
