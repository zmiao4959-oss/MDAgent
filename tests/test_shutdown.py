"""Ctrl+C stops Agent runtimes, without contacting an LLM or running MD."""
import asyncio
import signal
from types import SimpleNamespace

import pytest

from miniclaw.agent import Agent, AgentContext
from miniclaw.agents import AgentCatalog
from miniclaw.memory.session import Session
from miniclaw.subagent import SubAgentManager


class MemorySessions:
    def __init__(self):
        self.sessions = {}

    def get(self, chat_id):
        return self.sessions.get(chat_id)

    async def resolve_session(self, chat_id):
        return self.get(chat_id)

    def get_or_create(self, chat_id, channel="unknown", account_id=""):
        return self.sessions.setdefault(chat_id, Session("session:" + chat_id, chat_id))

    async def save(self, session):
        self.sessions[session.chat_id] = session


class BlockingLLM:
    def __init__(self, expected_calls):
        self.expected_calls = expected_calls
        self.calls = 0
        self.started = asyncio.Event()

    async def chat(self, *args, **kwargs):
        self.calls += 1
        if self.calls == self.expected_calls:
            self.started.set()
        await asyncio.Event().wait()


@pytest.fixture
def shutdown_state(monkeypatch, tmp_path):
    monkeypatch.setattr(Agent, "_shutdown_requested", False)
    monkeypatch.setattr(Agent, "_running_tasks", set())
    monkeypatch.setattr(Agent, "_memory_prefix", lambda *args: "")
    monkeypatch.setattr(Agent, "_experience_prefix", lambda *args: "")
    profiles = tmp_path / "profiles"
    for name in ("coordinator", "general-worker"):
        directory = profiles / name
        directory.mkdir(parents=True)
        (directory / "agent.yaml").write_text(
            f"name: {name}\ndescription: Test agent\nprompt: SYSTEM.md\nskills: []\ntools:\n  allow: []\n",
            encoding="utf-8",
        )
        (directory / "SYSTEM.md").write_text("Test agent.", encoding="utf-8")
    return AgentCatalog([profiles])


@pytest.mark.parametrize("parent_waits_for_child", [False, True])
def test_shutdown_stops_main_and_detached_child_and_blocks_new_calls(
    tmp_path, monkeypatch, shutdown_state, parent_waits_for_child,
):
    async def run():
        llm = BlockingLLM(1 if parent_waits_for_child else 2)
        agent = Agent(llm, MemorySessions())
        manager = SubAgentManager(agent, agent.sessions, catalog=shutdown_state)
        child = {}

        async def parent_turn(context, **kwargs):
            child["id"] = await manager.spawn(
                "child", parent_session_id="main", sandbox=False, workspace_dir=str(tmp_path),
            )
            return await manager.wait(child["id"])

        if parent_waits_for_child:
            monkeypatch.setattr(agent, "_process_message", parent_turn)
        primary = asyncio.create_task(agent.process_message(AgentContext("main", "test", "test")))
        if not parent_waits_for_child:
            child["id"] = await manager.spawn(
                "child", parent_session_id="main", sandbox=False, workspace_dir=str(tmp_path),
            )
        try:
            await asyncio.wait_for(llm.started.wait(), 2)
            runtimes = Agent.request_shutdown()
            await asyncio.wait_for(asyncio.gather(*runtimes, return_exceptions=True), 2)
            assert primary.cancelled()
            assert manager._running[child["id"]].cancelled()
            assert not Agent._running_tasks
            # A queued/new request cannot start another API call during shutdown.
            with pytest.raises(asyncio.CancelledError):
                await agent.process_message(AgentContext("new", "test", "test"))
            assert llm.calls == llm.expected_calls
        finally:
            primary.cancel()
            for task in manager._running.values():
                task.cancel()
            await asyncio.gather(primary, *manager._running.values(), return_exceptions=True)

    asyncio.run(run())


