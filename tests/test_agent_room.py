"""Public-channel acceptance with real Agent loops and scripted model responses."""
import asyncio
import inspect
import json

import httpx
import pytest

from miniclaw.agent import Agent, AgentContext
from miniclaw.agents import AgentCatalog
from miniclaw.agents.room import Room
from miniclaw.agents.collaboration import CollaborationRuntime
from miniclaw.agents.room_runtime import RoomConfig
from miniclaw.llm.base import LLMMessage, LLMResponse, LLMStreamChunk
from miniclaw.memory.session import SessionManager
from miniclaw.settings import config
from miniclaw.tools.registry import tool_registry
from miniclaw.channels.webchat_adapter import WebChatAdapter
from miniclaw.channels.web.web_app import create_web_app


def call(name, **arguments):
    return LLMResponse(tool_calls=[{"id": "room-test-call", "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments),
    }}], finish_reason="tool_calls")


class ScriptedLLM:
    def __init__(self, script):
        self.script, self.calls, self.counts = script, [], {}

    async def chat(self, messages, tools=None, **kwargs):
        system = messages[0].content
        marker = "Your room member: " if "Your room member: " in system else "Your node: "
        node = system.split(marker, 1)[1].split(".", 1)[0] if marker in system else "default"
        index = self.counts.get(node, 0)
        self.counts[node] = index + 1
        self.calls.append((node, list(messages), tools or []))
        result = self.script(node, index, messages, tools or [])
        return await result if inspect.isawaitable(result) else result

    async def chat_stream(self, *args, **kwargs):
        response = await self.chat(*args, **kwargs)
        yield LLMStreamChunk(delta_content=response.content, delta_tool_calls=response.tool_calls, finish_reason=response.finish_reason)


