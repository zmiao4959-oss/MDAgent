"""Evaluate, commit, promote, and recover one isolated source candidate."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional

from .source_experiment import (
    SourceExperiment,
    SourceExperimentStore,
    SourcePatchPolicy,
    _git,
    _run_command,
)
from .source_proposal import SourceProposalStore


class SourceCandidateEvaluator:
    def __init__(
        self,
        repo_root: Path | str,
        proposal_store: SourceProposalStore,
        experiment_store: SourceExperimentStore,
        *,
        policy: Optional[SourcePatchPolicy] = None,
        command_timeout_sec: int = 300,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.proposal_store = proposal_store
        self.experiment_store = experiment_store
        self.policy = policy or SourcePatchPolicy()
        self.command_timeout_sec = max(1, command_timeout_sec)

    def evaluate(
        self,
        experiment_id: str,
        *,
        full_commands: Optional[List[List[str]]] = None,
    ) -> SourceExperiment:
        experiment = self.experiment_store.get(experiment_id)
        if experiment is None:
            raise FileNotFoundError("source experiment not found")
        if experiment.status != "patched":
            raise ValueError("only a patched source experiment can be evaluated")
        worktree = Path(experiment.worktree).resolve()
        validation = self.policy.validate_patch(worktree)
        if not validation["valid"]:
            return self._fail(experiment, "source policy validation failed", validation)
        commands = full_commands or self._default_commands(worktree)
        commands = _validate_evaluation_commands(commands)
        experiment.status = "evaluating"
        self.experiment_store.save(experiment, "evaluation_started", {"commands": commands})
        results = [
            _run_command(worktree, command, self.command_timeout_sec)
            for command in commands
        ]
        passed = bool(results) and all(result["returncode"] == 0 for result in results)
        changed_lines = int(validation["added"]) + int(validation["deleted"])
        score = round(max(0.0, 1.0 - min(0.4, changed_lines / 2000)), 3) if passed else 0.0
        report = {
            "passed": passed,
            "score": score,
            "commands": results,
            "policy": validation,
            "targeted_tests_passed": all(
                result.get("returncode") == 0 for result in experiment.targeted_results
            ),
            "evaluated_at": time.time(),
        }
        experiment.evaluation_report = report
        if not passed:
            return self._fail(experiment, "one or more protected evaluation commands failed", report)
        _git(worktree, "add", "--all")
        _git(
            worktree,
            "commit",
            "-m",
            f"evolution({experiment.proposal_id[:12]}): validated source candidate",
        )
        experiment.candidate_commit = _git(worktree, "rev-parse", "HEAD").strip()
        experiment.status = "ready"
        self.experiment_store.save(experiment, "candidate_committed", report)
        self.proposal_store.update_status(
            experiment.proposal_id,
            "ready",
            experiment_id=experiment.experiment_id,
            detail={"candidate_commit": experiment.candidate_commit, "score": score},
        )
        return experiment

    def _fail(self, experiment: SourceExperiment, message: str, report: Dict) -> SourceExperiment:
        experiment.status = "failed"
        experiment.error = message
        experiment.evaluation_report = report
        self.experiment_store.save(experiment, "evaluation_failed", report)
        self.proposal_store.update_status(
            experiment.proposal_id,
            "failed",
            experiment_id=experiment.experiment_id,
            detail={"error": message},
        )
        return experiment

    @staticmethod
    def _default_commands(worktree: Path) -> List[List[str]]:
        commands = [["python", "-m", "pytest", "-q"]]
        app_js = worktree / "miniclaw" / "channels" / "web" / "static" / "app.js"
        if app_js.is_file():
            commands.append(["node", "--check", str(app_js.relative_to(worktree))])
        commands.append(["git", "diff", "--check"])
        return commands


class SourceCandidatePromoter:
    def __init__(
        self,
        repo_root: Path | str,
        proposal_store: SourceProposalStore,
        experiment_store: SourceExperimentStore,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.proposal_store = proposal_store
        self.experiment_store = experiment_store

    def promote(self, experiment_id: str, confirmation: str) -> SourceExperiment:
        experiment = self._get(experiment_id)
        if confirmation != f"PROMOTE {experiment_id}":
            raise PermissionError("promotion confirmation phrase does not match")
        if experiment.status != "ready" or not experiment.candidate_commit:
            raise ValueError("source candidate is not ready for promotion")
        self._require_clean_main()
        current = _git(self.repo_root, "rev-parse", "HEAD").strip()
        if current != experiment.baseline_commit:
            raise RuntimeError("main branch moved after the source experiment baseline")
        branch_head = _git(
            self.repo_root, "rev-parse", experiment.branch
        ).strip()
        if branch_head != experiment.candidate_commit:
            raise RuntimeError("candidate branch no longer matches the evaluated commit")
        _git(
            self.repo_root,
            "merge",
            "--no-ff",
            experiment.branch,
            "-m",
            f"Promote source evolution {experiment.proposal_id[:12]}",
        )
        experiment.promotion_commit = _git(self.repo_root, "rev-parse", "HEAD").strip()
        experiment.status = "promoted"
        self.experiment_store.save(experiment, "promoted", {
            "promotion_commit": experiment.promotion_commit,
        })
        self.proposal_store.update_status(
            experiment.proposal_id,
            "promoted",
            experiment_id=experiment.experiment_id,
            detail={"promotion_commit": experiment.promotion_commit},
        )
        return experiment

    def rollback(self, experiment_id: str, confirmation: str) -> SourceExperiment:
        experiment = self._get(experiment_id)
        if confirmation != f"ROLLBACK {experiment_id}":
            raise PermissionError("rollback confirmation phrase does not match")
        if experiment.status != "promoted" or not experiment.promotion_commit:
            raise ValueError("source candidate has not been promoted")
        self._require_clean_main()
        try:
            _git(
                self.repo_root,
                "merge-base",
                "--is-ancestor",
                experiment.promotion_commit,
                "HEAD",
            )
        except RuntimeError as exc:
            raise RuntimeError("promotion commit is not part of the current branch") from exc
        _git(
            self.repo_root,
            "revert",
            "-m",
            "1",
            "--no-edit",
            experiment.promotion_commit,
        )
        experiment.rollback_commit = _git(self.repo_root, "rev-parse", "HEAD").strip()
        experiment.status = "rolled_back"
        self.experiment_store.save(experiment, "rolled_back", {
            "rollback_commit": experiment.rollback_commit,
        })
        self.proposal_store.update_status(
            experiment.proposal_id,
            "rolled_back",
            experiment_id=experiment.experiment_id,
            detail={"rollback_commit": experiment.rollback_commit},
        )
        return experiment

    def _get(self, experiment_id: str) -> SourceExperiment:
        experiment = self.experiment_store.get(experiment_id)
        if experiment is None:
            raise FileNotFoundError("source experiment not found")
        return experiment

    def _require_clean_main(self) -> None:
        if _git(self.repo_root, "status", "--porcelain").strip():
            raise RuntimeError("main source worktree must be clean")


def _validate_evaluation_commands(commands: List[List[str]]) -> List[List[str]]:
    if not commands:
        raise ValueError("at least one protected evaluation command is required")
    values = [[str(item) for item in command] for command in commands]
    for command in values:
        allowed = (
            command[:3] == ["python", "-m", "pytest"]
            or command[:2] == ["node", "--check"]
            or command == ["git", "diff", "--check"]
        )
        if not allowed:
            raise PermissionError(f"unsupported evaluation command: {' '.join(command)}")
    if not any(command[:3] == ["python", "-m", "pytest"] for command in values):
        raise PermissionError("protected evaluation must include pytest")
    return values
