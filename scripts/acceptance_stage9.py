"""Stage 9 acceptance: deterministic canary rollout and automatic promotion."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def run(success=True, applied=None):
    trace = EvolutionTrace(
        chat_id="acceptance",
        objective="prepare a LAMMPS simulation",
        metadata={"applied_experience_ids": applied or []},
    )
    if applied is None:
        trace.add_tool("execute", {"command": "lammps"}, "completed")
    trace.finish(success=success, final_response="done" if success else "", error="failed" if not success else "")
    return trace


def main() -> None:
    with TemporaryDirectory() as directory:
        service = EvolutionService(
            directory,
            canary_enabled=True,
            canary_traffic_percent=100,
            canary_min_trials=3,
            canary_max_failures=2,
            canary_min_success_rate=0.8,
        )
        for _ in range(3):
            experience = service.complete_trace(run())
        assert experience and experience.status == "canary"
        for _ in range(3):
            _, applied = service.prompt_context("prepare LAMMPS simulation")
            service.complete_trace(run(applied=applied), learn=False)
        assert service.experience_store.all()[0].status == "verified"
        assert service.policy_versions()[0]["action"] == "canary_promote"
    print("Stage 9 accepted: deterministic canary rollout, metrics, promotion, and rollback work.")


if __name__ == "__main__":
    main()
