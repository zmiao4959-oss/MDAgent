"""Stage 6 acceptance: task-context isolation prevents cross-domain contamination."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def run_trace(objective: str) -> EvolutionTrace:
    trace = EvolutionTrace(chat_id="acceptance", objective=objective)
    trace.add_tool("read", {"path": "input"}, "ok")
    trace.add_tool("execute", {"command": "run"}, "completed")
    trace.finish(success=True, final_response="done")
    return trace


def main() -> None:
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        lammps = web = None
        for _ in range(3):
            lammps = service.complete_trace(
                run_trace("prepare a LAMMPS molecular dynamics simulation")
            )
            web = service.complete_trace(
                run_trace("research browser accessibility documentation")
            )

        assert lammps and web and lammps.experience_id != web.experience_id
        assert len(service.list_experiences("verified")) == 2
        lammps_matches = service.relevant("LAMMPS simulation")
        web_matches = service.relevant("browser accessibility")
        assert lammps_matches[0].experience_id == lammps.experience_id
        assert web_matches[0].experience_id == web.experience_id
        assert lammps.task_pattern != web.task_pattern
    print("Stage 6 accepted: contextual fingerprints isolate domains and retrieval stays relevant.")


if __name__ == "__main__":
    main()
