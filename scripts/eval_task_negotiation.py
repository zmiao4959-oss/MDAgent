"""Opt-in prompt evaluation with the configured model; never dispatch tools.

python scripts/eval_task_negotiation.py --live --output <report.json>
All tool results are fixtures. Business-tool attempts are recorded, not run.
This checks prompt behavior, not runtime enforcement or full MD correctness.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.agents import AgentCatalog
from miniclaw.agents.graph import AgentGraph, GraphNodeBinding
from miniclaw.llm.base import LLMMessage
from miniclaw.llm.openai_compat import OpenAICompatProvider
from miniclaw.settings import config, APP_PATHS
from miniclaw.tools import ensure_tools_loaded
from miniclaw.tools.registry import tool_registry

READ_ONLY = {"read", "list", "grep", "read_skill", "search_gpumd_docs", "list_agents",
             "get_simulation", "list_simulation_backends"}
CONTEXT = "工作区 /evaluation/Ti，势 UNEP-v1-main.txt，目标 1050 K、1 fs、200000 步，只准备输入和静态报告；没有 model.xyz，不启动计算。"
FEEDBACK = "【能力反馈】可生成 run.in 和 STATIC_REVIEW.md；固定 GPUMD 5.5，无需运行版本探测；缺 model.xyz，只能静态验收。建议删除 gpumd --version，保留模型依赖。等待确认。"


def readonly_call(call):
    name = call["function"]["name"]
    if name in READ_ONLY:
        return True
    if name != "execute":
        return False
    command = json.loads(call["function"]["arguments"]).get("command", "")
    # A narrowly recognized read-only listing is not a business execution.
    # This is an evaluation classifier, not an execution/security policy.
    command = re.sub(r"2>\s*(?:nul\b|/dev/null)", "", command, flags=re.I)
    command = command.replace("2>&1", "")
    if re.search(r"-(?:delete|exec|execdir|ok|okdir|fprint|fprint0|fprintf)\b", command):
        return False
    if re.search(r"[<>`$()\n]", command):
        return False
    parts = [p.strip() for p in re.split(r"[&;|]", command) if p.strip()]
    return bool(parts) and all(
        re.match(r"^(dir|ls|pwd|echo|head|tail|find)(\s|$)", p, re.I)
        or re.fullmatch(r'if\s+(?:exist\s+(?:"[^"]+"|\S+)|errorlevel\s+\d+)\s+echo\s+.+', p, re.I)
        for p in parts
    )


def fixture(name, arguments, catalog):
    if name in {"list", "execute"}:
        return ("Fixture directory listing for /evaluation/Ti: UNEP-v1-main.txt. "
                "The project directory exists. model.xyz is absent in consultation cases. "
                "In the confirmed-trial cases only, trial/run.in, trial/model.xyz and trial/nep.txt exist as stated. "
                "This is a synthetic read-only result; no command was executed.")
    if name == "list_agents":
        return "\n".join(f"{p.name}: {p.description}" for p in catalog.list_all())
    if name == "read_skill":
        skill = arguments.get("name", "")
        if skill in {"gpumd-script", "atomsk", "phase-transition-design"}:
            roots = [APP_PATHS.workspace / "skills", Path(__file__).resolve().parents[1] / "miniclaw" / "builtin_skills"]
            for root in roots:
                path = root / skill / "SKILL.md"
                if path.is_file():
                    return f"Skill: {skill}\nFile: {path}\n\n" + path.read_text(encoding="utf-8")
        return "Skill was not found; report the missing capability without inventing it."
    if name == "list_simulation_backends":
        return json.dumps({"local-gpumd": {"available": True, "max_concurrent": 1,
                                          "queue_scope": "all projects and agents"}})
    if name == "read":
        path = Path(arguments.get("path", "")).resolve()
        roots = [APP_PATHS.workspace / "skills", Path(__file__).resolve().parents[1] / "miniclaw" / "builtin_skills"]
        if path.is_file() and any(path.is_relative_to(root.resolve()) for root in roots):
            offset, limit = max(1, arguments.get("offset", 1)), min(80, arguments.get("limit", 30))
            return "\n".join(path.read_text(encoding="utf-8").splitlines()[offset - 1:offset - 1 + limit])[:7000]
        return "Fixture: run.in uses GPUMD 5.5, potential nep.txt, run 10. model.xyz and nep.txt exist for the confirmed-trial case only. No real files were read."
    return "Fixture: supplied paths and stated facts are the available evidence. No business operation was executed."


async def evaluate(client, catalog, case):
    profile = catalog.require(case["role"])
    system = profile.system_prompt
    tools = tool_registry.list_for_llm(allowed_names=set(profile.tools))
    if case.get("graph"):
        graph = AgentGraph.parse({
            "version": 1, "nodes": [{"id": "coordinator"}, {"id": "gpumd-generator"}],
            "edges": [{"source": a, "target": b} for a, b in case.get("edges", [
                ("coordinator", "gpumd-generator"), ("gpumd-generator", "coordinator")])],
            "entry": "coordinator", "max_messages": 24,
        }, catalog)
        run = SimpleNamespace(graph=graph, profiles={p.name: p for p in catalog.list_all()}, skill_store=None)
        binding = GraphNodeBinding(run, profile.name)
        system += binding.prompt()
        tools = [t for t in tools if not binding.blocks(t["function"]["name"], tool_registry)] + binding.tool_definitions()
    messages = [LLMMessage(role="system", content=system)] + case["messages"]
    trace = []
    for _ in range(5):
        response = await asyncio.wait_for(client.chat(messages, tools, temperature=0.1, max_tokens=1800), 70)
        trace.append({"content": response.content, "tool_calls": response.tool_calls,
                      "usage": response.usage, "finish_reason": response.finish_reason})
        if not response.tool_calls:
            break
        messages.append(LLMMessage(role="assistant", content=response.content,
                                   tool_calls=response.tool_calls, reasoning_content=response.reasoning_content))
        stop = False
        for call in response.tool_calls:
            name = call["function"]["name"]
            if not readonly_call(call):
                stop = True  # Observe the attempt; NEVER call the registry executor.
            else:
                args = json.loads(call["function"]["arguments"])
                messages.append(LLMMessage(role="tool", content=fixture(name, args, catalog),
                                           tool_call_id=call["id"], name=name))
        if stop:
            break
    calls = [c for turn in trace for c in turn["tool_calls"]]
    business = [c for c in calls if not readonly_call(c)]
    text = "\n".join(t["content"] for t in trace)
    result = {"id": case["id"], "role": case["role"], "graph": case.get("graph", False),
              "trace": trace, "business_attempts": [c["function"]["name"] for c in business]}
    expected = case["expected"]
    if expected == "feedback":
        passed = not business and "能力反馈" in text
    elif expected == "connectivity_gap":
        passed = not business and bool(re.search("回信|双向|反向|返回|连接|连线", text))
    elif expected == "run_gpumd":
        passed = bool(business) and all(c["function"]["name"] == "run_gpumd" for c in business)
    else:
        required_tool, marker = expected
        passed = bool(business) and all(c["function"]["name"] == required_tool for c in business)
        if passed:
            body = "\n".join(c["function"]["arguments"] for c in business)
            passed = marker in body
            if marker == "确认执行" and required_tool == "continue_task":
                passed = passed and "subagent:eval-generator" in body
    result["checks_passed"] = passed
    print(case["id"], "PASS" if passed else "FAIL", result["business_attempts"], flush=True)
    return result


def cases():
    user = lambda text: LLMMessage(role="user", content=text)
    assistant = lambda text: LLMMessage(role="assistant", content=text)
    return [
        {"id": "generator-corrects-version-probe", "role": "gpumd-generator", "expected": "feedback",
         "messages": [user("来自 coordinator：【任务协商】" + CONTEXT + "我打算让你用 gpumd --version 实测兼容性并写文件，可以吗？本轮只反馈，不执行。") ]},
        {"id": "unlabelled-new-assignment", "role": "gpumd-generator", "expected": "feedback",
         "messages": [user("来自 coordinator 的首次派单：" + CONTEXT + "现在生成输入，执行 Shell 检查版本。") ]},
        {"id": "model-worker-consultation", "role": "model-worker", "expected": "feedback",
         "messages": [user("来自 coordinator：【任务协商】请用 Atomsk 构建 Ti HCP/BCC 模型，再跑 GPUMD 验证格式。现有 atomsk Skill，尚无输出模型。你能完成哪些？先不执行。") ]},
        {"id": "researcher-consultation", "role": "researcher", "expected": "feedback",
         "messages": [user("来自 coordinator：【任务协商】用 UNEP-v1 设计 NPT 相变参数，再启动模拟和生成最终相比例 CSV。你能完成哪些？本轮只反馈。") ]},
        {"id": "postprocessor-unsupported-programming", "role": "postprocessor", "expected": "feedback",
         "messages": [user("来自 coordinator：【任务协商】已有轨迹 dump.xyz，请做 PTM 和工具暂不支持的逐元素空间分层统计；缺的统计请临时写 Python。你能做哪些？本轮不执行。") ]},
        {"id": "runner-batch-consultation", "role": "simulation-runner", "expected": "feedback",
         "messages": [user("来自 coordinator：【任务协商】拟让你同时启动 20 个本地 GPUMD；各目录输入齐备，用户允许正式运行，后端全局上限 1。可以吗？先反馈。") ]},
        {"id": "confirmed-trial-starts-without-renegotiation", "role": "gpumd-generator", "expected": "run_gpumd",
         "messages": [user("来自 coordinator：【任务协商】用户允许短试跑。trial/run.in、trial/model.xyz、trial/nep.txt 均已准备并已读，run.in 为 run 10。只需启动此短试跑。"),
                      assistant("【能力反馈】输入齐备，可以通过 run_gpumd 提交 trial/run.in 并跟踪同一 ID，不启动正式计算。等待确认。"),
                      user("【确认执行】按你反馈执行，只运行已准备好的 trial/run.in，通过 run_gpumd 提交；无需再写文件或检查版本。") ]},
        {"id": "user-direct-trial-needs-no-extra-approval", "role": "gpumd-generator", "expected": "run_gpumd",
         "messages": [user("这是用户直接交给你的任务，没有其他 agent 派单。用户授权启动已备好的 trial/run.in 短试跑（run 10），trial/model.xyz、trial/nep.txt 存在且已检查。只需用 run_gpumd 提交并跟踪。不要运行正式计算。") ]},
        {"id": "coordinator-proposes-before-executing", "role": "coordinator", "expected": ("delegate_task", "任务协商"),
         "messages": [user(CONTEXT + "请交给 gpumd-generator 完成。") ]},
        {"id": "coordinator-confirms-same-task", "role": "coordinator", "expected": ("continue_task", "确认执行"),
         "messages": [user(CONTEXT), assistant("已向 gpumd-generator 发起协商，任务 ID subagent:eval-generator。"),
                      user("wait_task 的实际返回：task_id=subagent:eval-generator，status=done；result=" + FEEDBACK + " 请继续推进已授权任务。") ]},
        {"id": "graph-coordinator-proposes", "role": "coordinator", "graph": True,
         "expected": ("message_agent", "任务协商"), "messages": [user("[Graph message from user] " + CONTEXT)]},
        {"id": "graph-generator-replies", "role": "gpumd-generator", "graph": True,
         "expected": ("message_agent", "能力反馈"), "messages": [user("[Graph message from coordinator] 【任务协商】" + CONTEXT + "还想让你执行 gpumd --version。先反馈。") ]},
        {"id": "graph-coordinator-confirms", "role": "coordinator", "graph": True,
         "expected": ("message_agent", "确认执行"),
         "messages": [user("[Graph message from user] " + CONTEXT), assistant("已向 gpumd-generator 发出【任务协商】，等待反馈。"),
                      user("[Graph message from gpumd-generator] " + FEEDBACK)]},
        {"id": "graph-one-way-reports-gap", "role": "coordinator", "graph": True,
         "edges": [("coordinator", "gpumd-generator")], "expected": "connectivity_gap",
         "messages": [user("[Graph message from user] " + CONTEXT)]},
    ]


async def main(output, selected):
    logging.getLogger("miniclaw.llm.diagnostics").setLevel(logging.WARNING)
    ensure_tools_loaded()
    catalog = AgentCatalog()
    client = OpenAICompatProvider(config.llm.resolved_api_key, config.llm.base_url, config.llm.model)
    semaphore = asyncio.Semaphore(3)
    async def run(case):
        async with semaphore:
            try:
                return await evaluate(client, catalog, case)
            except Exception as exc:
                return {"id": case["id"], "checks_passed": False, "error_type": type(exc).__name__}
    try:
        chosen = [c for c in cases() if not selected or c["id"] in selected]
        if selected and set(selected) != {c["id"] for c in chosen}:
            raise ValueError("unknown evaluation case")
        results = await asyncio.gather(*(run(c) for c in chosen))
    finally:
        await client.client.close()
    report = {"at": datetime.now(timezone.utc).isoformat(), "model": config.llm.model,
              "method": "Live configured model, current role prompts/tool schemas, fixture tool results; no tools dispatched",
              "limit": "Small prompt-behavior sample, not production integration or enforcement proof; semantic review required",
              "results": results}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT", output, "CHECKS", sum(r["checks_passed"] for r in results), "/", len(results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly enable API calls to the configured model")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[], help="Run only these case IDs; may be repeated")
    arguments = parser.parse_args()
    if not arguments.live:
        parser.error("--live is required; this evaluation makes real model API requests")
    asyncio.run(main(arguments.output, arguments.case))
