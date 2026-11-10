"""Exercise graph routing through real Agent loops with deterministic LLMs."""
import asyncio
import inspect
import json
from pathlib import Path

import httpx
import pytest

from miniclaw.agent import Agent, AgentContext
from miniclaw.agents import AgentCatalog
from miniclaw.agents.graph import AgentGraph, AgentGraphRuntime
from miniclaw.channels.web.web_app import create_web_app
from miniclaw.channels.webchat_adapter import WebChatAdapter
from miniclaw.llm.base import LLMMessage, LLMResponse, LLMStreamChunk
from miniclaw.memory.session import SessionManager
from miniclaw.settings import config
from miniclaw.tools.registry import tool_registry


def call(name, **arguments):
    return LLMResponse(tool_calls=[{"id": "test-call", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}], finish_reason="tool_calls")


class ScriptedLLM:
    def __init__(self, script=None):
        self.script = script or (lambda *_: LLMResponse(content="finished"))
        self.calls = []
        self.counts = {}

    async def chat(self, messages, tools=None, **kwargs):
        system = messages[0].content
        node = system.split("Your node: ", 1)[1].split(".", 1)[0] if "Your node: " in system else "default"
        index = self.counts.get(node, 0)
        self.counts[node] = index + 1
        self.calls.append((node, list(messages), tools or []))
        response = self.script(node, index, messages, tools or [])
        return await response if inspect.isawaitable(response) else response

    async def chat_stream(self, *args, **kwargs):
        response = await self.chat(*args, **kwargs)
        yield LLMStreamChunk(delta_content=response.content, delta_tool_calls=response.tool_calls, finish_reason=response.finish_reason)


