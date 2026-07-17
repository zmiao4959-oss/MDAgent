"""Stage 14 acceptance: project-local traces feed one global, provenance-aware experience store."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def trace(project_id):
    item = EvolutionTrace(
        chat_id=f"chat:{project_id}",
        objective="validate shared deployment workflow",
        metadata={"project_id": project_id},
    )
    item.add_tool("read", {"path": "config.yaml"}, "ok")
    item.add_tool("execute", {"command": "validate"}, "completed")
    item.finish(success=True, final_response="validated")
    return item


def main():
    with TemporaryDirectory() as directory:
        shared = Path(directory) / "workspace"
        results = []
        projects = []
        for project_id in ("a", "b", "c"):
            project = shared / "projects" / project_id
            projects.append(project)
            service = EvolutionService(project, shared_workspace=shared)
            results.append(service.complete_trace(trace(project_id)))
        assert len({item.experience_id for item in results}) == 1
        assert results[-1].status == "verified"
        assert all(
            (project / ".miniclaw" / "evolution" / "traces.jsonl").is_file()
            for project in projects
        )
        rows = EvolutionService(projects[0], shared_workspace=shared).list_experiences()
        assert rows[0]["source_project_count"] == 3
    print("Stage 14 accepted: projects share evidence while raw traces stay project-local.")


if __name__ == "__main__":
    main()
