"""Run one test-first source patch experiment in an isolated Git worktree."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from .source_proposal import SourceProposal, SourceProposalStore


@dataclass
class PatchActionResult:
    summary: str
    test_commands: List[List[str]] = field(default_factory=list)


class SourcePatchAgent(Protocol):
    def write_reproduction_test(
        self, worktree: Path, proposal: SourceProposal
    ) -> PatchActionResult: ...

    def implement_patch(
        self, worktree: Path, proposal: SourceProposal
    ) -> PatchActionResult: ...


@dataclass
class SourceExperiment:
    proposal_id: str
    baseline_commit: str
    branch: str
    worktree: str
    experiment_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "created"
    reproduction_commands: List[List[str]] = field(default_factory=list)
    reproduction_results: List[Dict] = field(default_factory=list)
    targeted_results: List[Dict] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    diff_stat: Dict = field(default_factory=dict)
    error: str = ""
    candidate_commit: str = ""
    promotion_commit: str = ""
    rollback_commit: str = ""
    evaluation_report: Dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)


class SourceExperimentStore:
    ACTIVE = frozenset({"created", "reproduction_failed", "patching", "patched", "evaluating", "ready"})

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_experiment_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, experiment: SourceExperiment, action: str, detail: Optional[Dict] = None) -> SourceExperiment:
        experiment.updated_at = time.time()
        payload = experiment.to_dict()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO source_experiments (
                    experiment_id, proposal_id, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(experiment_id) DO UPDATE SET
                    status=excluded.status, payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    experiment.experiment_id, experiment.proposal_id, experiment.status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    experiment.created_at, experiment.updated_at,
                ),
            )
            connection.execute(
                "INSERT INTO source_experiment_events (experiment_id, action, detail_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    experiment.experiment_id, action,
                    json.dumps(detail or {}, ensure_ascii=False, sort_keys=True), time.time(),
                ),
            )
        return experiment

    def get(self, experiment_id: str) -> Optional[SourceExperiment]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM source_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
        return SourceExperiment(**json.loads(row["payload_json"])) if row else None

    def list(self, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        query = "SELECT payload_json FROM source_experiments"
        params: List[object] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(0, limit))
        with closing(self._connect()) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def active(self) -> List[Dict]:
        return [row for row in self.list(limit=1000) if row.get("status") in self.ACTIVE]

    def events(self, experiment_id: str, limit: int = 100) -> List[Dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT action, detail_json, created_at FROM source_experiment_events
                WHERE experiment_id=? ORDER BY event_id DESC LIMIT ?
                """,
                (experiment_id, max(0, limit)),
            ).fetchall()
        return [
            {"action": row["action"], "detail": json.loads(row["detail_json"]), "created_at": row["created_at"]}
            for row in rows
        ]