@pytest.fixture
def room_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config.evolution, "enabled", False)
    monkeypatch.setattr(Agent, "_memory_prefix", lambda *_: "")
    monkeypatch.setattr(Agent, "_experience_prefix", lambda *_: "")
    monkeypatch.setattr("miniclaw.agent.WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(SessionManager, "_get_store", lambda _: None)
    profiles = tmp_path / "profiles"
    for name, tools in [("a", ["read", "delegate_task", "send_message"]), ("b", ["read"]), ("c", [])]:
        folder = profiles / name
        folder.mkdir(parents=True)
        (folder / "agent.yaml").write_text(json.dumps({"name": name, "description": name, "tools": tools, "skills": [], "runtime": {"max_tool_rounds": 6}}), encoding="utf-8")
    catalog = AgentCatalog([profiles])
    sessions = SessionManager(tmp_path / "sessions")

    def make(script=lambda *_: LLMResponse(content="private final"), members=None, room_id="test"):
        llm = ScriptedLLM(script)
        value = Room(members or ["a", "b"], room_id, llm=llm, sessions=sessions, catalog=catalog, workspace_dir=str(tmp_path))
        return value, llm

    return make, catalog, sessions, tmp_path


def test_registered_publish_uses_real_agent_tools_and_private_final(room_setup):
    make, _, sessions, path = room_setup
    (path / "evidence.txt").write_text("known evidence", encoding="utf-8")

    def script(node, index, messages, tools):
        names = {tool["function"]["name"] for tool in tools}
        assert "publish_message" in names
        assert not names.intersection({"delegate_task", "send_message", "execute"})
        if node == "a" and index == 0:
            return call("read", path=str(path / "evidence.txt"))
        if node == "a" and index == 1:
            assert "known evidence" in messages[-1].content
            return call("publish_message", message="verified evidence")
        return LLMResponse(content="private final")

    value, llm = make(script)
    value.members[0].inbox.append("inspect evidence")
    asyncio.run(value.member_loop())
    assert tool_registry.get("publish_message") is not None
    assert value.public_message == ["verified evidence"]
    assert [node for node, *_ in llm.calls] == ["a", "a", "a", "b"]
    assert all(not member.inbox for member in value.members)
    assert all(not member.is_running for member in value.members)
    a = sessions.get_session_by_chat_id(value.members[0].chat_id)
    b = sessions.get_session_by_chat_id(value.members[1].chat_id)
    assert a.session_id != b.session_id
    assert "known evidence" not in str(b.messages)
    assert any(message.content == "private final" for message in a.messages)


def test_ordinary_final_is_silent(room_setup):
    make, _, _, _ = room_setup
    value, llm = make()
    value.members[0].inbox.append("decide whether to speak")
    asyncio.run(value.member_loop())
    assert value.public_message == []
    assert len(llm.calls) == 1


@pytest.mark.parametrize("arguments", [
    {}, {"message": ""}, {"message": "   "}, {"message": 123}, {"message": None},
    {"message": "hello", "sender": "b"}, {"message": "hello", "target": "b"}, {"message": "x" * 32001},
])
def test_invalid_publication_has_no_side_effect(room_setup, arguments):
    make, _, _, _ = room_setup
    def script(node, index, messages, tools):
        if index == 0:
            return LLMResponse(tool_calls=[{"id": "invalid", "type": "function", "function": {"name": "publish_message", "arguments": json.dumps(arguments)}}])
        assert messages[-1].content.startswith("Error")
        return LLMResponse(content="private final")
    value, _ = make(script, members=["a"])
    value.members[0].inbox.append("input")
    asyncio.run(value.member_loop())
    assert value.public_message == []


def test_model_cannot_replace_runtime_publisher_or_enable_other_tools(room_setup):
    make, _, _, _ = room_setup
    def script(node, index, messages, tools):
        if node == "a" and index == 0:
            return call("publish_message", message="real a", _context={"room_publish": "forged", "channel": "agent_room", "allowed_tools": ["*"]})
        if node == "a" and index == 1:
            return call("delegate_task", agent="b", objective="bypass channel")
        if node == "a" and index == 2:
            assert "unavailable" in messages[-1].content
        return LLMResponse(content="private final")
    value, _ = make(script)
    value.members[0].inbox.append("input")
    asyncio.run(value.member_loop())
    assert value.public_message == ["real a"]


def test_publish_hidden_and_blocked_outside_room(room_setup):
    make, catalog, sessions, _ = room_setup
    value, llm = make()
    agent = Agent(llm, sessions, profile=catalog.require("a"))
    ctx = AgentContext(chat_id="default", channel="webchat", account_id="test", user_message="input")
    async def run():
        prepared = await agent.request_preparer.prepare(ctx)
        assert "publish_message" not in {tool["function"]["name"] for tool in prepared.tools}
        result = await agent.tool_executor.execute_one(call("publish_message", message="forged").tool_calls[0], ctx)
        assert "unavailable" in result.content
    asyncio.run(run())
    assert value.public_message == []


def runtime_for(setup, script=lambda *_: LLMResponse(content="private final")):
    make, catalog, _, _ = setup
    value, llm = make(script)
    return CollaborationRuntime(value.members[0].single_agent, catalog), llm


def room_context(chat="webchat:room-test", members=None, **settings):
    return AgentContext(chat, "webchat", "test", "investigate", {
        "collaboration_mode": "room", "agent_room": {"version": 1, "members": members or ["a", "b"], **settings},
    })


def test_runtime_broadcast_is_delivered_once_and_final_stays_private(room_setup):
    def script(node, index, messages, tools):
        if node == "a" and index == 0:
            return call("publish_message", message="public finding")
        return LLMResponse(content=f"private {node}")
    runtime, _ = runtime_for(room_setup, script)
    ctx = room_context(members=["a", "b", "c"])
    output = asyncio.run(runtime.process_message(ctx))
    root = runtime.sessions.get_session_by_chat_id(ctx.chat_id)
    record = root.metadata["last_room_run"]
    assert record["status"] == "idle"
    assert [item["author"] for item in record["messages"]] == ["user", "a"]
    assert "private" not in output and "public finding" in output
    message_id = record["messages"][1]["message_id"]
    children = [s for s in runtime.sessions._sessions.values() if s.metadata.get("channel") == "agent_room"]
    assert len(children) == 3
    for child in children:
        incoming = [m.content for m in child.messages if m.role == "user"]
        assert sum(text.count(message_id) for text in incoming) == (0 if child.chat_id.endswith(":a") else 1)


def test_concurrency_limit_and_fair_delivery(room_setup):
    async def run():
        gate, first_two = asyncio.Event(), asyncio.Event()
        active = peak = 0
        entered = []
        async def script(node, index, messages, tools):
            nonlocal active, peak
            entered.append(node)
            active += 1
            peak = max(peak, active)
            if len(entered) == 2:
                first_two.set()
            try:
                await gate.wait()
                return LLMResponse(content="private")
            finally:
                active -= 1
        runtime, _ = runtime_for(room_setup, script)
        pending = asyncio.create_task(runtime.process_message(room_context(members=["a", "b", "c"], max_concurrency=2)))
        await asyncio.wait_for(first_two.wait(), 3)
        assert len(entered) == 2 and peak == 2
        gate.set()
        await asyncio.wait_for(pending, 3)
        assert set(entered) == {"a", "b", "c"} and peak == 2
    asyncio.run(run())


def test_new_broadcast_wakes_idle_member_while_sender_still_running(room_setup):
    async def run():
        b_initial, b_received = asyncio.Event(), asyncio.Event()
        async def script(node, index, messages, tools):
            if node == "a" and index == 0:
                await b_initial.wait()
                return call("publish_message", message="new event")
            if node == "a" and index == 1:
                await b_received.wait()
            if node == "b" and index == 0:
                b_initial.set()
            if node == "b" and index == 1:
                assert "new event" in messages[-1].content
                b_received.set()
            return LLMResponse(content="private")
        runtime, _ = runtime_for(room_setup, script)
        await asyncio.wait_for(runtime.process_message(room_context(max_concurrency=2)), 3)
        assert b_received.is_set()
    asyncio.run(run())


def test_busy_member_receives_peer_message_after_tool_results(room_setup, monkeypatch):
    async def run():
        a_started, release_a, b_published = asyncio.Event(), asyncio.Event(), asyncio.Event()
        execute_tools = Agent._handle_tool_calls

        async def hold_a_tool(self, calls, context):
            if self.profile.name != "a":
                return await execute_tools(self, calls, context)
            a_started.set()
            await release_a.wait()
            return [LLMMessage(role="tool", tool_call_id=calls[0]["id"], content="read result")]

        monkeypatch.setattr(Agent, "_handle_tool_calls", hold_a_tool)

        async def script(node, index, messages, tools):
            if node == "a" and index == 0:
                return call("read", path="evidence.txt")
            if node == "a" and index == 1:
                assert [(m.role, m.content) for m in messages[-2:-1]] == [("tool", "read result")]
                assert messages[-1].role == "user" and "from b" in messages[-1].content
            if node == "b" and index == 0:
                await a_started.wait()
                return call("publish_message", message="from b")
            if node == "b" and index == 1:
                b_published.set()
            return LLMResponse(content="private")
        runtime, llm = runtime_for(room_setup, script)
        ctx = room_context(max_activations=2)
        pending = asyncio.create_task(runtime.process_message(ctx))
        await asyncio.wait_for(b_published.wait(), 3)
        assert llm.counts["a"] == 1
        release_a.set()
        await asyncio.wait_for(pending, 3)
        assert llm.counts["a"] == 2
        run = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_room_run"]
        assert run["activations"] == 2 and run["status"] == "idle"
    asyncio.run(run())


def test_concurrent_last_message_slot_and_cycle_stop(room_setup):
    def script(node, index, messages, tools):
        if messages[-1].role == "tool":
            return LLMResponse(content="private")
        return call("publish_message", message=f"proposal {node}")
    runtime, _ = runtime_for(room_setup, script)
    ctx = room_context(max_messages=1)
    asyncio.run(asyncio.wait_for(runtime.process_message(ctx), 3))
    record = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_room_run"]
    assert record["published"] == 1 and len(record["messages"]) == 2
    assert record["status"] == "limit_reached"
    assert not runtime.is_running(ctx.chat_id)


@pytest.mark.parametrize("outcome", ["cancel", "error"])
def test_room_stops_workers_revokes_late_publish_and_can_run_again(room_setup, outcome):
    async def run():
        entered, captured = asyncio.Event(), []
        runtime, _ = runtime_for(room_setup)
        original = Agent.process_message
        async def process(self, context, **kwargs):
            captured.append(context.metadata["_room_publish"])
            entered.set()
            if outcome == "error":
                raise RuntimeError("broken input")
            await asyncio.Event().wait()
        from unittest.mock import patch
        ctx = room_context()
        with patch.object(Agent, "process_message", process):
            pending = asyncio.create_task(runtime.process_message(ctx))
            await entered.wait()
            if outcome == "cancel":
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                with pytest.raises(RuntimeError, match="broken input"):
                    await pending
        assert not runtime.is_running(ctx.chat_id)
        record = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_room_run"]
        assert record["status"] == ("cancelled" if outcome == "cancel" else "error")
        assert "ended" in await captured[0]("late publication")
        assert record["published"] == 0
        await runtime.process_message(room_context())
    asyncio.run(run())


def test_activation_budget_and_private_sessions_across_rounds_and_rooms(room_setup):
    runtime, llm = runtime_for(room_setup)
    async def run():
        await runtime.process_message(room_context(max_activations=1))
        root = runtime.sessions.get_session_by_chat_id("webchat:room-test")
        assert root.metadata["last_room_run"]["status"] == "limit_reached"
        assert len(llm.calls) == 1
        assert root.metadata["last_room_run"]["discarded_messages"] == 1
        await runtime.process_message(room_context())
        first = {s.chat_id for s in runtime.sessions._sessions.values() if s.metadata.get("channel") == "agent_room"}
        await runtime.process_message(room_context())
        assert first == {s.chat_id for s in runtime.sessions._sessions.values() if s.metadata.get("channel") == "agent_room"}
        await runtime.process_message(room_context(chat="webchat:other-room"))
        second = {s.chat_id for s in runtime.sessions._sessions.values() if s.metadata.get("channel") == "agent_room"}
        assert len(second) == len(first) + 2
        seqs = [m["sequence"] for m in root.metadata["room_channels"][RoomConfig.parse(root.metadata["last_room_run"]["config"], runtime.catalog).fingerprint]["messages"]]
        assert seqs == sorted(set(seqs))
    asyncio.run(run())


@pytest.mark.parametrize("raw", [{}, {"members": []}, {"members": ["a", "a"]}, {"members": ["unknown"]}, {"members": ["a"], "max_concurrency": True}, {"members": ["a"], "max_messages": 0}])
def test_invalid_room_config(room_setup, raw):
    _, catalog, _, _ = room_setup
    with pytest.raises(ValueError):
        RoomConfig.parse(raw, catalog)


def web_adapter(runtime):
    adapter = WebChatAdapter(session_manager=runtime.sessions, agent_graph_runtime=runtime.graph_runtime, collaboration_runtime=runtime)
    adapter.set_message_handler(runtime.process_message)
    return adapter


def test_room_steering_reaches_active_and_idle_members_once(room_setup, monkeypatch):
    async def run():
        executing, release = asyncio.Event(), asyncio.Event()

        async def execute_tools(self, calls, context):
            executing.set()
            await release.wait()
            return [LLMMessage(role="tool", content=tc["id"], tool_call_id=tc["id"]) for tc in calls]

        def script(node, index, messages, tools):
            if node == "a" and index == 0:
                calls = [call("read", path=path).tool_calls[0] for path in ("a.txt", "b.txt")]
                for name, tc in zip(("read-a", "read-b"), calls):
                    tc["id"] = name
                return LLMResponse(tool_calls=calls, finish_reason="tool_calls")
            return LLMResponse(content="private final")

        monkeypatch.setattr(Agent, "_handle_tool_calls", execute_tools)
        runtime, llm = runtime_for(room_setup, script)
        adapter = web_adapter(runtime)
        root = runtime.sessions.get_or_create("webchat:room-steer", "webchat", "test")
        root.metadata.update(collaboration_mode="room", agent_room={"members": ["a", "b"], "max_concurrency": 1})
        project = adapter.projects.create("room steer", "test", root.chat_id, str(room_setup[-1]))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(adapter)), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/api/chat", json={"project_id": project.project_id, "message": "start"}))
            await asyncio.wait_for(executing.wait(), 3)
            projects = (await client.get("/api/projects")).json()["projects"]
            assert projects[0]["steering_run_id"] == root.metadata["last_room_run"]["run_id"]
            first = {"chat_id": root.chat_id, "run_id": projects[0]["steering_run_id"], "message_id": "one", "message": "only 300 K"}
            second = {**first, "message_id": "two", "message": "report uncertainty"}
            assert (await client.post("/api/chat/steer", json={**first, "run_id": "old"})).status_code == 409
            duplicates = await asyncio.gather(*(client.post("/api/chat/steer", json=first) for _ in range(2)))
            assert all(response.json() == {"ok": True} for response in duplicates)
            assert (await client.post("/api/chat/steer", json=second)).json() == {"ok": True}
            assert [node for node, *_ in llm.calls] == ["a"]
            release.set()
            response = await asyncio.wait_for(pending, 3)
            assert (await client.post("/api/chat/steer", json=second)).status_code == 409

        _, a_messages, _ = llm.calls[1]
        assert [node for node, *_ in llm.calls] == ["a", "a", "b"]
        assert [m.role for m in a_messages[-5:]] == ["assistant", "tool", "tool", "user", "user"]
        assert [m.tool_call_id for m in a_messages[-4:-2]] == ["read-a", "read-b"]
        assert [m.content.splitlines()[-1] for m in a_messages[-2:]] == [first["message"], second["message"]]
        b_input = llm.calls[2][1][-1].content
        assert b_input.index("start") < b_input.index(first["message"]) < b_input.index(second["message"])
        assert all(b_input.count(item["message"]) == 1 for item in (first, second))
        record = root.metadata["last_room_run"]
        assert record["activations"] == 2 and record["published"] == 0
        assert [item["content"] for item in record["messages"]] == ["start", first["message"], second["message"]]
        payloads = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert [p["room_event"]["message"]["content"] for p in payloads if p.get("room_event", {}).get("kind") == "message"] == [first["message"], second["message"]]
        assert payloads[-1]["done"]

    asyncio.run(run())


