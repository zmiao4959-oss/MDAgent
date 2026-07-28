import asyncio
import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi import HTTPException

from miniclaw.channels.web.capsule_routes import register_capsule_routes
from miniclaw.channels.webchat import WebChatAdapter
from miniclaw.config import WORKSPACE_DIR
from miniclaw.learning.trace import EvolutionTrace, TraceStore
from miniclaw.llm.base import LLMMessage
from miniclaw.memory.session import Session, SessionManager
from miniclaw.research_capsule import CapsuleIntegrityError, ResearchCapsule, ResearchCapsuleManager


def test_capsule_capture_is_sorted_integrity_addressed_and_redacted(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "inputs" / "run.in").write_text("run 1000\n", encoding="utf-8")
    (workspace / "result.txt").write_text("42\n", encoding="utf-8")
    (workspace / ".env").write_text("API_KEY=private", encoding="utf-8")
    (workspace / ".miniclaw").mkdir()
    (workspace / ".miniclaw" / "runtime.json").write_text("{}", encoding="utf-8")

    session = Session(session_id="session-one", chat_id="webchat:one")
    session.add_message(LLMMessage(role="user", content="run the experiment"))
    session.add_message(LLMMessage(role="assistant", content="finished"))

    trace = EvolutionTrace(chat_id="webchat:one", objective="demo", run_id="run-one")
    trace.add_tool(
        "execute",
        {
            "command": "gpumd API_KEY=command-secret Bearer abcdef123",
            "api_key": "secret",
            "nested": {"password": "hidden"},
        },
        "ok",
    )
    trace.finish(success=True, final_response="done", rounds=2, prompt_tokens=10, completion_tokens=5)

    manager = ResearchCapsuleManager(
        workspace,
        source_root=tmp_path / "not-a-repository",
        clock=lambda: 1234.5,
        id_factory=lambda: "fixed",
    )
    capsule = manager.capture(
        project_id="project:one",
        title="Demo",
        objective="Reproduce a small run TOKEN=objective-secret",
        chat_id="webchat:one",
        session=session,
        trace=trace,
    )

    assert [item.path for item in capsule.files] == ["inputs/run.in", "result.txt"]
    assert capsule.files[1].sha256 == hashlib.sha256(
        (workspace / "result.txt").read_bytes()
    ).hexdigest()
    assert capsule.conversation["roles"] == {"assistant": 1, "user": 1}
    assert capsule.objective == "Reproduce a small run TOKEN=[REDACTED]"
    assert capsule.trace["tool_calls"][0]["arguments"]["api_key"] == "[REDACTED]"
    assert capsule.trace["tool_calls"][0]["arguments"]["nested"]["password"] == "[REDACTED]"
    assert capsule.trace["tool_calls"][0]["arguments"]["command"] == (
        "gpumd API_KEY=[REDACTED] Bearer [REDACTED]"
    )
    assert {item["path"] for item in capsule.skipped_files} == {".env"}

    manifest_path = workspace / ".miniclaw" / "research-capsules" / "capsule_fixed" / "capsule.json"
    readme_path = manifest_path.with_name("README.md")
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "secret" not in manifest_text
    assert json.loads(manifest_text)["manifest_sha256"] == capsule.manifest_sha256
    assert "Research Capsule: Demo" in readme_path.read_text(encoding="utf-8")

    restored = manager.get_capsule("capsule:fixed")
    assert restored is not None
    assert restored.manifest_sha256 == capsule.manifest_sha256
    assert [item.capsule_id for item in manager.list_capsules()] == ["capsule:fixed"]


def test_capsule_manifest_digest_is_stable_for_unchanged_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    first = ResearchCapsuleManager(
        workspace,
        source_root=tmp_path,
        id_factory=lambda: "first",
    ).capture(
        project_id="project:stable",
        title="Stable",
        objective="",
        chat_id="webchat:stable",
    )
    second = ResearchCapsuleManager(
        workspace,
        source_root=tmp_path,
        id_factory=lambda: "second",
    ).capture(
        project_id="project:stable",
        title="Stable",
        objective="",
        chat_id="webchat:stable",
    )

    assert first.manifest_sha256 == second.manifest_sha256

    legacy = first.to_dict()
    for field in ("schema_version", "status", "file_count", "total_bytes"):
        legacy.pop(field)
    restored = ResearchCapsule.from_dict(legacy)
    assert restored.schema_version == 1
    assert restored.status == "captured"