def topology(*nodes, edges=(), entry=None, limit=24, concurrency=None):
    graph = {"version": 1, "nodes": [{"id": name} for name in nodes], "edges": [{"source": a, "target": b} for a, b in edges], "entry": entry or nodes[0], "max_messages": limit}
    if concurrency is not None:
        graph["max_concurrency"] = concurrency
    return graph


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config.evolution, "enabled", False)
    monkeypatch.setattr(Agent, "_memory_prefix", lambda *_: "")
    monkeypatch.setattr(Agent, "_experience_prefix", lambda *_: "")
    monkeypatch.setattr("miniclaw.agent.WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(SessionManager, "_get_store", lambda _: None)
    profiles = tmp_path / "profiles"
    for name, tools in [("coordinator", ["delegate_task", "list_agents", "send_message", "read"]), ("researcher", ["read"]), ("worker", ["read"])]:
        folder = profiles / name
        folder.mkdir(parents=True)
        (folder / "agent.yaml").write_text(json.dumps({"name": name, "description": name, "tools": tools, "skills": [], "runtime": {"max_tool_rounds": 5}}), encoding="utf-8")
    catalog = AgentCatalog([profiles])

    def make(script=None):
        llm = ScriptedLLM(script)
        sessions = SessionManager(tmp_path / "sessions")
        runtime = AgentGraphRuntime(Agent(llm, sessions, profile=catalog.require("coordinator")), catalog)
        return runtime, llm

    return make, catalog, tmp_path


def context(graph, chat="webchat:graph-test"):
    return AgentContext(chat_id=chat, channel="webchat", account_id="test", user_message="Design an NPT run", metadata={"agent_graph": graph})


@pytest.mark.parametrize("bad", [
    {},
    topology("missing"),
    topology("researcher", "researcher"),
    topology("researcher", "worker"),  # unreachable
    topology("researcher", edges=[("researcher", "researcher")]),
    topology("researcher", edges=[("researcher", "missing")]),
    topology("researcher", "worker", edges=[("researcher", "worker"), ("researcher", "worker")]),
    topology("researcher", entry="worker"),
    topology("researcher", limit=True),
    topology("researcher", limit=2001),
    topology("researcher", concurrency=True),
    topology("researcher", concurrency=17),
    {**topology("researcher"), "nodes": [{"id": "researcher", "x": float("nan")}]},
])
def test_reject_invalid_topologies(setup, bad):
    _, catalog, _ = setup
    with pytest.raises(ValueError):
        AgentGraph.parse(bad, catalog)


def test_single_node_and_default_mode_use_selected_role(setup):
    make, _, _ = setup
    runtime, llm = make()

    async def run():
        await runtime.process_message(context(topology("researcher")))
        await runtime.process_message(context(None, "webchat:default-test"))

    asyncio.run(run())
    assert [node for node, *_ in llm.calls] == ["researcher", "default"]
    assert {t["function"]["name"] for t in llm.calls[0][2]} == {"read"}
    assert tool_registry.get("message_agent") is None


def test_one_way_delivery_blocks_reply_and_preserves_role_tools(setup):
    make, _, _ = setup

    def script(node, index, messages, tools):
        names = {t["function"]["name"] for t in tools}
        assert "execute" not in names
        if node == "coordinator":
            assert not names.intersection({"delegate_task", "list_agents", "send_message"})
            if index == 0:
                return call("message_agent", target="researcher", message="Compute the NPT parameters")
        elif index == 0:
            assert "message_agent" not in names
            return call("message_agent", target="coordinator", message="forged reply")
        else:
            assert "does not permit researcher" in messages[-1].content
        return LLMResponse(content=f"{node} result")

    runtime, llm = make(script)
    events, chunks = [], []

    async def run():
        ctx = context(topology("coordinator", "researcher", edges=[("coordinator", "researcher")]))
        ctx.metadata["_on_graph_event"] = lambda event: capture(events, event)
        result = await runtime.process_message(ctx, on_stream_chunk=lambda text: capture(chunks, text))
        root = await runtime.sessions.resolve_session(ctx.chat_id)
        assert root.metadata["last_graph_run"]["status"] == "done"
        assert [m.role for m in root.messages] == ["user", "assistant"]
        children = [s for s in runtime.sessions._sessions.values() if s.metadata.get("channel") == "agent_graph"]
        assert len(children) == 2
        assert all(s.metadata["parent_session_id"] == root.session_id for s in children)
        assert "Compute the NPT parameters" in children[1].messages[0].content
        assert result.count("**researcher**") == 1

    asyncio.run(run())
    assert [e["kind"] for e in events].count("message") == 1
    assert any(e["kind"] == "blocked" for e in events)
    assert "researcher result" in "".join(chunks)


async def capture(items, value):
    items.append(value)


def test_serial_canvas_retains_fifo_feedback_and_node_history(setup):
    make, _, _ = setup

    def script(node, index, messages, tools):
        if node == "researcher" and index == 0:
            return call("message_agent", target="worker", message="Check this proposed parameter set")
        if node == "worker" and index == 0:
            return call("message_agent", target="researcher", message="Time step needs revision")
        if node == "researcher" and index == 2:
            assert any(m.role == "assistant" and m.content == "researcher result" for m in messages)
            assert "Time step needs revision" in messages[-1].content
        return LLMResponse(content=f"{node} result")

    runtime, llm = make(script)
    ctx = context(topology(
        "researcher", "worker", edges=[("researcher", "worker"), ("worker", "researcher")],
        concurrency=1,
    ))
    asyncio.run(runtime.process_message(ctx))
    record = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_graph_run"]
    assert [item["node"] for item in record["outputs"]] == ["researcher", "worker", "researcher"]
    assert len([s for s in runtime.sessions._sessions.values() if s.metadata.get("channel") == "agent_graph"]) == 2


def test_canvas_fanout_starts_multiple_receivers_before_sender_finishes(setup):
    make, _, _ = setup

    async def run():
        entered = {node: asyncio.Event() for node in ("coordinator", "researcher", "worker")}
        release = asyncio.Event()

        async def script(node, index, messages, tools):
            if node == "coordinator" and index == 0:
                calls = [
                    {**call("message_agent", target=target, message=f"ask {target}").tool_calls[0], "id": target}
                    for target in ("researcher", "worker")
                ]
                return LLMResponse(tool_calls=calls, finish_reason="tool_calls")
            if node != "coordinator":
                assert f"ask {node}" in messages[-1].content
            entered[node].set()
            await release.wait()
            return LLMResponse(content=f"{node} answer")

        runtime, _ = make(script)
        ctx = context(topology(
            "coordinator", "researcher", "worker",
            edges=[("coordinator", "researcher"), ("coordinator", "worker")],
        ))
        outputs = []
        ctx.metadata["_on_graph_output"] = lambda output: capture(outputs, output)
        pending = asyncio.create_task(runtime.process_message(ctx))
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 3)
        record = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_graph_run"]
        assert [event["node"] for event in record["events"] if event["kind"] == "node_started"] == [
            "coordinator", "researcher", "worker",
        ]
        assert not record["outputs"]
        release.set()
        await asyncio.wait_for(pending, 3)
        assert len({output["message_id"] for output in outputs}) == 3
        assert {output["node"]: output["full"] for output in outputs if "full" in output} == {
            node: f"{node} answer" for node in entered
        }

    asyncio.run(run())


