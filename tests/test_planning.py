import asyncio

from miniclaw.planning import (
    plan_add_tasks,
    register_plan_project_manager,
    unregister_plan_project_manager,
)
from miniclaw.projects import ProjectManager


def test_plan_add_tasks_prefers_request_context_project_id(tmp_path):
    manager = ProjectManager(tmp_path / "projects.json")
    project = manager.create("Planning", "objective", "webchat:test")
    register_plan_project_manager(manager)
    try:
        result = asyncio.run(
            plan_add_tasks(
                tasks=[{"title": "First task"}],
                project_id="project_stale123",
                _context={"project_id": project.project_id},
            )
        )
    finally:
        unregister_plan_project_manager()

    restored = ProjectManager(tmp_path / "projects.json").get(project.project_id)

    assert "Successfully added 1 task" in result
    assert "ignored stale model-supplied project_id" in result
    assert restored is not None
    assert [task["title"] for task in restored.tasks] == ["First task"]


def test_plan_add_tasks_still_supports_explicit_project_id(tmp_path):
    manager = ProjectManager(tmp_path / "projects.json")
    project = manager.create("Planning", "objective", "webchat:test")
    register_plan_project_manager(manager)
    try:
        result = asyncio.run(
            plan_add_tasks(
                tasks=[{"title": "Explicit task"}],
                project_id=project.project_id,
                _context={},
            )
        )
    finally:
        unregister_plan_project_manager()

    restored = ProjectManager(tmp_path / "projects.json").get(project.project_id)

    assert "Successfully added 1 task" in result
    assert restored is not None
    assert [task["title"] for task in restored.tasks] == ["Explicit task"]
