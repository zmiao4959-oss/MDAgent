from pathlib import Path

from miniclaw.learning.experience import ExperienceEngine, ExperienceStore
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def _trace(project_id: str) -> EvolutionTrace:
    trace = EvolutionTrace(
        chat_id=f"chat:{project_id}",
        objective="validate shared deployment workflow",
        metadata={"project_id": project_id},
    )
    trace.add_tool("read", {"path": "config.yaml"}, "ok")
    trace.add_tool("execute", {"command": "validate"}, "completed")
    trace.finish(success=True, final_response="validated")
    return trace


def test_projects_share_abstract_experience_but_keep_local_traces(tmp_path):
    shared = tmp_path / "workspace"
    projects = [shared / "projects" / name for name in ("a", "b", "c")]

    learned = []
    for name, project in zip(("a", "b", "c"), projects):
        service = EvolutionService(project, shared_workspace=shared)
        learned.append(service.complete_trace(_trace(name)))

    assert all(item is not None for item in learned)
    assert len({item.experience_id for item in learned}) == 1
    assert learned[0].positive_evidence == 1
    assert learned[1].positive_evidence == 2
    assert learned[2].status == "verified"
    assert (shared / ".miniclaw" / "evolution" / "experiences.db").is_file()
    assert not any(
        (project / ".miniclaw" / "evolution" / "experiences.db").exists()
        for project in projects
    )
    assert all(
        (project / ".miniclaw" / "evolution" / "traces.jsonl").is_file()
        for project in projects
    )
    assert not (shared / ".miniclaw" / "evolution" / "traces.jsonl").exists()

    visible_from_a = EvolutionService(projects[0], shared_workspace=shared).list_experiences()
    assert visible_from_a[0]["positive_evidence"] == 3
    assert visible_from_a[0]["source_project_count"] == 3
    assert {source["project_id"] for source in visible_from_a[0]["sources"]} == {
        "a", "b", "c"
    }


def test_legacy_project_databases_are_migrated_only_once(tmp_path):
    shared = tmp_path / "workspace"
    project = shared / "projects" / "legacy"
    legacy_path = project / ".miniclaw" / "evolution" / "experiences.db"
    legacy_store = ExperienceStore(legacy_path)
    trace = _trace("legacy")
    first = ExperienceEngine(legacy_store).observe_trace(trace)
    assert first is not None

    service = EvolutionService(project, shared_workspace=shared)
    imported = service.experience_store.all()[0]
    assert imported.experience_id == first.experience_id
    assert imported.positive_evidence == 1

    reloaded = EvolutionService(project, shared_workspace=shared)
    same = reloaded.experience_store.all()[0]
    assert same.positive_evidence == 1
    assert same.observations == 1


def test_manual_feedback_records_its_source_project(tmp_path):
    shared = tmp_path / "workspace"
    project = shared / "projects" / "a"
    service = EvolutionService(project, shared_workspace=shared)
    experience = service.complete_trace(_trace("a"))
    assert experience is not None

    service.feedback(experience.experience_id, True, project_id="review-project")
    provenance = service.experience_store.evidence_sources(experience.experience_id)

    assert provenance["source_project_count"] == 2
    assert {source["project_id"] for source in provenance["sources"]} == {
        "a", "review-project"
    }