def test_canvas_steers_running_receiver_after_tool_results(setup, monkeypatch):
    make, _, _ = setup

    async def run():
        reading, release, sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
        execute_tools = Agent._handle_tool_calls

        async def hold_read(self, calls, ctx):
            if calls[0]["function"]["name"] != "read":
                return await execute_tools(self, calls, ctx)
            reading.set()
            await release.wait()
            return [LLMMessage(role="tool", tool_call_id=calls[0]["id"], content="file result")]

        async def script(node, index, messages, tools):
            if node == "researcher":
                if index == 0:
                    return call("message_agent", target="worker", message="review the conditions")
                if index == 1:
                    return call("read", path="inputs.txt")
                assert messages[-2].role == "tool" and messages[-2].content == "file result"
                assert messages[-1].role == "user" and "corrected condition" in messages[-1].content
            elif index == 0:
                await reading.wait()
                return call("message_agent", target="researcher", message="corrected condition")
            else:
                assert json.loads(messages[-1].content)["status"] == "current_turn"
                sent.set()
            return LLMResponse(content=f"{node} answer")

        monkeypatch.setattr(Agent, "_handle_tool_calls", hold_read)
        runtime, _ = make(script)
        ctx = context(topology("researcher", "worker", edges=[("researcher", "worker"), ("worker", "researcher")]))
        pending = asyncio.create_task(runtime.process_message(ctx))
        await asyncio.wait_for(sent.wait(), 3)
        release.set()
        await asyncio.wait_for(pending, 3)
        record = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_graph_run"]
        assert [event["node"] for event in record["events"] if event["kind"] == "node_started"] == [
            "researcher", "worker",
        ]
        assert [e["delivery"] for e in record["events"] if e["kind"] == "message"] == ["queued", "current_turn"]

    asyncio.run(run())


def test_canvas_queues_message_for_receiver_that_is_finalizing(setup):
    make, _, _ = setup

    async def run():
        finishing, release, sent = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def on_event(event):
            if event["kind"] == "node_done" and event["message_id"] == "entry":
                finishing.set()
                await release.wait()

        async def script(node, index, messages, tools):
            if node == "researcher" and index == 0:
                return call("message_agent", target="worker", message="review")
            if node == "worker" and index == 0:
                await finishing.wait()
                return call("message_agent", target="researcher", message="follow-up after finish")
            if node == "worker":
                assert json.loads(messages[-1].content)["status"] == "queued"
                sent.set()
            if node == "researcher" and index == 2:
                assert messages[-2].content == "researcher answer"
                assert "follow-up after finish" in messages[-1].content
            return LLMResponse(content=f"{node} answer")

        runtime, _ = make(script)
        ctx = context(topology("researcher", "worker", edges=[("researcher", "worker"), ("worker", "researcher")]))
        ctx.metadata["_on_graph_event"] = on_event
        pending = asyncio.create_task(runtime.process_message(ctx))
        await asyncio.wait_for(sent.wait(), 3)
        record = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_graph_run"]
        assert sum(e["kind"] == "node_started" and e["node"] == "researcher" for e in record["events"]) == 1
        release.set()
        await asyncio.wait_for(pending, 3)
        assert [item["node"] for item in record["outputs"]] == ["researcher", "worker", "researcher"]

    asyncio.run(run())


