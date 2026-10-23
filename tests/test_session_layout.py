import asyncio
import json
from types import SimpleNamespace

import pytest

from miniclaw.agent import Agent, AgentContext
from miniclaw.agents import AgentCatalog
from miniclaw.memory.session import SessionManager
from miniclaw.memory.session_store import SessionStore
from miniclaw.runtime.request_preparer import RequestPreparer
from miniclaw.subagent import SubAgentManager


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    from miniclaw.memory import session_store

    monkeypatch.setattr(session_store, "WORKSPACE_DIR", tmp_path)
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    manager = SessionManager(tmp_path / "sessions")
    manager._store = store
    yield manager
    store.close()


def test_project_main_and_children_share_folder_and_reload(sessions):
    main = sessions.get_or_create("parent", "webchat")
    main.metadata["project_id"] = "project-a"
    child = sessions.get_or_create("child-chat", "subagent")
    child.metadata.update(project_id="project-a", parent_session_id=main.session_id,
                          root_session_id=main.session_id, task_id="task-1")

    async def run():
        await sessions.save(main)
        await sessions.save(child)
        root = sessions.save_dir / "project-a" / sessions._component(main.session_id)
        assert (root / "main.json").is_file()
        child_path = root / "subsessions" / "task-1.json"
        assert json.loads(child_path.read_text(encoding="utf-8"))["metadata"]["parent_session_id"] == main.session_id
        assert sessions._store.resolve_by_chat(child.chat_id)["metadata"]["project_id"] == "project-a"
        rebuilt = SessionStore(sessions.save_dir.parent / "rebuilt.db")
        try:
            rebuilt.open()
            assert rebuilt.resolve_by_chat(child.chat_id)["metadata"]["task_id"] == "task-1"
        finally:
            rebuilt.close()
        restored = SessionManager(sessions.save_dir)
        restored._store = sessions._store
        assert (await restored.load(child.session_id)).metadata["task_id"] == "task-1"
        assert (await restored.resolve_session(main.chat_id)).session_id == main.session_id
        await restored.delete_session(child.chat_id)
        assert not child_path.exists()
        assert (root / "main.json").exists()

    asyncio.run(run())


def test_flat_legacy_session_moves_on_save_and_remains_listable(sessions):
    original = sessions.get_or_create("old-chat", "webchat")
    legacy = sessions.save_dir / (original.session_id.replace(":", "_") + ".json")
    legacy.write_text(json.dumps(original.to_save_dict()), encoding="utf-8")
    sessions._sessions.clear()
    sessions._chat_to_session.clear()

    async def run():
        loaded = await sessions.resolve_session(original.chat_id)
        loaded.metadata["project_id"] = "project-a"
        await sessions.save(loaded)
        assert not legacy.exists()
        assert sessions._json_path(loaded).is_file()

        # Force JSON fallback rather than letting SQLite hide path traversal bugs.
        sessions._get_store = lambda: (_ for _ in ()).throw(RuntimeError("DB unavailable"))
        assert sessions.list_conversations_by_channel("webchat")[0]["chat_id"] == "old-chat"
        await sessions.delete_session("old-chat")
        assert not list(sessions.save_dir.rglob("*.json"))

    asyncio.run(run())


def test_global_folder_and_unsafe_ids_stay_inside_sessions(sessions):
    main = sessions.get_or_create("chat", "webchat")
    assert sessions._json_path(main).relative_to(sessions.save_dir).parts[0] == "_global"
    main.metadata.update(project_id="../../escape", root_session_id="CON", parent_session_id="parent", task_id="../task")
    path = sessions._json_path(main)
    assert path.resolve().is_relative_to(sessions.save_dir.resolve())
    assert ".." not in path.relative_to(sessions.save_dir).parts


def test_subagent_resolves_parent_chat_to_session_metadata(sessions, monkeypatch):
    parent = sessions.get_or_create("parent-chat", "webchat")
    parent.metadata.update(project_id="project-a", root_session_id=parent.session_id)
    template = Agent(object(), sessions)
    manager = SubAgentManager(template, sessions, catalog=AgentCatalog())
    seen = {}

    async def process(self, context, **kwargs):
        seen.update(context.metadata)
        return "done"

    monkeypatch.setattr(Agent, "_process_message", process)

    async def run():
        task_id = await manager.spawn("test", parent_session_id=parent.chat_id,
                                      agent_name="general-worker", sandbox=False)
        await manager.wait(task_id)
        assert seen["parent_session_id"] == parent.session_id
        assert seen["root_session_id"] == parent.session_id
        assert seen["project_id"] == "project-a"
        assert seen["task_id"] == task_id

    asyncio.run(run())


def test_request_preparer_persists_only_session_relationship_metadata(sessions, monkeypatch):
    from miniclaw.runtime import request_preparer

    async def fire(*args, **kwargs):
        return SimpleNamespace(prevent=False)

    monkeypatch.setattr(request_preparer.hook_system, "fire", fire)
    preparer = RequestPreparer(
        sessions=sessions, lifecycle=SimpleNamespace(start=lambda ctx: None),
        prompt_builder=SimpleNamespace(build_system_prompt=lambda: "", build_tool_definitions=lambda: []),
        memory_prefix=lambda _: "", experience_prefix=lambda *_: "",
    )
    context = AgentContext(chat_id="child", channel="subagent", account_id="system", metadata={
        "project_id": "project-a", "parent_session_id": "parent-id", "root_session_id": "root-id",
        "task_id": "task-2", "_callback": lambda: None,
    })
    prepared = asyncio.run(preparer.prepare(context))
    asyncio.run(sessions.save(prepared.session))
    path = sessions.save_dir / "project-a" / "root-id" / "subsessions" / "task-2.json"
    assert path.exists()
    assert "_callback" not in json.loads(path.read_text(encoding="utf-8"))["metadata"]
