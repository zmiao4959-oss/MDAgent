from pathlib import Path
import subprocess

import pytest

from miniclaw.learning.source_experiment import (
    PatchActionResult,
    SingleCandidateExperimentRunner,
    SourceExperimentStore,
)
from miniclaw.learning.source_proposal import SourceProposal, SourceProposalStore


def _run(repo, *args):
    return subprocess.run(
        list(args), cwd=repo, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout


def _repo(tmp_path):
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
    return repo


def _proposal(store):
    proposal = SourceProposal(
        title="Fix repeated addition defect",
        problem_signature="value-error|wrong addition",
        error_category="value-error",
        evidence_run_ids=["a", "b", "c"],
        source_projects=["a", "b"],
        suspected_files=["package/calc.py"],
        sanitized_examples=["expected addition result"],
        hypothesis="The addition implementation uses the wrong operator.",
        expected_benefit="Restore correct addition.",
        risk="Low and covered by a focused regression test.",
        rollback_plan="Revert the candidate merge.",
    )
    store.upsert(proposal)
    return store.update_status(proposal.proposal_id, "approved")


class FixingAgent:
    implemented = False

    def write_reproduction_test(self, worktree, proposal):
        path = worktree / "tests" / "test_evolution_generated_calc.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "from package.calc import add\n\n"
            "def test_addition_regression():\n"
            "    assert add(5, 2) == 7\n",
            encoding="utf-8",
        )
        return PatchActionResult(
            summary="Reproduce incorrect addition.",
            test_commands=[["python", "-m", "pytest", "-q", str(path.relative_to(worktree))]],
        )

    def implement_patch(self, worktree, proposal):
        self.implemented = True
        path = worktree / "package" / "calc.py"
        path.write_text(
            "def add(left, right):\n    return left + right\n", encoding="utf-8"
        )
        return PatchActionResult(summary="Use addition operator.")


def _runner(tmp_path, repo, proposal_store, experiment_store):
    return SingleCandidateExperimentRunner(
        repo,
        tmp_path / "evolution-storage",
        proposal_store,
        experiment_store,
    )


def test_test_first_patch_runs_in_worktree_and_keeps_main_untouched(tmp_path):
    repo = _repo(tmp_path)
    database = tmp_path / "source.db"
    proposals = SourceProposalStore(database)
    experiments = SourceExperimentStore(database)
    proposal = _proposal(proposals)
    baseline_source = (repo / "package" / "calc.py").read_text(encoding="utf-8")

    experiment = _runner(tmp_path, repo, proposals, experiments).run(
        proposal.proposal_id, FixingAgent()
    )

    assert experiment.status == "patched"
    assert experiment.reproduction_results[0]["returncode"] != 0
    assert experiment.targeted_results[0]["returncode"] == 0
    assert "package/calc.py" in experiment.changed_files
    assert "tests/test_evolution_generated_calc.py" in experiment.changed_files
    assert (repo / "package" / "calc.py").read_text(encoding="utf-8") == baseline_source
    assert _run(repo, "git", "status", "--porcelain") == ""
    assert Path(experiment.worktree).is_dir()


def test_reproduction_test_must_fail_before_implementation(tmp_path):
    repo = _repo(tmp_path)
    database = tmp_path / "source.db"
    proposals = SourceProposalStore(database)
    experiments = SourceExperimentStore(database)
    proposal = _proposal(proposals)

    class PassingTestAgent(FixingAgent):
        def write_reproduction_test(self, worktree, proposal):
            path = worktree / "tests" / "test_evolution_generated_existing.py"
            path.parent.mkdir(exist_ok=True)
            path.write_text("def test_existing_behavior():\n    assert True\n", encoding="utf-8")
            return PatchActionResult("bad reproduction", [[
                "python", "-m", "pytest", "-q", str(path.relative_to(worktree))
            ]])

    agent = PassingTestAgent()
    with pytest.raises(ValueError, match="must fail"):
        _runner(tmp_path, repo, proposals, experiments).run(proposal.proposal_id, agent)

    assert agent.implemented is False
    assert experiments.list()[0]["status"] == "failed"


def test_protected_file_and_dangerous_code_changes_are_rejected(tmp_path):
    repo = _repo(tmp_path)
    protected = repo / "miniclaw" / "learning"
    protected.mkdir(parents=True)
    (protected / "governance.py").write_text("SAFE = True\n", encoding="utf-8")
    _run(repo, "git", "add", ".")
    _run(repo, "git", "commit", "-m", "add protected file")
    database = tmp_path / "source.db"
    proposals = SourceProposalStore(database)
    experiments = SourceExperimentStore(database)
    proposal = _proposal(proposals)

    class ProtectedAgent(FixingAgent):
        def implement_patch(self, worktree, proposal):
            (worktree / "miniclaw" / "learning" / "governance.py").write_text(
                "SAFE = False\n", encoding="utf-8"
            )
            return PatchActionResult("weaken governance")

    with pytest.raises(PermissionError, match="protected path"):
        _runner(tmp_path, repo, proposals, experiments).run(
            proposal.proposal_id, ProtectedAgent()
        )


def test_only_one_source_experiment_can_remain_active(tmp_path):
    repo = _repo(tmp_path)
    database = tmp_path / "source.db"
    proposals = SourceProposalStore(database)
    experiments = SourceExperimentStore(database)
    proposal = _proposal(proposals)
    runner = _runner(tmp_path, repo, proposals, experiments)
    runner.run(proposal.proposal_id, FixingAgent())

    second = SourceProposal(
        title="Second", problem_signature="second|failure", error_category="runtime-error",
        evidence_run_ids=["1", "2", "3"], source_projects=["a", "b"],
        suspected_files=[], sanitized_examples=["failure"], hypothesis="second",
        expected_benefit="fix", risk="low", rollback_plan="revert",
    )
    proposals.upsert(second)
    proposals.update_status(second.proposal_id, "approved")

    with pytest.raises(RuntimeError, match="another source evolution experiment"):
        runner.run(second.proposal_id, FixingAgent())