def test_edges_do_not_automatically_execute_or_loop(setup):
    make, _, _ = setup
    runtime, llm = make()
    asyncio.run(runtime.process_message(context(topology("researcher", "worker", edges=[("researcher", "worker"), ("worker", "researcher")]))))
    assert [node for node, *_ in llm.calls] == ["researcher"]


def test_message_budget_terminates_a_cycle(setup):
    make, _, _ = setup

    def script(node, index, messages, tools):
        if messages[-1].role == "tool":
            return LLMResponse(content="handoff recorded")
        return call("message_agent", target="worker" if node == "researcher" else "researcher", message="review again")

    runtime, _ = make(script)
    ctx = context(topology("researcher", "worker", edges=[("researcher", "worker"), ("worker", "researcher")], limit=2))
    result = asyncio.run(runtime.process_message(ctx))
    record = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_graph_run"]
    assert record["status"] == "limit_reached"
    assert sum(event["kind"] == "message" for event in record["events"]) == 2
    assert "limit_reached" in result


def test_forged_delegation_and_profile_escalation_are_not_executed(setup, monkeypatch):
    make, _, _ = setup
    visited = []

    async def fake_execute(name, args, context=None):
        visited.append(name)
        return "should never be executed"

    def script(node, index, messages, tools):
        if index == 0:
            return call("delegate_task", agent="worker", objective="bypass the graph")
        assert "disabled in graph mode" in messages[-1].content
        return LLMResponse(content="stopped")

    runtime, _ = make(script)
    monkeypatch.setattr(tool_registry, "execute", fake_execute)
    asyncio.run(runtime.process_message(context(topology("coordinator"))))
    assert visited == []


def test_model_cannot_replace_skill_policy_context(setup):
    result = asyncio.run(tool_registry.execute("read_skill", {"name": "phase-transition-design", "_context": {"allowed_skills": ["*"]}}, {"allowed_tools": ["read_skill"], "allowed_skills": []}))
    assert "not allowed" in result


def test_cancel_stops_active_nodes_and_drops_queued_nodes(setup, monkeypatch):
    make, _, _ = setup
    runtime, _ = make()
    activated = []
    stopped = []
    entered = asyncio.Event()

    async def process(self, ctx, **kwargs):
        node = self.profile.name
        activated.append(node)
        try:
            if node == "researcher":
                for target in ("worker", "coordinator"):
                    await ctx.metadata["_graph_binding"].execute({"target": target, "message": "pending"})
            else:
                entered.set()
            await asyncio.Event().wait()
        finally:
            stopped.append(node)

    monkeypatch.setattr(Agent, "_process_message", process)
    ctx = context(topology(
        "researcher", "worker", "coordinator",
        edges=[("researcher", "worker"), ("researcher", "coordinator")], concurrency=2,
    ))

    async def run():
        task = asyncio.create_task(runtime.process_message(ctx))
        await asyncio.wait_for(entered.wait(), 3)
        with pytest.raises(RuntimeError, match="正在运行"):
            await runtime.process_message(ctx)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not runtime.is_running(ctx.chat_id)

    asyncio.run(run())
    assert activated == ["researcher", "worker"]
    assert set(stopped) == {"researcher", "worker"}
    assert runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_graph_run"]["status"] == "cancelled"


