"""Stage 19 acceptance: a test-first patch stays inside one isolated Git worktree."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.source_experiment import (
    PatchActionResult, SingleCandidateExperimentRunner, SourceExperimentStore,
)
from miniclaw.learning.source_proposal import SourceProposal, SourceProposalStore


def command(repo, *args):
    return subprocess.run(
        list(args), cwd=repo, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout


class Agent:
    def write_reproduction_test(self, worktree, proposal):
        path = worktree / "tests" / "test_evolution_generated_calc.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "from package.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            encoding="utf-8",
        )
        return PatchActionResult("reproduce", [["python", "-m", "pytest", "-q", "tests/test_evolution_generated_calc.py"]])

    def implement_patch(self, worktree, proposal):
        (worktree / "package" / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        return PatchActionResult("fix")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        repo = root / "repo"
        (repo / "package").mkdir(parents=True)
        (repo / "package" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "package" / "calc.py").write_text(
            "def add(a, b):\n    return a - b\n", encoding="utf-8"
        )
        command(repo, "git", "init", "-b", "main")
        command(repo, "git", "config", "user.email", "evolution@example.invalid")
        command(repo, "git", "config", "user.name", "Evolution Acceptance")
        command(repo, "git", "add", ".")
        command(repo, "git", "commit", "-m", "baseline")
        database = root / "source.db"
        proposals = SourceProposalStore(database)
        experiments = SourceExperimentStore(database)
        proposal = SourceProposal(
            title="fix", problem_signature="addition|wrong", error_category="value-error",
            evidence_run_ids=["1", "2", "3"], source_projects=["a", "b"],
            suspected_files=["package/calc.py"], sanitized_examples=["wrong"],
            hypothesis="operator", expected_benefit="correct", risk="low", rollback_plan="revert",
        )
        proposals.upsert(proposal)
        proposals.update_status(proposal.proposal_id, "approved")
        baseline = (repo / "package" / "calc.py").read_text(encoding="utf-8")
        result = SingleCandidateExperimentRunner(
            repo, root / "storage", proposals, experiments
        ).run(proposal.proposal_id, Agent())
        assert result.status == "patched"
        assert result.reproduction_results[0]["returncode"] != 0
        assert result.targeted_results[0]["returncode"] == 0
        assert (repo / "package" / "calc.py").read_text(encoding="utf-8") == baseline
    print("Stage 19 accepted: test-first source patching stays isolated and policy-bounded.")


if __name__ == "__main__":
    main()
