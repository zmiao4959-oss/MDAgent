import asyncio
from ast import literal_eval
import json
from pathlib import Path

import pytest

from miniclaw.agent import Agent, AgentContext
from miniclaw.agents import AgentCatalog
from miniclaw.llm.base import LLMMessage, LLMResponse
from miniclaw.memory.session import SessionManager
from miniclaw.memory.session_store import SessionStore
from miniclaw.settings import active_workspace_dir, config
from miniclaw.subagent import SubAgentManager
from miniclaw.tools import delegation_tool


class RecordingLLM:
    def __init__(self):
        self.calls = []
        self.release = None
        self.fail_first = False

    async def chat(self, messages, tools=None, **kwargs):
        self.calls.append(([(m.role, m.content) for m in messages], active_workspace_dir()))
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("temporary LLM failure")
        if len(self.calls) == 2 and self.release is not None:
            await self.release.wait()
        return LLMResponse(content=f"reply-{len(self.calls)}")


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from miniclaw.memory import session_store

    monkeypatch.setattr(session_store, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(config.evolution, "enabled", False)
    monkeypatch.setattr(Agent, "_memory_prefix", lambda self, message: "")
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    sessions = SessionManager(tmp_path / "sessions")
    sessions._store = store
    llm = RecordingLLM()
    # Continuation tests must not depend on a packaged role that may be jailed.
    profile = tmp_path / "profiles" / "general-worker"
    profile.mkdir(parents=True)
    (profile / "agent.yaml").write_text(
        "name: general-worker\ndescription: isolated continuation test role\ntools: []\nskills: []\n",
        encoding="utf-8",
    )
    result = SubAgentManager(Agent(llm, sessions), sessions, catalog=AgentCatalog([profile.parent]))
    monkeypatch.setattr(delegation_tool, "_manager", result)
    yield result, llm
    store.close()


@pytest.mark.parametrize("sandbox", [False, True])
def test_continue_preserves_history_workspace_and_session_file(manager, tmp_path, sandbox):
    manager, llm = manager
    parent = manager.sessions.get_or_create("parent", "webchat")
    parent.metadata["project_id"] = "project-a"

    async def run():
        await manager.sessions.save(parent)
        task_id = await manager.spawn("first instruction", parent_session_id="parent",
                                      sandbox=sandbox, workspace_dir=str(tmp_path))
        assert await manager.wait(task_id) == "reply-1"
        task = manager.get(task_id)
        original_sandbox = task.sandbox_dir
        workdir = Path(original_sandbox) if sandbox else tmp_path
        (workdir / "keep.txt").write_text("keep", encoding="utf-8")
        previous = await manager.sessions.resolve_session(f"subagent:{task_id}")
        session_id = previous.session_id
        session_path = manager.sessions._json_path(previous)

        # Even after a cache clear and a parent project change, reuse the child's lineage.
        parent.metadata["project_id"] = "project-b"
        await manager.sessions.save(parent)
        manager.sessions._sessions.clear()
        manager.sessions._chat_to_session.clear()
        manager.sessions._session_paths.clear()
        receipt = await delegation_tool.continue_task_tool(task_id, "follow-up", _context={"chat_id": "parent"})
        assert receipt["task_id"] == task_id and receipt["continued"] is True
        assert await manager.wait(task_id) == "reply-2"
        current = await manager.sessions.resolve_session(f"subagent:{task_id}")
        assert current.session_id == session_id
        assert manager.sessions._json_path(current) == session_path
        assert current.metadata["project_id"] == "project-a"
        assert [m.content for m in current.messages if m.role == "user"] == ["first instruction", "follow-up"]
        assert ("assistant", "reply-1") in llm.calls[1][0]
        assert llm.calls[0][1] == llm.calls[1][1] == workdir
        assert task.sandbox_dir == original_sandbox
        assert (workdir / "keep.txt").read_text(encoding="utf-8") == "keep"
        assert len(task.collected_files) == len(set(task.collected_files))
        assert len(list(manager.sessions.save_dir.rglob("subsessions/*.json"))) == 1
        if sandbox:
            await manager.kill(task_id)

    asyncio.run(run())


def test_parallel_continuations_start_only_one_turn(manager, tmp_path):
    manager, llm = manager

    async def run():
        task_id = await manager.spawn("first", parent_session_id="parent", sandbox=False, workspace_dir=str(tmp_path))
        await manager.wait(task_id)
        llm.release = asyncio.Event()
        results = await asyncio.gather(*(
            delegation_tool.continue_task_tool(task_id, "next", _context={"chat_id": "parent"})
            for _ in range(2)
        ))
        assert sum(result.get("continued", False) for result in results) == 1
        assert sum(result["status"] == "error" for result in results) == 1
        llm.release.set()
        assert await manager.wait(task_id) == "reply-2"
        assert len(llm.calls) == 2

    asyncio.run(run())


def test_continuation_rejects_wrong_parent_and_missing_workspace(manager, tmp_path):
    manager, llm = manager

    async def run():
        workspace = tmp_path / "project"
        workspace.mkdir()
        task_id = await manager.spawn("first", parent_session_id="parent", sandbox=False, workspace_dir=str(workspace))
        await manager.wait(task_id)
        foreign = await delegation_tool.continue_task_tool(task_id, "next", _context={"chat_id": "other"})
        assert foreign["status"] == "error"
        empty = await delegation_tool.continue_task_tool(task_id, " ", _context={"chat_id": "parent"})
        assert empty["status"] == "error"
        workspace.rmdir()
        missing = await delegation_tool.continue_task_tool(task_id, "next", _context={"chat_id": "parent"})
        assert missing["status"] == "error"
        assert len(llm.calls) == 1

    asyncio.run(run())


def test_failed_task_can_continue_from_saved_history(manager, tmp_path):
    manager, llm = manager
    llm.fail_first = True

    async def run():
        task_id = await manager.spawn("first", parent_session_id="parent", sandbox=False, workspace_dir=str(tmp_path))
        assert "temporary LLM failure" in await manager.wait(task_id)
        assert manager.get(task_id).status == "error"
        await delegation_tool.continue_task_tool(task_id, "recover", _context={"chat_id": "parent"})
        assert await manager.wait(task_id) == "reply-2"
        assert ("user", "first") in llm.calls[1][0]
        assert ("user", "recover") in llm.calls[1][0]

    asyncio.run(run())


def test_missing_history_never_starts_a_fresh_conversation(manager, tmp_path):
    manager, llm = manager

    async def run():
        task_id = await manager.spawn("first", parent_session_id="parent", sandbox=False, workspace_dir=str(tmp_path))
        await manager.wait(task_id)
        await manager.sessions.delete_session(f"subagent:{task_id}")
        await delegation_tool.continue_task_tool(task_id, "next", _context={"chat_id": "parent"})
        assert "without its history" in await manager.wait(task_id)
        assert len(llm.calls) == 1

    asyncio.run(run())


def test_delegate_task_messages_active_and_idle_child(manager, tmp_path, monkeypatch):
    manager, llm = manager

    async def run():
        executing, release = asyncio.Event(), asyncio.Event()
        chat = llm.chat

        async def chat_with_tool(*args, **kwargs):
            response = await chat(*args, **kwargs)
            if len(llm.calls) == 1:
                return LLMResponse(tool_calls=[{
                    "id": "read-1", "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }])
            return response

        async def execute_tools(self, calls, context):
            executing.set()
            await release.wait()
            return [LLMMessage(role="tool", tool_call_id=calls[0]["id"], content="read result")]

        monkeypatch.setattr(llm, "chat", chat_with_tool)
        monkeypatch.setattr(Agent, "_handle_tool_calls", execute_tools)
        parent = {"chat_id": "parent", "workspace_dir": str(tmp_path)}
        started = await delegation_tool.delegate_task_tool("general-worker", "first", _context=parent)
        task_id = started["task_id"]
        original_worker = manager._running[task_id]
        await asyncio.wait_for(executing.wait(), 3)

        foreign = await delegation_tool.delegate_task_tool(
            task_id=task_id, message="unrelated", _context={"chat_id": "other"},
        )
        assert foreign["status"] == "error"
        args = {"task_id": task_id, "message": "update during tool", "idempotency_key": "update-1"}
        result = await manager.agent.tool_executor.execute_one({
            "id": "delegate-message-1",
            "function": {"name": "delegate_task", "arguments": json.dumps(args)},
        }, AgentContext("parent", "webchat", "test"))
        receipt = literal_eval(result.content)
        retry = await delegation_tool.delegate_task_tool(**args, _context=parent)
        assert receipt["task_id"] == task_id and receipt["delivery"] == "current_turn"
        assert retry["deduplicated"] is True and manager._running[task_id] is original_worker
        release.set()
        assert await manager.wait(task_id, timeout=3) == "reply-2"
        assert llm.calls[1][0][-2:] == [("tool", "read result"), ("user", args["message"])]
        assert (await delegation_tool.delegate_task_tool(**args, _context=parent))["deduplicated"]
        assert len(llm.calls) == 2

        follow_up = await delegation_tool.delegate_task_tool(
            task_id=task_id, message="follow-up when idle", _context=parent,
        )
        assert follow_up["task_id"] == task_id and follow_up["delivery"] == "next_turn"
        assert await manager.wait(task_id, timeout=3) == "reply-3"
        assert llm.calls[2][0][-2:] == [("assistant", "reply-2"), ("user", "follow-up when idle")]
        assert all(workspace == tmp_path for _, workspace in llm.calls)
        assert len(manager.list_all()) == 1

    asyncio.run(run())


def test_delegate_task_messages_during_finalization_run_next_in_order(manager, tmp_path):
    manager, llm = manager

    async def run():
        finalizing, release = asyncio.Event(), asyncio.Event()

        async def callback(event):
            if event["status"] == "done" and not finalizing.is_set():
                finalizing.set()
                await release.wait()

        task_id = await manager.spawn(
            "first", parent_session_id="parent", sandbox=False,
            workspace_dir=str(tmp_path), event_callback=callback,
        )
        worker = manager._running[task_id]
        await asyncio.wait_for(finalizing.wait(), 3)
        for text in ("next one", "next two"):
            receipt = await delegation_tool.delegate_task_tool(
                task_id=task_id, message=text, _context={"chat_id": "parent"},
            )
            assert receipt["delivery"] == "next_turn" and receipt["status"] == "pending"
        assert manager._running[task_id] is worker and len(llm.calls) == 1
        release.set()
        assert await manager.wait(task_id, timeout=3) == "reply-2"
        assert llm.calls[1][0][-3:] == [
            ("assistant", "reply-1"), ("user", "next one"), ("user", "next two"),
        ]

    asyncio.run(run())


def test_wait_returns_reply_from_worker_it_observed(manager, tmp_path, monkeypatch):
    manager, llm = manager

    async def run():
        first_entered, first_release = asyncio.Event(), asyncio.Event()
        second_release = asyncio.Event()
        chat = llm.chat

        async def gated_chat(*args, **kwargs):
            if not llm.calls:
                first_entered.set()
                await first_release.wait()
            return await chat(*args, **kwargs)

        monkeypatch.setattr(llm, "chat", gated_chat)
        llm.release = second_release
        task_id = await manager.spawn(
            "first", parent_session_id="parent", sandbox=False, workspace_dir=str(tmp_path),
        )
        worker = manager._running[task_id]
        await asyncio.wait_for(first_entered.wait(), 3)
        follow_up = asyncio.get_running_loop().create_future()

        async def send_follow_up():
            receipt = await manager.send_task_message(task_id, "next", parent_session_id="parent")
            follow_up.set_result(receipt)

        worker.add_done_callback(lambda _: asyncio.create_task(send_follow_up()))
        waiter = asyncio.create_task(manager.wait(task_id, timeout=3))
        await asyncio.sleep(0)
        first_release.set()
        receipt = await asyncio.wait_for(follow_up, 3)
        assert receipt["delivery"] == "next_turn"
        assert await waiter == "reply-1"
        second_release.set()
        assert await manager.wait(task_id, timeout=3) == "reply-2"

    asyncio.run(run())