def test_graph_config_restores_after_restart_and_layout_keeps_context(setup):
    make, catalog, tmp = setup
    graph = topology("researcher")
    parsed = AgentGraph.parse(graph, catalog)
    moved = {**graph, "nodes": [{"id": "researcher", "x": 300, "y": 200}]}
    assert parsed.fingerprint == AgentGraph.parse(moved, catalog).fingerprint
    runtime, _ = make()

    async def run():
        session = runtime.sessions.get_or_create("webchat:restart", "webchat", "test")
        session.metadata["agent_graph"] = parsed.to_dict()
        await runtime.sessions.save(session)
        restarted, llm = make()
        await restarted.process_message(AgentContext(chat_id=session.chat_id, channel="webchat", account_id="test", user_message="start"))
        assert llm.calls[0][0] == "researcher"

    asyncio.run(run())


def test_http_configuration_and_sse_graph_run(setup):
    make, _, _ = setup

    def script(node, index, messages, tools):
        if node == "coordinator" and index == 0:
            return call("message_agent", target="researcher", message="NPT design")
        return LLMResponse(content=f"{node} complete")

    runtime, _ = make(script)
    adapter = WebChatAdapter(session_manager=runtime.sessions, agent_graph_runtime=runtime)
    adapter.set_message_handler(runtime.process_message)
    app = create_web_app(adapter)

    async def run():
        session = runtime.sessions.get_or_create("webchat:http", "webchat", "test")
        await runtime.sessions.save(session)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            profiles = (await client.get("/api/agent-profiles")).json()["profiles"]
            assert {p["name"] for p in profiles} == {"coordinator", "researcher", "worker"}
            path = f"/api/conversations/{session.chat_id}/agent-graph"
            invalid = await client.put(path, json={"graph": topology("missing")})
            assert invalid.status_code == 400
            graph = topology("coordinator", "researcher", edges=[("coordinator", "researcher")])
            assert (await client.put(path, json={"graph": graph})).status_code == 200
            runtime._active_chats.add(session.chat_id)
            assert (await client.put(path, json={"graph": None})).status_code == 409
            runtime._active_chats.clear()
            blocked = await client.post("/api/chat", json={"chat_id": session.chat_id, "message": "go", "plan_mode": True})
            assert blocked.status_code == 400
            response = await client.post("/api/chat", json={"chat_id": session.chat_id, "message": "go"})
            assert response.status_code == 200
            payloads = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert any(p.get("graph_event", {}).get("kind") == "message" for p in payloads)
            node_outputs = [p["graph_output"] for p in payloads if "graph_output" in p]
            assert {p["node"]: p["full"] for p in node_outputs if "full" in p} == {
                "coordinator": "coordinator complete", "researcher": "researcher complete",
            }
            assert {p["node"] for p in node_outputs if "delta" in p} == {"coordinator", "researcher"}
            assert payloads[-1]["done"] is True
            assert "researcher complete" in payloads[-1]["full"]
            saved = (await client.get(path)).json()
            assert saved["last_run"]["status"] == "done"
            assert (await client.put(path, json={"graph": None})).json()["graph"] is None

    asyncio.run(run())