@pytest.mark.parametrize("max_rounds", [1, 2])
def test_default_steering_continues_after_text_reply(room_setup, monkeypatch, max_rounds):
    monkeypatch.setattr(config.agent, "max_tool_rounds", max_rounds)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def script(node, index, messages, tools):
            if index == 0:
                entered.set()
                await release.wait()
                return LLMResponse(content="initial reply")
            return LLMResponse(content="updated reply")

        _, catalog, sessions, _ = room_setup
        llm = ScriptedLLM(script)
        runtime = CollaborationRuntime(Agent(llm, sessions), catalog)
        adapter = web_adapter(runtime)
        chat_id = "webchat:default-steer"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(adapter)), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/api/chat", json={"chat_id": chat_id, "message": "start"}))
            await asyncio.wait_for(entered.wait(), 3)
            run_id = runtime.steering_run_id(chat_id)
            steer = {"chat_id": chat_id, "run_id": run_id, "message_id": "one", "message": "new constraint"}
            assert (await client.post("/api/chat/steer", json={**steer, "chat_id": "webchat:other"})).status_code == 409
            assert (await client.post("/api/chat/steer", json=steer)).json() == {"ok": True}
            release.set()
            response = await asyncio.wait_for(pending, 3)
            assert (await client.post("/api/chat/steer", json=steer)).status_code == 409

        assert len(llm.calls) == 2
        assert [(m.role, m.content) for m in llm.calls[1][1][-2:]] == [
            ("assistant", "initial reply"), ("user", "new constraint"),
        ]
        assert bool(llm.calls[1][2]) == (max_rounds > 1)
        payloads = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert payloads[0]["steering_run_id"] == run_id
        assert payloads[-1] == {"done": True, "full": "updated reply"}
        assert [m.content for m in sessions.get_session_by_chat_id(chat_id).messages if m.role == "user"] == ["start", "new constraint"]

    asyncio.run(run())