class SourcePatchPolicy:
    GENERATED_TEST_PREFIX = "tests/test_evolution_generated_"
    PROTECTED_PREFIXES = (
        ".git", ".github", "miniclaw/learning/source_",
        "miniclaw/learning/governance.py", "miniclaw/tools/execute.py",
        "scripts/acceptance_stage", "docs/SOURCE_EVOLUTION_SINGLE_CANDIDATE_PLAN.md",
    )
    DANGEROUS_ADDITIONS = re.compile(
        r"\b(?:eval|exec)\s*\(|\bos\.system\s*\(|shell\s*=\s*True|"
        r"\bpickle\.loads\s*\(|\brequests\.(?:get|post)\s*\(|"
        r"\burllib\.request\.|\bsubprocess\.(?:Popen|run)\([^\n]*shell\s*=\s*True",
        re.IGNORECASE,
    )

    def __init__(self, *, max_files: int = 8, max_changed_lines: int = 500):
        self.max_files = max_files
        self.max_changed_lines = max_changed_lines

    def validate_reproduction_test(self, repo: Path) -> List[str]:
        entries = _status_entries(repo)
        errors = []
        if not entries:
            return ["patch agent did not create a reproduction test"]
        for status, path in entries:
            if status != "??" or not path.startswith(self.GENERATED_TEST_PREFIX) or not path.endswith(".py"):
                errors.append("reproduction phase may only add tests/test_evolution_generated_*.py")
        return list(dict.fromkeys(errors))

    def validate_patch(self, repo: Path) -> Dict:
        entries = _status_entries(repo)
        errors = []
        files = [path for _, path in entries]
        if len(files) > self.max_files:
            errors.append(f"patch changes {len(files)} files; maximum is {self.max_files}")
        for status, path in entries:
            if path.startswith("tests/") and status != "??":
                errors.append(f"existing test cannot be modified: {path}")
            if any(path == prefix or path.startswith(prefix) for prefix in self.PROTECTED_PREFIXES):
                errors.append(f"protected path cannot be modified: {path}")
            target = (repo / path).resolve()
            try:
                target.relative_to(repo.resolve())
            except ValueError:
                errors.append(f"path escapes repository: {path}")
                continue
            if target.is_symlink():
                errors.append(f"symbolic links are forbidden: {path}")
            if target.is_file() and b"\x00" in target.read_bytes()[:8192]:
                errors.append(f"binary files are forbidden: {path}")
        added, deleted = _changed_line_counts(repo, entries)
        if added + deleted > self.max_changed_lines:
            errors.append(
                f"patch changes {added + deleted} lines; maximum is {self.max_changed_lines}"
            )
        additions = _added_text(repo, entries)
        if self.DANGEROUS_ADDITIONS.search(additions):
            errors.append("patch adds a dangerous dynamic-execution or network pattern")
        return {
            "valid": not errors,
            "errors": list(dict.fromkeys(errors)),
            "files": sorted(files),
            "added": added,
            "deleted": deleted,
        }