def test_project_pause_cancels_graph_and_allows_configuration_change(setup, monkeypatch):
    make, _, tmp = setup
    runtime, _ = make()
    adapter = WebChatAdapter(session_manager=runtime.sessions, agent_graph_runtime=runtime)
    adapter.set_message_handler(runtime.process_message)
    entered = asyncio.Event()
    activated = []

    async def process(self, ctx, **kwargs):
        activated.append(self.profile.name)
        await ctx.metadata["_graph_binding"].execute({"target": "worker", "message": "queued work"})
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Agent, "_process_message", process)

    async def run():
        session = runtime.sessions.get_or_create("webchat:pause-http", "webchat", "test")
        session.metadata["agent_graph"] = runtime.validate(topology(
            "researcher", "worker", edges=[("researcher", "worker")], concurrency=1,
        )).to_dict()
        await runtime.sessions.save(session)
        workspace = tmp / "project"
        workspace.mkdir()
        project = adapter.projects.create("pause graph", "test", session.chat_id, str(workspace))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(adapter)), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/api/chat", json={"project_id": project.project_id, "message": "start"}))
            await asyncio.wait_for(entered.wait(), 5)
            path = f"/api/conversations/{session.chat_id}/agent-graph"
            assert (await client.put(path, json={"graph": None})).status_code == 409
            duplicate = await client.post("/api/chat", json={"project_id": project.project_id, "message": "duplicate"})
            assert duplicate.status_code == 409
            paused = await client.post(f"/api/projects/{project.project_id}/action", json={"action": "pause"})
            assert paused.json()["ok"]
            result = await asyncio.wait_for(pending, 5)
            assert "Project paused" in result.text
            assert (await client.get(path)).json()["last_run"]["status"] == "cancelled"
            assert (await client.put(path, json={"graph": None})).status_code == 200
            assert not adapter._project_runs

    asyncio.run(run())
    assert activated == ["researcher"]


def test_failed_node_keeps_partial_results_and_marks_run_error(setup):
    make, _, _ = setup

    def script(node, index, messages, tools):
        if node == "worker":
            raise RuntimeError("simulation input unavailable")
        if index == 0:
            return call("message_agent", target="worker", message="check input")
        return LLMResponse(content="parameter design complete")

    runtime, _ = make(script)
    ctx = context(topology("researcher", "worker", edges=[("researcher", "worker")], concurrency=1))
    with pytest.raises(RuntimeError, match="input unavailable"):
        asyncio.run(runtime.process_message(ctx))
    session = runtime.sessions.get_session_by_chat_id(ctx.chat_id)
    assert session.metadata["last_graph_run"]["status"] == "error"
    assert "parameter design complete" in session.messages[-1].content
    assert "parameter design complete" in ctx.metadata["_graph_result"]
    assert "simulation input unavailable" in ctx.metadata["_graph_result"]
    from miniclaw.channels.web.chat_run import ChatRunService
    from miniclaw.channels.web.chat_events import ChatEventStream
    service = ChatRunService(adapter=None, context=ctx, project=None, timeout=5, events=ChatEventStream())
    assert "parameter design complete" in service._with_graph_results("failed")
    assert not runtime.is_running(ctx.chat_id)


def test_node_sessions_are_isolated_between_projects_and_topologies(setup):
    make, _, _ = setup
    runtime, _ = make()

    async def run():
        first = context(topology("researcher"), "webchat:project-one")
        second = context(topology("researcher"), "webchat:project-two")
        await runtime.process_message(first)
        await runtime.process_message(second)
        first.metadata["agent_graph"] = topology("researcher", "worker", edges=[("researcher", "worker")])
        await runtime.process_message(first)

    asyncio.run(run())
    children = [s for s in runtime.sessions._sessions.values() if s.metadata.get("channel") == "agent_graph"]
    assert len(children) == 3
    assert len({s.chat_id for s in children}) == 3


def test_restart_marks_incomplete_run_interrupted(setup):
    make, _, _ = setup
    runtime, _ = make()
    adapter = WebChatAdapter(session_manager=runtime.sessions, agent_graph_runtime=runtime)

    async def run():
        session = runtime.sessions.get_or_create("webchat:interrupted", "webchat", "test")
        session.metadata["last_graph_run"] = {"run_id": "old-run", "status": "running", "events": []}
        await runtime.sessions.save(session)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(adapter)), base_url="http://test") as client:
            record = (await client.get(f"/api/conversations/{session.chat_id}/agent-graph")).json()["last_run"]
            assert record["status"] == "interrupted"
            assert record["events"][-1]["kind"] == "interrupted"

    asyncio.run(run())