def test_capsule_verification_reports_workspace_drift(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = b"temperature=300\n"
    (workspace / "input.txt").write_bytes(original)
    manager = ResearchCapsuleManager(workspace, source_root=tmp_path, id_factory=lambda: "verify")
    capsule = manager.capture(
        project_id="project:verify",
        title="Verify",
        objective="",
        chat_id="webchat:verify",
    )

    report = manager.verify(capsule.capsule_id)
    assert report.status == "verified"
    assert report.unchanged == ["input.txt"]
    assert manager.get_verification(capsule.capsule_id).status == "verified"

    (workspace / "input.txt").write_text("temperature=301\n", encoding="utf-8")
    changed = manager.verify(capsule.capsule_id)
    assert changed.status == "changed"
    assert changed.changed[0]["path"] == "input.txt"

    (workspace / "input.txt").write_bytes(original)
    (workspace / "new-result.csv").write_text("step,value\n1,42\n", encoding="utf-8")
    added = manager.verify(capsule.capsule_id)
    assert added.status == "changed"
    assert added.added[0]["path"] == "new-result.csv"

    (workspace / "new-result.csv").unlink()
    (workspace / "input.txt").unlink()
    missing = manager.verify(capsule.capsule_id)
    assert missing.status == "missing"
    assert missing.missing == ["input.txt"]


def test_capsule_export_requires_exact_confirmation_and_redacts_text(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source_text = "endpoint=https://example.test\nAPI_KEY=export-secret\n"
    (workspace / "settings.txt").write_text(source_text, encoding="utf-8")
    manager = ResearchCapsuleManager(workspace, source_root=tmp_path, id_factory=lambda: "export")
    capsule = manager.capture(
        project_id="project:export",
        title="Export",
        objective="",
        chat_id="webchat:export",
    )

    with pytest.raises(ValueError, match="confirmation must exactly match"):
        manager.export_capsule(capsule.capsule_id, confirmation="EXPORT capsule:wrong")

    exported = manager.export_capsule(
        capsule.capsule_id,
        confirmation=f"EXPORT {capsule.capsule_id}",
    )
    assert exported.redacted_file_count == 1
    with zipfile.ZipFile(exported.path) as archive:
        assert {
            "capsule.json", "README.md", "verification.json", "export.json",
            "files/settings.txt",
        } <= set(archive.namelist())
        redacted = archive.read("files/settings.txt").decode("utf-8")
        assert "export-secret" not in redacted
        assert "API_KEY=[REDACTED]" in redacted
        export_manifest = json.loads(archive.read("export.json"))
        assert export_manifest["files"][0]["redacted"] is True
        assert export_manifest["files"][0]["source_sha256"] != export_manifest["files"][0]["export_sha256"]

    (workspace / "settings.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(CapsuleIntegrityError) as integrity_error:
        manager.export_capsule(
            capsule.capsule_id,
            confirmation=f"EXPORT {capsule.capsule_id}",
        )
    assert integrity_error.value.report.status == "changed"


def test_capsule_manifest_only_export_does_not_include_workspace_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.bin").write_bytes(b"data")
    manager = ResearchCapsuleManager(workspace, source_root=tmp_path, id_factory=lambda: "manifest")
    capsule = manager.capture(
        project_id="project:manifest",
        title="Manifest",
        objective="",
        chat_id="webchat:manifest",
    )

    exported = manager.export_capsule(
        capsule.capsule_id,
        confirmation="",
        include_files=False,
    )
    with zipfile.ZipFile(exported.path) as archive:
        assert not any(name.startswith("files/") for name in archive.namelist())
        assert json.loads(archive.read("export.json"))["includes_files"] is False

    (workspace / "large.bin").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    large_manager = ResearchCapsuleManager(
        workspace, source_root=tmp_path, id_factory=lambda: "actually-large"
    )
    large_capsule = large_manager.capture(
        project_id="project:large",
        title="Large",
        objective="",
        chat_id="webchat:large",
    )
    with pytest.raises(ValueError, match="use manifest-only export"):
        large_manager.export_capsule(
            large_capsule.capsule_id,
            confirmation=f"EXPORT {large_capsule.capsule_id}",
        )


def test_project_capsule_routes_capture_session_trace_and_timeline(tmp_path):
    sessions = SessionManager(tmp_path / "global" / "sessions")
    adapter = WebChatAdapter(session_manager=sessions)
    workspace = tmp_path / "project-workspace"
    workspace.mkdir()
    (workspace / "result.log").write_text("complete", encoding="utf-8")
    project = adapter.projects.create(
        "Capsule API",
        "Capture the result",
        "webchat:capsule-api",
        str(workspace),
    )

    session = sessions.get_or_create(project.chat_id, "webchat", "local")
    session.add_message(LLMMessage(role="user", content="capture this"))
    trace = EvolutionTrace(chat_id=project.chat_id, objective=project.objective, run_id="api-run")
    trace.finish(success=True, final_response="done")
    TraceStore(workspace / ".miniclaw" / "evolution" / "traces.jsonl").append(trace)

    app = FastAPI()
    register_capsule_routes(app, adapter)

    def endpoint(path: str, method: str):
        return next(
            route.endpoint for route in app.routes
            if getattr(route, "path", "") == path and method in getattr(route, "methods", set())
        )

    class RunningTask:
        @staticmethod
        def done():
            return False

    adapter._project_runs[project.project_id] = RunningTask()
    with pytest.raises(HTTPException, match="project is still running") as running_error:
        asyncio.run(endpoint("/api/projects/{project_id}/capsules", "POST")(project.project_id))
    assert running_error.value.status_code == 409
    adapter._project_runs.pop(project.project_id)

    shared_project = adapter.projects.create(
        "Shared workspace",
        "Must be isolated",
        "webchat:shared",
        str(WORKSPACE_DIR.resolve()),
    )
    with pytest.raises(HTTPException, match="isolated project workspace") as scope_error:
        asyncio.run(endpoint("/api/projects/{project_id}/capsules", "GET")(shared_project.project_id))
    assert scope_error.value.status_code == 409

    created = asyncio.run(endpoint("/api/projects/{project_id}/capsules", "POST")(project.project_id))
    capsule_id = created["capsule"]["capsule_id"]
    assert created["capsule"]["trace"]["run_id"] == "api-run"
    assert created["capsule"]["conversation"]["message_count"] == 1
    assert adapter.projects.get(project.project_id).timeline[-1]["kind"] == "capsule_created"

    listed = asyncio.run(endpoint("/api/projects/{project_id}/capsules", "GET")(project.project_id))
    assert [item["capsule_id"] for item in listed["capsules"]] == [capsule_id]

    verified = asyncio.run(
        endpoint("/api/projects/{project_id}/capsules/{capsule_id}/verify", "POST")(
            project.project_id, capsule_id
        )
    )
    assert verified["verification"]["status"] == "verified"
    listed = asyncio.run(endpoint("/api/projects/{project_id}/capsules", "GET")(project.project_id))
    assert listed["capsules"][0]["verification"]["status"] == "verified"

    detail = asyncio.run(
        endpoint("/api/projects/{project_id}/capsules/{capsule_id}", "GET")(
            project.project_id, capsule_id
        )
    )
    assert detail["capsule"]["files"][0]["path"] == "result.log"


def test_research_capsule_web_panel_is_wired():
    index = Path(__file__).resolve().parents[1] / "miniclaw" / "channels" / "web" / "static" / "index.html"
    app = index.with_name("app.js")
    capsule_module = index.with_name("capsules.js")
    html = index.read_text(encoding="utf-8")
    javascript = app.read_text(encoding="utf-8")
    capsules = capsule_module.read_text(encoding="utf-8")

    assert 'id="tab-capsules"' in html
    assert 'id="create-capsule"' in html
    assert "createResearchCapsulePanel" in javascript
    assert "async function refresh()" in capsules
    assert "async function verifyCapsule(capsuleId)" in capsules
    assert "async function exportCapsule(capsuleId, includeFiles = true)" in capsules
    assert "EXPORT ${capsuleId}" in capsules