def test_cancelling_tool_batch_keeps_accepted_steering_in_history(room_setup, monkeypatch):
    async def run():
        executing = asyncio.Event()

        async def execute_tools(self, calls, context):
            executing.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(Agent, "_handle_tool_calls", execute_tools)
        runtime, llm = runtime_for(room_setup, lambda *_: call("read", path="pending.txt"))
        context = AgentContext("webchat:cancelled-steer", "webchat", "test", "start")
        pending = asyncio.create_task(runtime.process_message(context))
        await asyncio.wait_for(executing.wait(), 3)
        run_id = runtime.steering_run_id(context.chat_id)
        assert await runtime.steer(context.chat_id, run_id, "one", "new constraint")
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

        restored = SessionManager(runtime.sessions.save_dir)
        session = await restored.resolve_session(context.chat_id)
        assert [(message.role, message.content) for message in session.messages] == [
            ("user", "start"), ("user", "new constraint"),
        ]
        assert len(llm.calls) == 1
        assert runtime.steering_run_id(context.chat_id) is None

    asyncio.run(run())


def test_http_mode_switch_sse_and_legacy_canvas(room_setup):
    def script(node, index, messages, tools):
        if node == "a" and index == 0 and "Your room member:" in messages[0].content:
            return call("publish_message", message="public HTTP evidence")
        return LLMResponse(content="private final")
    runtime, _ = runtime_for(room_setup, script)
    adapter = web_adapter(runtime)
    async def run():
        root = runtime.sessions.get_or_create("webchat:room-http", "webchat", "test")
        await runtime.sessions.save(root)
        base = f"/api/conversations/{root.chat_id}"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(adapter)), base_url="http://test") as client:
            assert (await client.get(base + "/collaboration")).json()["mode"] == "default"
            assert (await client.put(base + "/collaboration", json={"mode": "bad"})).status_code == 400
            assert (await client.put(base + "/collaboration", json={"mode": None})).status_code == 400
            assert (await client.put(base + "/collaboration", json={"mode": "room", "room": {"members": ["bad"]}})).status_code == 400
            graph = {"version": 1, "nodes": [{"id": "a"}], "edges": [], "entry": "a", "max_messages": 24}
            assert (await client.put(base + "/agent-graph", json={"graph": graph})).status_code == 200
            assert (await client.get(base + "/collaboration")).json()["mode"] == "graph"
            assert (await client.put(base + "/collaboration", json={"mode": "room", "room": {"members": ["a", "b"]}})).status_code == 200
            assert (await client.post("/api/chat", json={"chat_id": root.chat_id, "message": "go", "plan_mode": True})).status_code == 400
            response = await client.post("/api/chat", json={"chat_id": root.chat_id, "message": "go"})
            payloads = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert any(item.get("room_event", {}).get("kind") == "message" for item in payloads)
            assert payloads[-1]["done"] and "public HTTP evidence" in payloads[-1]["full"]
            assert "private final" not in response.text
            stored = (await client.get(base + "/collaboration")).json()
            assert stored["last_room_run"]["status"] == "idle"
            assert [m["author"] for m in stored["channel"]["messages"]] == ["user", "a"]
            assert stored["graph"] is not None
            assert (await client.put(base + "/collaboration", json={"mode": "default"})).status_code == 200
            await client.post("/api/chat", json={"chat_id": root.chat_id, "message": "default"})
            assert (await client.get(base + "/collaboration")).json()["room"] is not None
            assert (await client.put(base + "/collaboration", json={"mode": "graph"})).status_code == 200
            assert (await client.put(base + "/agent-graph", json={"graph": None})).status_code == 200
            assert (await client.get(base + "/collaboration")).json()["mode"] == "default"
    asyncio.run(run())