def test_repeated_shutdown_does_not_interrupt_cancellation_cleanup(monkeypatch, shutdown_state):
    async def run():
        agent = Agent(object(), MemorySessions())
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def work(context, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        monkeypatch.setattr(agent, "_process_message", work)
        task = asyncio.create_task(agent.process_message(AgentContext("main", "test", "test")))
        await started.wait()
        Agent.request_shutdown()
        await cleaning.wait()
        assert Agent.request_shutdown() == (task,)
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert not Agent._running_tasks

    asyncio.run(run())


def test_web_server_preserves_main_sigint_handler(monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    from miniclaw.channels.webchat_adapter import WebChatAdapter
    from miniclaw.channels.web import web_app

    received = []

    def main_handler(signum, frame):
        received.append(signum)

    async def serve(server, sockets=None):
        assert signal.getsignal(signal.SIGINT) is main_handler
        assert server.config.timeout_graceful_shutdown == 5.0
        signal.raise_signal(signal.SIGINT)

    monkeypatch.setattr(web_app, "create_web_app", lambda adapter: object())
    monkeypatch.setattr(uvicorn.Server, "_serve", serve)
    # Constructor services are irrelevant to the embedded server lifecycle.
    adapter = object.__new__(WebChatAdapter)
    adapter.host, adapter.port, adapter._uvicorn_server = "127.0.0.1", 0, None
    previous = signal.signal(signal.SIGINT, main_handler)
    try:
        asyncio.run(adapter.start())
        assert received == [signal.SIGINT]
        assert adapter._uvicorn_server is None
    finally:
        signal.signal(signal.SIGINT, previous)


def test_main_sigint_cancels_agent_runtimes_before_stopping_servers(monkeypatch, shutdown_state):
    import main as entry

    async def run():
        llm = BlockingLLM(2)
        sessions = MemorySessions()
        handlers, captured = {}, {}

        class Gateway:
            def __init__(self, agent):
                self.agent = agent
                self.stopped = asyncio.Event()

            async def start(self):
                primary = asyncio.create_task(self.agent.process_message(AgentContext("main", "test", "test")))
                captured["primary"] = primary
                captured["child_id"] = await captured["manager"].spawn("child", sandbox=False)
                await llm.started.wait()
                handlers[signal.SIGINT](signal.SIGINT, None)
                await self.stopped.wait()

            async def stop(self):
                assert Agent._shutdown_requested
                assert not Agent._running_tasks
                self.stopped.set()

        class Background:
            jobs = []

            def __init__(self, agent):
                pass

            async def run(self):
                await asyncio.Event().wait()

        monkeypatch.setattr(entry, "config", SimpleNamespace(
            llm=SimpleNamespace(fallback_providers=[]), paths=SimpleNamespace(sessions=None),
            multi_agent=SimpleNamespace(default_profile="coordinator"),
            channels=SimpleNamespace(enabled={"webchat": False, "telegram": False}),
            scheduler=SimpleNamespace(morning_summary_enabled=False),
            gateway=SimpleNamespace(host="127.0.0.1", port=0),
            webchat=SimpleNamespace(host="127.0.0.1", port=0),
            agent=SimpleNamespace(heartbeat_interval_min=1),
        ))
        monkeypatch.setattr(entry, "LLMRouter", lambda *args: llm)
        monkeypatch.setattr(entry, "AgentCatalog", lambda: shutdown_state)
        monkeypatch.setattr(entry, "SessionManager", lambda *args: sessions)
        monkeypatch.setattr(entry, "GatewayServer", Gateway)
        monkeypatch.setattr(entry, "HeartbeatLoop", Background)
        monkeypatch.setattr(entry, "CronScheduler", Background)
        monkeypatch.setattr(entry, "register_subagent_manager", lambda manager: captured.update(manager=manager))
        monkeypatch.setattr(entry.signal, "signal", lambda number, handler: handlers.update({number: handler}))
        await asyncio.wait_for(entry.main(), 3)
        assert captured["primary"].cancelled()
        assert captured["manager"]._running[captured["child_id"]].cancelled()
        assert llm.calls == 2

    asyncio.run(run())
