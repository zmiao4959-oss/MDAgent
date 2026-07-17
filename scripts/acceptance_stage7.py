"""Stage 7 acceptance: offline gate, policy versioning, and rollback."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def successful_trace() -> EvolutionTrace:
    trace = EvolutionTrace(chat_id="acceptance", objective="prepare a LAMMPS simulation")
    trace.add_tool("execute", {"command": "lammps"}, "completed")
    trace.finish(
        success=True,
        final_response="simulation complete",
        prompt_tokens=100,
        completion_tokens=20,
    )
    return trace


def main() -> None:
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        experience = None
        for _ in range(3):
            experience = service.complete_trace(successful_trace())

        assert experience and experience.status == "verified"
        evaluation = service.evaluations()[0]
        assert evaluation["passed"] is True
        assert evaluation["report"]["sample_count"] == 3
        assert service.policy_versions()[0]["action"] == "promote"

        rolled_back = service.rollback(experience.experience_id)
        assert rolled_back and rolled_back.status == "rejected"
        assert service.relevant("LAMMPS simulation") == []
        assert service.policy_versions()[0]["action"] == "rollback"
    print("Stage 7 accepted: offline promotion gate, version snapshot, and rollback work.")


if __name__ == "__main__":
    main()
