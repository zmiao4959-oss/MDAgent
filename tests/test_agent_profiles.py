import asyncio
from pathlib import Path

from miniclaw.agent import Agent
from miniclaw.agents import AgentCatalog, AgentProfile
from miniclaw.config import active_workspace_dir
from miniclaw.memory.session import SessionManager
from miniclaw.subagent import SubAgentManager
from miniclaw.tools.registry import tool_registry
from miniclaw.tools.skill_tool import read_skill_tool
from miniclaw.tools.delegation_tool import (
    delegate_task_tool,
    list_tasks_tool,
    register_subagent_manager,
)


class _UnusedLLM:
    pass


def _write_profile(
    root: Path,
    name: str,
    *,
    description: str = "test agent",
    tools: str = "[]",
    skills: str = "[]",
) -> None:
    profile_dir = root / name
    profile_dir.mkdir(parents=True)
    (profile_dir / "SYSTEM.md").write_text(f"# {name}\n", encoding="utf-8")
    (profile_dir / "agent.yaml").write_text(
        (
            f"name: {name}\n"
            f"description: {description}\n"
            "prompt: SYSTEM.md\n"
            f"tools: {tools}\n"
            f"skills: {skills}\n"
        ),
        encoding="utf-8",
    )


def test_catalog_loads_profiles_and_later_root_overrides(tmp_path: Path):
    builtins = tmp_path / "builtins"
    custom = tmp_path / "custom"
    _write_profile(builtins, "worker", description="built in")
    _write_profile(custom, "worker", description="custom")

    catalog = AgentCatalog([builtins, custom])

    assert catalog.require("worker").description == "custom"
    assert catalog.require("worker").system_prompt == "# worker"


def test_agent_filters_visible_tools_and_enforces_execution(tmp_path: Path):
    profile = AgentProfile.from_mapping(
        {
            "name": "reader",
            "description": "read only",
            "tools": ["read"],
            "skills": [],
        }
    )
    agent = Agent(_UnusedLLM(), SessionManager(tmp_path / "sessions"), profile=profile)

    definitions = agent._build_tool_definitions()
    names = {item["function"]["name"] for item in definitions}

    assert names == {"read"}
    result = asyncio.run(
        tool_registry.execute(
            "write",
            {"path": "blocked.txt", "content": "no"},
            {"allowed_tools": ["read"]},
        )
    )
    assert "not allowed" in result


