from miniclaw.projects import ProjectManager
from miniclaw.channels.webchat import WebChatAdapter
from miniclaw.memory.session import SessionManager
from miniclaw.config import workspace_scope
from miniclaw.tools.paths import resolve_workspace_path, safe_relpath


def test_projects_persist_and_support_lifecycle(tmp_path):
    store = ProjectManager(tmp_path / "projects.json")
    project = store.create("Research", "Compare two approaches", "webchat:research")

    store.update(project.project_id, status="paused", last_run=True)
    restored = ProjectManager(tmp_path / "projects.json").get(project.project_id)

    assert restored is not None
    assert restored.title == "Research"
    assert restored.status == "paused"
    assert restored.last_run_at > 0

    store.add_event(project.project_id, "paused", "Project paused")
    assert ProjectManager(tmp_path / "projects.json").get(project.project_id).timeline[-1]["kind"] == "paused"


def test_bootstrap_keeps_existing_conversations(tmp_path):
    store = ProjectManager(tmp_path / "projects.json")
    store.bootstrap([
        {"chat_id": "webchat:one", "alias": "One", "last_active": 10},
        {"chat_id": "webchat:two", "preview": "Second", "last_active": 20},
    ])

    assert [project.title for project in store.list()] == ["Second", "One"]


def test_webchat_creates_a_first_project_for_a_fresh_workspace(tmp_path):
    sessions = SessionManager(tmp_path / "workspace" / "sessions")
    adapter = WebChatAdapter(session_manager=sessions)

    import asyncio
    asyncio.run(adapter._ensure_projects())

    project = adapter.projects.list()[0]
    assert project.title
    assert project.chat_id.startswith("webchat:")


def test_project_workspace_scope_keeps_file_paths_inside_project(tmp_path):
    project_workspace = tmp_path / "project-workspace"
    with workspace_scope(project_workspace):
        path, error = resolve_workspace_path("results/output.txt", allow_create=True)

    assert error is None
    assert path == (project_workspace / "results" / "output.txt").resolve()


def test_project_artifacts_are_scoped_and_sorted(tmp_path):
    sessions = SessionManager(tmp_path / "workspace" / "sessions")
    adapter = WebChatAdapter(session_manager=sessions)
    workspace = tmp_path / "project-workspace"
    workspace.mkdir()
    (workspace / "older.txt").write_text("old", encoding="utf-8")
    (workspace / "result.log").write_text("new", encoding="utf-8")
    project = adapter.projects.create("Artifacts", "", "webchat:artifacts", str(workspace))

    artifacts = adapter._project_artifacts(project)

    assert {artifact["name"] for artifact in artifacts} == {"result.log", "older.txt"}


def test_resolve_workspace_path_allows_extra_read_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_root = tmp_path / "skills"
    skill_root.mkdir()
    skill_file = skill_root / "demo" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text("demo", encoding="utf-8")

    path, error = resolve_workspace_path(
        str(skill_file),
        must_exist=True,
        extra_roots=[skill_root],
    )

    assert error is None
    assert path == skill_file.resolve()
    assert safe_relpath(path) == str(path.resolve()).replace("\\", "/")
