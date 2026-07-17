"""Acceptance check for the complete single-candidate source evolution cycle."""
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.source_experiment import PatchActionResult, SingleCandidateExperimentRunner, SourceExperimentStore
from miniclaw.learning.source_promotion import SourceCandidateEvaluator, SourceCandidatePromoter
from miniclaw.learning.source_proposal import SourceProposal, SourceProposalStore


def run(repo, *args):
    return subprocess.run(args, cwd=repo, check=True, text=True, capture_output=True).stdout


class Agent:
    def write_reproduction_test(self, worktree, proposal):
        path = worktree / "tests" / "test_evolution_generated_value.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text("from app import value\n\ndef test_value():\n    assert value() == 2\n", encoding="utf-8")
        return PatchActionResult("reproduce", [["python", "-m", "pytest", "-q", str(path.relative_to(worktree))]])

    def implement_patch(self, worktree, proposal):
        (worktree / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        return PatchActionResult("fix")


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repo = root / "repo"
        (repo / "tests").mkdir(parents=True)
        (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        run(repo, "git", "init", "-b", "main")
        run(repo, "git", "config", "user.email", "acceptance@example.invalid")
        run(repo, "git", "config", "user.name", "Acceptance")
        run(repo, "git", "add", ".")
        run(repo, "git", "commit", "-m", "baseline")
        database = root / "source.db"
        proposals = SourceProposalStore(database)
        experiments = SourceExperimentStore(database)
        proposal = SourceProposal(
            title="Fix value", problem_signature="assertion|value", error_category="assertion",
            evidence_run_ids=["1", "2", "3"], source_projects=["a", "b"],
            suspected_files=["app.py"], sanitized_examples=["wrong value"],
            hypothesis="constant is wrong", expected_benefit="correct value", risk="low",
            rollback_plan="revert merge",
        )
        proposals.upsert(proposal)
        proposals.update_status(proposal.proposal_id, "approved")
        experiment = SingleCandidateExperimentRunner(repo, root / "storage", proposals, experiments).run(proposal.proposal_id, Agent())
        experiment = SourceCandidateEvaluator(repo, proposals, experiments).evaluate(
            experiment.experiment_id,
            full_commands=[["python", "-m", "pytest", "-q"], ["git", "diff", "--check"]],
        )
        promoter = SourceCandidatePromoter(repo, proposals, experiments)
        experiment = promoter.promote(experiment.experiment_id, f"PROMOTE {experiment.experiment_id}")
        assert "return 2" in (repo / "app.py").read_text(encoding="utf-8")
        experiment = promoter.rollback(experiment.experiment_id, f"ROLLBACK {experiment.experiment_id}")
        assert experiment.status == "rolled_back"
        assert "return 1" in (repo / "app.py").read_text(encoding="utf-8")
    print("stage20 acceptance passed")


if __name__ == "__main__":
    main()