def test_read_skill_enforces_profile_allowlist(tmp_path: Path, monkeypatch):
    from miniclaw.skills import loader as loader_module
    from miniclaw.tools import paths as paths_module

    skills_root = tmp_path / "skills"
    for name in ("allowed", "blocked"):
        path = skills_root / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(loader_module, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(paths_module, "WORKSPACE_DIR", tmp_path)

    result = read_skill_tool(
        "blocked",
        _context={"allowed_skills": ["allowed"]},
    )
    assert "not allowed" in result


def test_subagent_uses_independent_profiled_agent_and_real_sandbox(
    tmp_path: Path,
    monkeypatch,
):
    profiles = tmp_path / "profiles"
    _write_profile(profiles, "worker", tools='["read"]', skills="[]")
    catalog = AgentCatalog([profiles])
    template = Agent(_UnusedLLM(), SessionManager(tmp_path / "sessions"))
    manager = SubAgentManager(template, template.sessions, catalog=catalog)
    seen = {}
    events = []

    async def fake_process(self, context, **kwargs):
        seen["same_instance"] = self is template
        seen["profile"] = self.profile.name
        seen["workspace"] = active_workspace_dir()
        return "completed"

    monkeypatch.setattr(Agent, "_process_message", fake_process)

    async def run():
        async def on_event(event):
            events.append(dict(event))

        task_id = await manager.spawn(
            "do work",
            agent_name="worker",
            sandbox=True,
            event_callback=on_event,
        )
        result = await manager.wait(task_id)
        return manager.get(task_id), result

    task, result = asyncio.run(run())

    assert result == "completed"
    assert seen["same_instance"] is False
    assert seen["profile"] == "worker"
    assert seen["workspace"] == Path(task.sandbox_dir)
    assert [event["status"] for event in events] == ["pending", "running", "done"]


def test_coordinator_exposes_delegation_but_not_execution_tools(tmp_path: Path):
    profile = AgentCatalog().require("coordinator")
    agent = Agent(_UnusedLLM(), SessionManager(tmp_path / "sessions"), profile=profile)

    definitions = agent._build_tool_definitions()
    names = {definition["function"]["name"] for definition in definitions}

    assert {
        "list_agents",
        "list_tasks",
        "delegate_task",
        "inspect_task",
        "wait_task",
        "cancel_task",
    } <= names
    assert "execute" not in names
    assert "write" not in names
    assert "opaque identifier" in profile.system_prompt
    assert "call `list_tasks`" in profile.system_prompt
    assert "Never call `delegate_task` again" in profile.system_prompt

    by_name = {
        definition["function"]["name"]: definition["function"]
        for definition in definitions
    }
    for tool_name in ("inspect_task", "wait_task", "cancel_task"):
        description = by_name[tool_name]["parameters"]["properties"]["task_id"][
            "description"
        ]
        assert "Copy it verbatim" in description
        assert "subagent:" in description


def test_coordinator_delegation_tool_runs_subagents_in_parallel(
    tmp_path: Path,
    monkeypatch,
):
    profiles = tmp_path / "profiles"
    _write_profile(profiles, "worker", tools='["read"]', skills="[]")
    catalog = AgentCatalog([profiles])
    template = Agent(_UnusedLLM(), SessionManager(tmp_path / "sessions"))
    manager = SubAgentManager(template, template.sessions, catalog=catalog)
    register_subagent_manager(manager)
    started = []
    events = []

    async def run_slow(self, context, **kwargs):
        started.append(context.chat_id)
        while len(started) < 2:
            await asyncio.sleep(0)
        return f"done:{context.user_message}"

    async def on_event(event):
        events.append(dict(event))

    monkeypatch.setattr(Agent, "_process_message", run_slow)

    async def run():
        context = {"chat_id": "parent", "task_event_callback": on_event}
        first, second = await asyncio.gather(
            delegate_task_tool(
                "worker", "first", _context=context, sandbox=False
            ),
            delegate_task_tool(
                "worker", "second", _context=context, sandbox=False
            ),
        )
        results = await asyncio.gather(
            manager.wait(first["task_id"]),
            manager.wait(second["task_id"]),
        )
        return results

    results = asyncio.run(run())

    assert sorted(results) == ["done:first", "done:second"]
    assert len({event["task_id"] for event in events}) == 2
    assert sum(event["status"] == "running" for event in events) == 2


def test_equivalent_delegations_reuse_task_unless_forced(
    tmp_path: Path,
    monkeypatch,
):
    profiles = tmp_path / "profiles"
    _write_profile(profiles, "worker", tools='["read"]', skills="[]")
    catalog = AgentCatalog([profiles])
    template = Agent(_UnusedLLM(), SessionManager(tmp_path / "sessions"))
    manager = SubAgentManager(template, template.sessions, catalog=catalog)
    register_subagent_manager(manager)
    calls = 0

    async def process(self, context, **kwargs):
        nonlocal calls
        calls += 1
        return "done"

    monkeypatch.setattr(Agent, "_process_message", process)

    async def run():
        context = {"chat_id": "parent"}
        first = await delegate_task_tool(
            "worker",
            "same objective",
            _context=context,
            sandbox=False,
        )
        duplicate = await delegate_task_tool(
            "worker",
            "same   objective",
            _context=context,
            sandbox=False,
        )
        forced = await delegate_task_tool(
            "worker",
            "same objective",
            _context=context,
            sandbox=False,
            force_new=True,
        )
        await asyncio.gather(
            manager.wait(first["task_id"]),
            manager.wait(forced["task_id"]),
        )
        return first, duplicate, forced

    first, duplicate, forced = asyncio.run(run())

    assert first["reused"] is False
    assert duplicate == {
        **first,
        "reused": True,
    }
    assert duplicate["task_id"].startswith("subagent:")
    assert forced["task_id"] != first["task_id"]
    assert forced["reused"] is False
    assert calls == 2


def test_list_tasks_is_scoped_to_parent_and_supports_reconciliation(
    tmp_path: Path,
    monkeypatch,
):
    profiles = tmp_path / "profiles"
    _write_profile(profiles, "worker", tools='["read"]', skills="[]")
    catalog = AgentCatalog([profiles])
    template = Agent(_UnusedLLM(), SessionManager(tmp_path / "sessions"))
    manager = SubAgentManager(template, template.sessions, catalog=catalog)
    register_subagent_manager(manager)

    async def process(self, context, **kwargs):
        return "done"

    monkeypatch.setattr(Agent, "_process_message", process)

    async def run():
        first = await delegate_task_tool(
            "worker",
            "analyze alpha",
            idempotency_key="alpha-key",
            _context={"chat_id": "parent-one"},
            sandbox=False,
        )
        await delegate_task_tool(
            "worker",
            "analyze beta",
            idempotency_key="beta-key",
            _context={"chat_id": "parent-two"},
            sandbox=False,
        )
        scoped = list_tasks_tool(_context={"chat_id": "parent-one"})
        matched = list_tasks_tool(
            query="alpha-key",
            _context={"chat_id": "parent-one"},
        )
        missing = list_tasks_tool(
            query="beta",
            _context={"chat_id": "parent-one"},
        )
        await asyncio.gather(
            *(manager.wait(task.task_id) for task in manager.list_all())
        )
        return first, scoped, matched, missing

    first, scoped, matched, missing = asyncio.run(run())

    assert scoped["count"] == 1
    assert scoped["tasks"][0]["task_id"] == first["task_id"]
    assert scoped["tasks"][0]["idempotency_key"] == "alpha-key"
    assert matched["tasks"][0]["objective"] == "analyze alpha"
    assert missing["tasks"] == []
