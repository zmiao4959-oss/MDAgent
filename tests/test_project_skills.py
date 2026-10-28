"""Project Skill validation, scoping, permission checks and a real resume loop."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from miniclaw.agent import Agent, AgentContext
from miniclaw.agents import AgentCatalog, AgentProfile
from miniclaw.agents.graph import AgentGraph, AgentGraphRuntime, GraphNodeBinding, GraphRun
from miniclaw.llm.base import LLMResponse
from miniclaw.memory.session import SessionManager
from miniclaw.settings import config, workspace_scope
from miniclaw.skills.loader import SkillLoader
from miniclaw.skills.project import ProjectSkillStore
from miniclaw.tools import ensure_tools_loaded
from miniclaw.tools.registry import tool_registry
from miniclaw.tools.skill_tool import read_skill_tool


NAME = "count-project-phase-labels"
IMPLEMENTATION = '''import json
import sys
from collections import Counter

def fractions(labels):
    if not labels or any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("labels must contain nonempty strings")
    return {label: count / len(labels) for label, count in sorted(Counter(labels).items())}

if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as source:
        print(json.dumps(fractions(json.load(source))))
'''
TESTS = '''import unittest
from scripts.count_labels import fractions

class FractionsTest(unittest.TestCase):
    def test_known_counts(self):
        self.assertEqual(fractions(["fcc", "fcc", "bcc", "unknown"]), {"bcc": .25, "fcc": .5, "unknown": .25})
    def test_empty(self):
        with self.assertRaises(ValueError): fractions([])
    def test_bad_label(self):
        with self.assertRaises(ValueError): fractions([""])
'''


def profile(name="researcher", tools=("read_skill", "read", "execute")):
    return AgentProfile.from_mapping({"name": name, "description": "Count supplied phase labels; do not infer a new phase classifier", "tools": list(tools), "skills": []})


def package_files(*, version="1.0.0", code=True, required=None):
    files = {
        "SKILL.md": f"---\nname: {NAME}\ndescription: Count the fractions of supplied phase labels without classifying structures.\n---\nUse scripts/count_labels.py with the supplied JSON labels. Keep unknown labels in the denominator. Ratios are dimensionless. This does not classify new phases.\n",
        "skill.yaml": yaml.safe_dump({"schema_version": 1, "name": NAME, "version": version, "required_tools": required if required is not None else (["read_skill", "read", "execute"] if code else ["read_skill", "read"])}),
    }
    if code:
        files.update({"scripts/count_labels.py": IMPLEMENTATION, "tests/test_labels.py": TESTS})
    return files


def write_package(store, request, files):
    root = store.draft(request["request_id"])
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    ensure_tools_loaded()
    monkeypatch.setattr(config.evolution, "enabled", False)
    monkeypatch.setattr(Agent, "_memory_prefix", lambda *_: "")
    monkeypatch.setattr(Agent, "_experience_prefix", lambda *_: "")
    monkeypatch.setattr("miniclaw.agent.WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(SessionManager, "_get_store", lambda _: None)
    return ProjectSkillStore(tmp_path, "project:skills"), profile(), tmp_path


def request_skill(store, requester):
    return store.create_request(requester, "Count supplied structure labels", ["fcc=0.5, bcc=0.25, unknown=0.25 for known fixture", "Reject empty labels"])


def test_published_skill_is_pinned_scoped_and_readable_without_global_registration(setup):
    store, requester, tmp = setup
    request = request_skill(store, requester)
    write_package(store, request, package_files())
    tools_before = tool_registry.tool_names()
    receipt = asyncio.run(store.publish(request["request_id"], requester))
    assert receipt["validation"]["tests_run"] == 3
    assert receipt["validation"]["kind"] == "unittest_passed"
    mounted = store.mounted(requester)
    assert NAME in mounted
    assert SkillLoader().get(NAME) is None
    assert tool_registry.tool_names() == tools_before
    assert not requester.skills
    assert not store.mounted(profile("other"))
    assert not ProjectSkillStore(tmp / "other-project", "project:other").mounted(requester)
    assert NAME in ProjectSkillStore(tmp, "project:skills").mounted(requester)

    tool_context = {"agent_name": requester.name, "workspace_dir": str(tmp), "project_id": "project:skills", "allowed_skills": [NAME], "allowed_tools": list(requester.tools)}
    with workspace_scope(tmp):
        assert "Count the fractions" in read_skill_tool(NAME, _context=tool_context)
        assert "skill not found" in read_skill_tool(NAME, _context={**tool_context, "agent_name": "other"})
    (store.draft(request["request_id"]) / "SKILL.md").write_text("changed draft", encoding="utf-8")
    assert "Count the fractions" in mounted[NAME].location.read_text(encoding="utf-8")
    store.revoke(requester.name, NAME)
    assert not store.mounted(requester)
    assert mounted[NAME].location.exists()
    assert "skill not found" in read_skill_tool(NAME, _context=tool_context)


@pytest.mark.parametrize("case", ["missing-manifest", "tool-escalation", "undeclared-execute", "syntax", "missing-tests", "failing-tests", "skipped-tests", "zero-tests", "malformed-manifest", "collision", "invalid-frontmatter"])
def test_invalid_packages_do_not_mount_and_can_be_repaired(setup, case):
    store, requester, _ = setup
    request = request_skill(store, requester)
    files = package_files()
    if case == "missing-manifest":
        del files["skill.yaml"]
    elif case == "tool-escalation":
        files["skill.yaml"] = files["skill.yaml"].replace("- execute", "- submit_simulation")
    elif case == "undeclared-execute":
        files["skill.yaml"] = files["skill.yaml"].replace("- execute\n", "")
    elif case == "syntax":
        files["scripts/count_labels.py"] = "def broken(:"
    elif case == "missing-tests":
        del files["tests/test_labels.py"]
    elif case == "failing-tests":
        files["tests/test_labels.py"] = TESTS.replace('"fcc": .5', '"fcc": .75')
    elif case == "skipped-tests":
        files["tests/test_labels.py"] = 'import unittest\n@unittest.skip("not implemented")\nclass Test(unittest.TestCase):\n def test_skip(self): pass\n'
    elif case == "zero-tests":
        files["tests/test_labels.py"] = "# no actual tests\n"
    elif case == "malformed-manifest":
        files["skill.yaml"] = "name: ["
    elif case == "collision":
        files["SKILL.md"] = files["SKILL.md"].replace(NAME, "phase-transition-design")
    elif case == "invalid-frontmatter":
        files["SKILL.md"] = f"---\nname: [\n---\n<name>{NAME}</name><description>legacy fallback is not a valid new package</description>\n"
    write_package(store, request, files)
    with pytest.raises((ValueError, SyntaxError)):
        asyncio.run(store.publish(request["request_id"], requester))
    assert not store.mounted(requester)
    assert store.require_request(request["request_id"])["status"] == "validation_failed"
    assert store.can_write_draft(str(store.draft(request["request_id"]) / "SKILL.md"))


def test_script_skill_cannot_be_attached_to_agent_without_execute(setup):
    store, _, _ = setup
    reader = profile(tools=("read", "read_skill"))
    request = request_skill(store, reader)
    write_package(store, request, package_files())
    with pytest.raises(ValueError, match="权限"):
        asyncio.run(store.publish(request["request_id"], reader))
    assert not store.mounted(reader)


def test_instruction_skill_does_not_claim_behavior_tests(setup):
    store, _, _ = setup
    reader = profile(tools=("read", "read_skill"))
    request = request_skill(store, reader)
    write_package(store, request, package_files(code=False))
    receipt = asyncio.run(store.publish(request["request_id"], reader))
    assert receipt["validation"] == {"tests_run": 0, "kind": "structure_only"}
    assert NAME in store.mounted(reader)


def test_tampered_release_and_removed_permission_disable_mount(setup):
    store, requester, _ = setup
    request = request_skill(store, requester)
    write_package(store, request, package_files())
    receipt = asyncio.run(store.publish(request["request_id"], requester))
    assert not store.mounted(profile(tools=("read", "read_skill")))
    Path(receipt["location"]).write_text("tampered", encoding="utf-8")
    assert not store.mounted(requester)


def test_validation_timeout_leaves_no_binding(setup):
    store, requester, _ = setup
    request = request_skill(store, requester)
    files = package_files()
    files["tests/test_labels.py"] = "import time, unittest\nclass Test(unittest.TestCase):\n def test_wait(self): time.sleep(30)\n"
    write_package(store, request, files)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(store.publish(request["request_id"], requester, timeout=.2))
    assert not store.mounted(requester)
    assert store.require_request(request["request_id"])["status"] == "validation_failed"


def test_builder_draft_paths_cannot_escape_to_config_or_releases(setup):
    store, requester, tmp = setup
    request = request_skill(store, requester)
    assert store.can_write_draft(str(store.draft(request["request_id"]) / "scripts/count.py"))
    for path in ("agent.yaml", "../../main.py", str(store.index), str(store.root / "releases/a/file.py")):
        assert not store.can_write_draft(path)
    with pytest.raises(ValueError):
        store.require_request("../outside")
    assert not store.can_write_draft(str(store.draft(request["request_id"]) / ".." / "outside.py"))


def test_failed_upgrade_keeps_old_version_and_success_supersedes_it(setup):
    store, requester, _ = setup
    first = request_skill(store, requester)
    write_package(store, first, package_files())
    receipt = asyncio.run(store.publish(first["request_id"], requester))
    second = request_skill(store, requester)
    broken = package_files(version="1.0.1")
    broken["tests/test_labels.py"] = TESTS.replace('"fcc": .5', '"fcc": .75')
    write_package(store, second, broken)
    with pytest.raises(ValueError):
        asyncio.run(store.publish(second["request_id"], requester))
    assert str(store.mounted(requester)[NAME].location) == receipt["location"]
    write_package(store, second, package_files(version="1.0.1"))
    upgraded = asyncio.run(store.publish(second["request_id"], requester))
    assert upgraded["digest"] != receipt["digest"]
    assert store.require_request(first["request_id"])["status"] == "superseded"
    assert Path(receipt["location"]).exists()
    assert str(store.mounted(requester)[NAME].location) == upgraded["location"]


def test_restart_recovers_interrupted_validation(setup):
    store, requester, _ = setup
    request = request_skill(store, requester)
    index = json.loads(store.index.read_text(encoding="utf-8"))
    index["requests"][request["request_id"]].update(status="validating", validation_pid=-1)
    store.index.write_text(json.dumps(index), encoding="utf-8")
    assert store.require_request(request["request_id"])["status"] == "validation_failed"
    assert store.can_write_draft(str(store.draft(request["request_id"]) / "SKILL.md"))


def test_cancelled_validation_does_not_mount(setup, monkeypatch):
    store, requester, _ = setup
    request = request_skill(store, requester)
    write_package(store, request, package_files())
    started = asyncio.Event()

    async def blocked(*_):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(store, "_run_tests", blocked)

    async def run():
        task = asyncio.create_task(store.publish(request["request_id"], requester))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not store.mounted(requester)
    assert store.require_request(request["request_id"])["status"] == "validation_failed"


def test_builder_write_guard_is_enforced_by_shared_executor(setup):
    from miniclaw.runtime.tool_executor import ToolExecutor

    store, requester, tmp = setup
    request = request_skill(store, requester)
    builder = profile("skill-builder", tools=("read", "write", "edit", "read_skill"))
    executor = ToolExecutor(builder)
    ctx = AgentContext(chat_id="builder", channel="agent_graph", account_id="test", metadata={"project_id": "project:skills", "workspace_dir": str(tmp)})

    async def run():
        with workspace_scope(tmp):
            for i, path in enumerate((str(tmp / "agent.yaml"), str(store.index), str(store.root / "releases/file.py"))):
                result = await executor.execute_one({"id": str(i), "function": {"name": "write", "arguments": json.dumps({"path": path, "content": "forbidden"})}}, ctx)
                assert "only inside an open Skill request" in result.content
            path = store.draft(request["request_id"]) / "SKILL.md"
            result = await executor.execute_one({"id": "draft", "function": {"name": "write", "arguments": json.dumps({"path": str(path), "content": "draft"})}}, ctx)
            assert "Successfully wrote" in result.content
            assert path.read_text(encoding="utf-8") == "draft"

    asyncio.run(run())
    assert not (tmp / "agent.yaml").exists()


def test_symlinked_resources_cannot_be_published(setup):
    store, requester, tmp = setup
    request = request_skill(store, requester)
    write_package(store, request, package_files(code=False))
    outside = tmp / "outside.md"
    outside.write_text("external", encoding="utf-8")
    link = store.draft(request["request_id"]) / "references/link.md"
    link.parent.mkdir()
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable for this Windows account")
    with pytest.raises(ValueError, match="链接"):
        asyncio.run(store.publish(request["request_id"], requester))
    assert not store.mounted(requester)


def graph_bindings(setup, *, reverse=True, limit=4):
    store, requester, tmp = setup
    profiles = {requester.name: requester, "skill-builder": profile("skill-builder", tools=("read", "read_skill", "write", "edit"))}
    catalog = SimpleNamespace(require=lambda name: profiles[name])
    raw = {"nodes": [{"id": name} for name in profiles], "edges": [{"source": requester.name, "target": "skill-builder"}], "entry": requester.name, "max_messages": limit}
    if reverse:
        raw["edges"].append({"source": "skill-builder", "target": requester.name})
    graph = AgentGraph.parse(raw, catalog)
    sessions = SessionManager(tmp / "sessions")
    ctx = AgentContext(chat_id="webchat:binding", channel="webchat", account_id="test", metadata={"workspace_dir": str(tmp), "project_id": "project:skills"})
    session = sessions.get_or_create(ctx.chat_id, ctx.channel, ctx.account_id)
    run = GraphRun(SimpleNamespace(catalog=catalog, sessions=sessions), graph, ctx, session)
    return run, GraphNodeBinding(run, requester.name), GraphNodeBinding(run, "skill-builder")


def test_request_requires_bidirectional_builder_edge(setup):
    store, _, _ = setup
    _, requester, _ = graph_bindings(setup, reverse=False)
    definitions = {tool["function"]["name"] for tool in requester.tool_definitions()}
    assert "request_skill" not in definitions
    result = asyncio.run(requester.execute_tool("request_skill", {"objective": "missing capability", "acceptance": ["known result"]}))
    assert result.startswith("Error:")
    assert not store.requests()


def test_budget_is_checked_before_skill_activation(setup):
    store, owner, _ = setup
    request = request_skill(store, owner)
    write_package(store, request, package_files())
    run, requester, builder = graph_bindings(setup, limit=1)
    request_result = asyncio.run(requester.execute_tool("request_skill", {"objective": "another capability", "acceptance": ["known result"]}))
    assert "预算不足" in request_result
    run.accepted = 1
    result = asyncio.run(builder.execute_tool("publish_skill", {"request_id": request["request_id"]}))
    assert "消息额度" in result
    assert not store.mounted(owner)
    assert store.require_request(request["request_id"])["status"] == "requested"


def test_only_builder_can_publish_and_repeated_publication_does_not_resume_twice(setup):
    store, owner, _ = setup
    request = request_skill(store, owner)
    write_package(store, request, package_files())
    run, requester, builder = graph_bindings(setup)

    async def execute():
        forbidden = await requester.execute_tool("publish_skill", {"request_id": request["request_id"]})
        assert forbidden.startswith("Error:")
        first = json.loads(await builder.execute_tool("publish_skill", {"request_id": request["request_id"]}))
        second = json.loads(await builder.execute_tool("publish_skill", {"request_id": request["request_id"]}))
        assert first["status"] == second["status"] == "mounted"
        assert second["reused"]
        assert run.accepted == 1
        assert run.reserved_messages == 0
        assert len(run.queue) == 2  # initial user message plus exactly one resume

    asyncio.run(execute())


def test_requester_without_read_skill_cannot_request_a_mount(setup):
    store, _, _ = setup
    with pytest.raises(ValueError, match="read_skill"):
        request_skill(store, profile(tools=("read",)))


def test_project_skills_are_visible_and_revocable_through_http(setup):
    import httpx
    from miniclaw.channels.web.web_app import create_web_app
    from miniclaw.channels.webchat_adapter import WebChatAdapter

    _, owner, tmp = setup
    sessions = SessionManager(tmp / "sessions")
    runtime = AgentGraphRuntime(Agent(object(), sessions, profile=owner), AgentCatalog())
    adapter = WebChatAdapter(session_manager=sessions, agent_graph_runtime=runtime)
    session = sessions.get_or_create("webchat:skills-api", "webchat", "test")
    project = adapter.projects.create("Skills API", "test", session.chat_id, str(tmp))
    store = ProjectSkillStore(tmp, project.project_id)
    request = request_skill(store, owner)
    write_package(store, request, package_files(code=False))

    async def run():
        await sessions.save(session)
        await store.publish(request["request_id"], owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(adapter)), base_url="http://test") as client:
            path = f"/api/conversations/{session.chat_id}"
            data = (await client.get(path + "/agent-graph")).json()
            assert data["project_skills"]["bindings"][owner.name][NAME]["available"]
            delete_path = path + f"/project-skills/{owner.name}/{NAME}"
            runtime._active_chats.add(session.chat_id)
            assert (await client.delete(delete_path)).status_code == 409
            runtime._active_chats.clear()
            assert (await client.delete(delete_path)).json()["status"] == "revoked"
            data = (await client.get(path + "/agent-graph")).json()
            assert not data["project_skills"]["bindings"][owner.name]

    asyncio.run(run())


def tool(tool_name, **arguments):
    return LLMResponse(tool_calls=[{"id": f"call-{tool_name}", "type": "function", "function": {"name": tool_name, "arguments": json.dumps(arguments)}}], finish_reason="tool_calls")


def test_graph_builds_tests_mounts_and_resumes_requester_with_real_script_execution(setup):
    store, _, tmp = setup
    root = tmp / "profiles"
    for name, tools in [("researcher", ["read_skill", "read", "execute"]), ("skill-builder", ["read_skill", "read", "list", "write", "edit"])]:
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "agent.yaml").write_text(yaml.safe_dump({"name": name, "description": "Count supplied phase labels" if name == "researcher" else "Build project Skills", "tools": tools, "skills": [], "runtime": {"max_tool_rounds": 8}}), encoding="utf-8")
    originals = {p: p.read_bytes() for p in root.rglob("agent.yaml")}
    (tmp / "labels.json").write_text(json.dumps(["fcc", "fcc", "bcc", "unknown"]), encoding="utf-8")

    class LLM:
        def __init__(self):
            self.counts = {}
            self.request = None
            self.execution = None

        async def chat(self, messages, tools=None, **kwargs):
            system = messages[0].content
            node = system.split("Your node: ")[1].split(".")[0]
            index = self.counts.get(node, 0)
            self.counts[node] = index + 1
            names = {item["function"]["name"] for item in tools}
            if node == "researcher":
                if index == 0:
                    assert "request_skill" in names
                    return tool("request_skill", objective="Count supplied structure labels", acceptance=["Known counts: fcc=.5, bcc=.25, unknown=.25", "Reject empty labels"])
                if index == 1:
                    self.request = json.loads(messages[-1].content)
                    return LLMResponse(content="Skill requested; waiting for the supplied implementation")
                if index == 2:
                    assert NAME in system
                    assert "Temporary project Skills" in system
                    assert "revoke_skill" in names
                    return tool("read_skill", name=NAME)
                if index == 3:
                    content = messages[-1].content
                    assert "Count the fractions" in content
                    skill_file = Path(content.split("File: ")[1].splitlines()[0])
                    return tool("execute", command=f'python -m scripts.count_labels "{tmp / "labels.json"}"', working_dir=str(skill_file.parent))
                self.execution = messages[-1].content
                assert '"fcc": 0.5' in self.execution
                return LLMResponse(content="Verified fcc=.5, bcc=.25, unknown=.25")
            if index == 0:
                return tool("list_skill_requests", request_id=self.request["request_id"])
            if index == 1:
                contract = json.loads(messages[-1].content)
                assert contract["requester"] == "researcher"
                assert "execute" in contract["allowed_tools"]
                return LLMResponse(tool_calls=[{"id": f"write-{i}", "type": "function", "function": {"name": "write", "arguments": json.dumps({"path": str(Path(contract["draft_dir"]) / relative), "content": data})}} for i, (relative, data) in enumerate(package_files().items())], finish_reason="tool_calls")
            if index == 2:
                return tool("publish_skill", request_id=self.request["request_id"])
            receipt = json.loads(messages[-1].content)
            assert receipt["status"] == "mounted"
            assert receipt["validation"]["tests_run"] == 3
            return LLMResponse(content="Skill package published")

    catalog = AgentCatalog([root])
    llm = LLM()
    runtime = AgentGraphRuntime(Agent(llm, SessionManager(tmp / "sessions"), profile=catalog.require("researcher")), catalog)
    graph = {"version": 1, "nodes": [{"id": "researcher"}, {"id": "skill-builder"}], "edges": [{"source": "researcher", "target": "skill-builder"}, {"source": "skill-builder", "target": "researcher"}], "entry": "researcher", "max_messages": 4}
    ctx = AgentContext(chat_id="webchat:skill-loop", channel="webchat", account_id="test", user_message="Summarize the supplied phase labels", metadata={"project_id": "project:skills", "workspace_dir": str(tmp), "agent_graph": graph})
    tools_before = tool_registry.tool_names()
    result = asyncio.run(runtime.process_message(ctx))
    assert "Verified fcc=.5" in result
    assert llm.execution
    assert tool_registry.tool_names() == tools_before
    assert {p: p.read_bytes() for p in originals} == originals
    assert NAME in store.mounted(catalog.require("researcher"))
    record = runtime.sessions.get_session_by_chat_id(ctx.chat_id).metadata["last_graph_run"]
    assert [item["node"] for item in record["outputs"]] == ["researcher", "skill-builder", "researcher"]
    assert [event["kind"] for event in record["events"]].count("skill_mounted") == 1
