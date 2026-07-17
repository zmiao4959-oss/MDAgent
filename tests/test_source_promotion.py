from pathlib import Path
import subprocess

import pytest

from miniclaw.learning.source_experiment import (
    PatchActionResult,
    SingleCandidateExperimentRunner,
    SourceExperimentStore,
)
from miniclaw.learning.source_promotion import (
    SourceCandidateEvaluator,
    SourceCandidatePromoter,
)
from miniclaw.learning.source_proposal import SourceProposal, SourceProposalStore


def _run(repo, *args):
    return subprocess.run(
        list(args), cwd=repo, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout


def _setup(tmp_path):
    repo = tmp_path / "repo"
    (repo / "package").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "package" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "package" / "calc.py").write_text(
        "def add(left, right):\n    return left - right\n", encoding="utf-8"
    )
    _run(repo, "git", "init", "-b", "main")
    _run(repo, "git", "config", "user.email", "evolution@example.invalid")
    _run(repo, "git", "config", "user.name", "Evolution Test")
    _run(repo, "git", "add", ".")
    _run(repo, "git", "commit", "-m", "baseline")
    database = tmp_path / "source.db"
    proposals = SourceProposalStore(database)
    experiments = SourceExperimentStore(database)
    proposal = SourceProposal(
        title="Fix repeated addition defect",
        problem_signature="value-error|wrong addition",
        error_category="value-error",
        evidence_run_ids=["a", "b", "c"],
        source_projects=["a", "b"],
        suspected_files=["package/calc.py"],
        sanitized_examples=["expected addition result"],
        hypothesis="The implementation uses the wrong operator.",
        expected_benefit="Restore addition.", risk="Low.",
        rollback_plan="Revert the merge.",
    )
    proposals.upsert(proposal)
    proposals.update_status(proposal.proposal_id, "approved")
    return repo, proposals, experiments, proposal


class FixingAgent:
    def write_reproduction_test(self, worktree, proposal):
        path = worktree / "tests" / "test_evolution_generated_calc.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "from package.calc import add\n\n"
            "def test_addition_regression():\n    assert add(5, 2) == 7\n",
            encoding="utf-8",
        )
        return PatchActionResult("reproduce", [[
            "python", "-m", "pytest", "-q", str(path.relative_to(worktree))
        ]])

    def implement_patch(self, worktree, proposal):
        (worktree / "package" / "calc.py").write_text(
            "def add(left, right):\n    return left + right\n", encoding="utf-8"
        )
        return PatchActionResult("fix")


def _patched(tmp_path):
    repo, proposals, experiments, proposal = _setup(tmp_path)
    experiment = SingleCandidateExperimentRunner(
        repo, tmp_path / "storage", proposals, experiments
    ).run(proposal.proposal_id, FixingAgent())
    return repo, proposals, experiments, experiment


def _evaluate(repo, proposals, experiments, experiment):
    return SourceCandidateEvaluator(repo, proposals, experiments).evaluate(
        experiment.experiment_id,
        full_commands=[["python", "-m", "pytest", "-q"], ["git", "diff", "--check"]],
    )


def test_evaluation_commits_candidate_without_touching_main(tmp_path):
    repo, proposals, experiments, experiment = _patched(tmp_path)
    ready = _evaluate(repo, proposals, experiments, experiment)

    assert ready.status == "ready"
    assert ready.candidate_commit
    assert ready.evaluation_report["passed"] is True
    assert _run(repo, "git", "status", "--porcelain") == ""
    assert "left - right" in (repo / "package" / "calc.py").read_text(encoding="utf-8")
    assert _run(repo, "git", "rev-parse", ready.branch).strip() == ready.candidate_commit


def test_full_evaluation_failure_cannot_be_promoted(tmp_path):
    repo, proposals, experiments, proposal = _setup(tmp_path)

    class PartiallyFixingAgent(FixingAgent):
        def implement_patch(self, worktree, proposal):
            super().implement_patch(worktree, proposal)
            (worktree / "tests" / "test_evolution_generated_other.py").write_text(
                "def test_other_regression():\n    assert False\n", encoding="utf-8"
            )
            return PatchActionResult("partial fix")

    experiment = SingleCandidateExperimentRunner(
        repo, tmp_path / "storage", proposals, experiments
    ).run(proposal.proposal_id, PartiallyFixingAgent())
    failed = _evaluate(repo, proposals, experiments, experiment)

    assert failed.status == "failed"
    assert not failed.candidate_commit
    with pytest.raises(ValueError, match="not ready"):
        SourceCandidatePromoter(repo, proposals, experiments).promote(
            failed.experiment_id, f"PROMOTE {failed.experiment_id}"
        )


def test_exact_confirmation_promotes_candidate(tmp_path):
    repo, proposals, experiments, experiment = _patched(tmp_path)
    ready = _evaluate(repo, proposals, experiments, experiment)
    promoter = SourceCandidatePromoter(repo, proposals, experiments)

    with pytest.raises(PermissionError, match="confirmation"):
        promoter.promote(ready.experiment_id, "PROMOTE")
    promoted = promoter.promote(ready.experiment_id, f"PROMOTE {ready.experiment_id}")

    assert promoted.status == "promoted"
    assert promoted.promotion_commit
    assert "left + right" in (repo / "package" / "calc.py").read_text(encoding="utf-8")
    assert len(_run(repo, "git", "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3


def test_main_drift_blocks_promotion(tmp_path):
    repo, proposals, experiments, experiment = _patched(tmp_path)
    ready = _evaluate(repo, proposals, experiments, experiment)
    (repo / "README.md").write_text("drift\n", encoding="utf-8")
    _run(repo, "git", "add", "README.md")
    _run(repo, "git", "commit", "-m", "unrelated drift")

    with pytest.raises(RuntimeError, match="main branch moved"):
        SourceCandidatePromoter(repo, proposals, experiments).promote(
            ready.experiment_id, f"PROMOTE {ready.experiment_id}"
        )


def test_promoted_merge_can_be_rolled_back(tmp_path):
    repo, proposals, experiments, experiment = _patched(tmp_path)
    ready = _evaluate(repo, proposals, experiments, experiment)
    promoter = SourceCandidatePromoter(repo, proposals, experiments)
    promoter.promote(ready.experiment_id, f"PROMOTE {ready.experiment_id}")

    with pytest.raises(PermissionError, match="confirmation"):
        promoter.rollback(ready.experiment_id, "ROLLBACK")
    rolled_back = promoter.rollback(
        ready.experiment_id, f"ROLLBACK {ready.experiment_id}"
    )

    assert rolled_back.status == "rolled_back"
    assert rolled_back.rollback_commit
    assert "left - right" in (repo / "package" / "calc.py").read_text(encoding="utf-8")
    assert not (repo / "tests" / "test_evolution_generated_calc.py").exists()


def test_evaluation_command_allowlist_is_enforced(tmp_path):
    repo, proposals, experiments, experiment = _patched(tmp_path)
    with pytest.raises(PermissionError, match="unsupported evaluation command"):
        SourceCandidateEvaluator(repo, proposals, experiments).evaluate(
            experiment.experiment_id, full_commands=[["python", "setup.py", "test"]]
        )