def test_running_room_blocks_all_mode_edit_routes_and_pause_cleans_up(room_setup):
    async def run():
        entered = asyncio.Event()
        async def script(*_):
            entered.set()
            await asyncio.Event().wait()
        runtime, _ = runtime_for(room_setup, script)
        adapter = web_adapter(runtime)
        root = runtime.sessions.get_or_create("webchat:room-pause", "webchat", "test")
        root.metadata.update(collaboration_mode="room", agent_room={"members": ["a", "b"]})
        await runtime.sessions.save(root)
        path = room_setup[-1] / "project"
        path.mkdir()
        project = adapter.projects.create("room pause", "test", root.chat_id, str(path))
        base = f"/api/conversations/{root.chat_id}"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(adapter)), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/api/chat", json={"project_id": project.project_id, "message": "start"}))
            await asyncio.wait_for(entered.wait(), 3)
            assert (await client.put(base + "/collaboration", json={"mode": "default"})).status_code == 409
            assert (await client.put(base + "/agent-graph", json={"graph": None})).status_code == 409
            assert (await client.post("/api/chat", json={"project_id": project.project_id, "message": "duplicate"})).status_code == 409
            await client.post(f"/api/projects/{project.project_id}/action", json={"action": "pause"})
            response = await asyncio.wait_for(pending, 3)
            assert "Project paused" in response.text
            assert (await client.get(base + "/collaboration")).json()["last_room_run"]["status"] == "cancelled"
            assert not runtime.is_running(root.chat_id)
            assert (await client.put(base + "/collaboration", json={"mode": "default"})).status_code == 200
    asyncio.run(run())