class SingleCandidateExperimentRunner:
    def __init__(
        self,
        repo_root: Path | str,
        storage_root: Path | str,
        proposal_store: SourceProposalStore,
        experiment_store: SourceExperimentStore,
        *,
        policy: Optional[SourcePatchPolicy] = None,
        command_timeout_sec: int = 120,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.storage_root = Path(storage_root).resolve()
        self.proposal_store = proposal_store
        self.experiment_store = experiment_store
        self.policy = policy or SourcePatchPolicy()
        self.command_timeout_sec = max(1, command_timeout_sec)

    def run(self, proposal_id: str, patch_agent: SourcePatchAgent) -> SourceExperiment:
        proposal = self.proposal_store.get(proposal_id)
        if proposal is None:
            raise FileNotFoundError("source proposal not found")
        if proposal.status != "approved":
            raise PermissionError("source proposal must be approved before an experiment")
        if self.experiment_store.active():
            raise RuntimeError("another source evolution experiment is active")
        self._preflight_repo()
        baseline = _git(self.repo_root, "rev-parse", "HEAD").strip()
        experiment_id = uuid.uuid4().hex
        branch = f"codex/evolution-{proposal_id[:12]}-{experiment_id[:6]}"
        worktree = (self.storage_root / "worktrees" / experiment_id).resolve()
        expected_parent = (self.storage_root / "worktrees").resolve()
        if worktree.parent != expected_parent:
            raise ValueError("invalid worktree target")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        _git(self.repo_root, "worktree", "add", "-b", branch, str(worktree), baseline)
        experiment = SourceExperiment(
            experiment_id=experiment_id,
            proposal_id=proposal_id,
            baseline_commit=baseline,
            branch=branch,
            worktree=str(worktree),
            status="created",
        )
        self.experiment_store.save(experiment, "worktree_created")
        self.proposal_store.update_status(
            proposal_id, "experimenting", experiment_id=experiment_id
        )
        try:
            reproduction = patch_agent.write_reproduction_test(worktree, proposal)
            errors = self.policy.validate_reproduction_test(worktree)
            if errors:
                raise ValueError("; ".join(errors))
            commands = _validate_test_commands(reproduction.test_commands)
            experiment.reproduction_commands = commands
            experiment.reproduction_results = [
                _run_command(worktree, command, self.command_timeout_sec)
                for command in commands
            ]
            if not experiment.reproduction_results or all(
                result["returncode"] == 0 for result in experiment.reproduction_results
            ):
                raise ValueError("reproduction test must fail against the unchanged implementation")
            experiment.status = "reproduction_failed"
            self.experiment_store.save(experiment, "reproduction_confirmed")

            experiment.status = "patching"
            self.experiment_store.save(experiment, "patch_started")
            patch_agent.implement_patch(worktree, proposal)
            validation = self.policy.validate_patch(worktree)
            if not validation["valid"]:
                raise PermissionError("; ".join(validation["errors"]))
            experiment.changed_files = validation["files"]
            experiment.diff_stat = {
                "files": len(validation["files"]),
                "added": validation["added"],
                "deleted": validation["deleted"],
            }
            experiment.targeted_results = [
                _run_command(worktree, command, self.command_timeout_sec)
                for command in commands
            ]
            if any(result["returncode"] != 0 for result in experiment.targeted_results):
                raise ValueError("candidate patch does not pass its reproduction tests")
            experiment.status = "patched"
            self.experiment_store.save(experiment, "patch_validated", validation)
            return experiment
        except Exception as exc:
            experiment.status = "failed"
            experiment.error = str(exc)[:1000]
            self.experiment_store.save(experiment, "experiment_failed", {"error": experiment.error})
            self.proposal_store.update_status(
                proposal_id, "failed", experiment_id=experiment_id,
                detail={"error": experiment.error},
            )
            raise

    def _preflight_repo(self) -> None:
        if _git(self.repo_root, "rev-parse", "--is-inside-work-tree").strip() != "true":
            raise ValueError("source repository is not a Git worktree")
        if _git(self.repo_root, "status", "--porcelain").strip():
            raise RuntimeError("source repository must be clean before evolution")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def _run_command(repo: Path, command: List[str], timeout_sec: int) -> Dict:
    started = time.time()
    actual_command = list(command)
    environment = dict(os.environ)
    if actual_command[:3] == ["python", "-m", "pytest"]:
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if "no:cacheprovider" not in actual_command:
            actual_command.extend(["-p", "no:cacheprovider"])
    completed = subprocess.run(
        actual_command, cwd=repo, env=environment,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout_sec, check=False,
    )
    return {
        "command": actual_command,
        "returncode": completed.returncode,
        "output": completed.stdout[-4000:],
        "duration_sec": round(time.time() - started, 3),
    }


def _validate_test_commands(commands: List[List[str]]) -> List[List[str]]:
    if not commands:
        raise ValueError("patch agent must provide a reproduction test command")
    validated = []
    for command in commands:
        values = [str(item) for item in command]
        if values[:3] != ["python", "-m", "pytest"]:
            raise PermissionError("only python -m pytest reproduction commands are allowed")
        targets = [item for item in values[3:] if not item.startswith("-")]
        if not targets or any(
            not target.replace("\\", "/").startswith("tests/test_evolution_generated_")
            for target in targets
        ):
            raise PermissionError("reproduction command must target generated evolution tests")
        validated.append(values)
    return validated


def _status_entries(repo: Path) -> List[tuple[str, str]]:
    rows = []
    for line in _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines():
        status = line[:2]
        path = line[3:].replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append((status, path))
    return rows


def _changed_line_counts(repo: Path, entries: List[tuple[str, str]]) -> tuple[int, int]:
    added = deleted = 0
    tracked = _git(repo, "diff", "--numstat")
    for line in tracked.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            added += int(parts[0])
            deleted += int(parts[1])
    for status, path in entries:
        target = repo / path
        if status == "??" and target.is_file():
            added += len(target.read_text(encoding="utf-8", errors="replace").splitlines())
    return added, deleted


def _added_text(repo: Path, entries: List[tuple[str, str]]) -> str:
    additions = [
        line[1:] for line in _git(repo, "diff", "--unified=0").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    for status, path in entries:
        target = repo / path
        if status == "??" and target.is_file():
            additions.append(target.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(additions)