def test_snapshot_is_fixed_and_restart_marks_interrupted_without_replay(room_setup):
    runtime, _ = runtime_for(room_setup)
    async def run():
        root = runtime.sessions.get_or_create("webchat:room-restart", "webchat", "test")
        root.metadata.update(collaboration_mode="room", agent_room={"members": ["a", "b"]})
        snapshot = runtime.snapshot(root.metadata)
        root.metadata["agent_room"]["members"].append("c")
        assert snapshot["agent_room"]["members"] == ["a", "b"]
        ctx = AgentContext(root.chat_id, "webchat", "test", "start", snapshot)
        await runtime.process_message(ctx)
        assert root.metadata["last_room_run"]["config"]["members"] == ["a", "b"]
        root.metadata["last_room_run"]["status"] = "running"
        await runtime.sessions.save(root)
        restored_sessions = SessionManager(runtime.sessions.save_dir)
        restored = CollaborationRuntime(Agent(ScriptedLLM(lambda *_: LLMResponse(content="unused")), restored_sessions, profile=runtime.catalog.require("a")), runtime.catalog)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(web_adapter(restored))), base_url="http://test") as client:
            payload = (await client.get(f"/api/conversations/{root.chat_id}/collaboration")).json()
        assert payload["mode"] == "room" and payload["last_room_run"]["status"] == "interrupted"
        assert not restored.is_running(root.chat_id)
    asyncio.run(run())


def test_planned_room_round_does_not_mark_task_complete(room_setup):
    runtime, _ = runtime_for(room_setup)
    adapter = web_adapter(runtime)
    async def run():
        root = runtime.sessions.get_or_create("webchat:room-plan", "webchat", "test")
        root.metadata.update(collaboration_mode="room", agent_room={"members": ["a"]})
        await runtime.sessions.save(root)
        project = adapter.projects.create("room plan", "test", root.chat_id, str(room_setup[-1]))
        adapter.projects.add_task(project.project_id, "verify actual outputs")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(adapter)), base_url="http://test") as client:
            assert (await client.post(f"/api/projects/{project.project_id}/run-next")).json()["ok"]
            await adapter._project_runs[project.project_id]
        assert not adapter.projects.get(project.project_id).tasks[0]["done"]
    asyncio.run(run())


def test_room_timeout_cancels_members_and_unlocks_conversation(room_setup):
    async def script(*_):
        await asyncio.Event().wait()
    runtime, _ = runtime_for(room_setup, script)
    ctx = room_context(timeout_sec=1)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(runtime.process_message(ctx))
    assert not runtime.is_running(ctx.chat_id)
    record = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_room_run"]
    assert record["status"] == "timed_out" and record.get("ended_at")


def test_concurrent_rooms_do_not_share_cancellation_or_private_history(room_setup):
    async def run():
        first_started, second_started, release_second = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def script(node, index, messages, tools):
            text = messages[-1].content
            if "first request" in text:
                first_started.set()
                await asyncio.Event().wait()
            second_started.set()
            await release_second.wait()
            return LLMResponse(content="private")
        runtime, _ = runtime_for(room_setup, script)
        first, second = room_context(chat="first", members=["a"]), room_context(chat="second", members=["a"])
        first.user_message, second.user_message = "first request", "second request"
        pending_first = asyncio.create_task(runtime.process_message(first))
        pending_second = asyncio.create_task(runtime.process_message(second))
        await asyncio.wait_for(asyncio.gather(first_started.wait(), second_started.wait()), 3)
        pending_first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending_first
        assert runtime.is_running("second") and not runtime.is_running("first")
        release_second.set()
        await asyncio.wait_for(pending_second, 3)
        assert runtime.sessions.get_session_by_chat_id("first").metadata["last_room_run"]["status"] == "cancelled"
        assert runtime.sessions.get_session_by_chat_id("second").metadata["last_room_run"]["status"] == "idle"
    asyncio.run(run())
