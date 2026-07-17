"""Privacy, export, backup, restore, purge, and audit controls."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from .experience import ExperienceStore
from .trace import TraceStore
from .source_experiment import SourceExperimentStore
from .source_proposal import SourceProposalStore


class GovernanceManager:
    CONFIRM_PURGE = "DELETE EVOLUTION DATA"
    CONFIRM_CLEANUP = "CLEAN EVOLUTION ARTIFACTS"

    def __init__(self, root: Path, experience_store: ExperienceStore, trace_store: TraceStore):
        self.root = root
        self.experience_store = experience_store
        self.trace_store = trace_store
        self.audit_path = root / "audit.jsonl"
        self.backup_dir = root / "backups"

    def export(self, *, redact: bool = True) -> Dict:
        source_database = self.root / "source-evolution.db"
        source_proposals: List[Dict] = []
        source_experiments: List[Dict] = []
        if source_database.is_file():
            source_proposals = SourceProposalStore(source_database).list(limit=10000)
            source_experiments = SourceExperimentStore(source_database).list(limit=10000)
        payload = {
            "schema_version": 2,
            "exported_at": time.time(),
            "experiences": [item.__dict__.copy() for item in self.experience_store.all()],
            "evaluations": self.experience_store.evaluations(),
            "versions": self.experience_store.policy_versions(),
            "canary": self.experience_store.canary_stats(),
            "replays": self.experience_store.replay_reports(),
            "maintenance": self.experience_store.maintenance_reports(),
            "reflections": self.experience_store.reflections(None),
            "traces": [trace.to_dict() for trace in self.trace_store.recent(10000)],
            "source_proposals": source_proposals,
            "source_experiments": source_experiments,
            "managed_artifacts": self.managed_artifacts(),
        }
        secrets = _collect_secrets(payload) if redact else set()
        result = _redact_value(payload, secrets) if redact else payload
        self._audit("export", {"redacted": redact})
        return result

    def backup(self) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        destination = self.backup_dir / f"evolution-{timestamp}-{time.time_ns()}.zip"
        files = self._managed_files()
        manifest = {
            "schema_version": 2,
            "created_at": time.time(),
            "files": {name: _sha256(path) for name, path in files},
        }
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in files:
                archive.write(path, arcname=name)
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        self._audit(
            "backup", {"file": destination.name, "managed_files": len(files)}
        )
        return destination

    def restore(self, backup_name: str) -> Dict:
        source = (self.backup_dir / Path(backup_name).name).resolve()
        if source.parent != self.backup_dir.resolve() or not source.is_file():
            raise FileNotFoundError("backup not found")
        safety_backup = self.backup()
        with tempfile.TemporaryDirectory(prefix="miniclaw-restore-") as directory:
            temporary = Path(directory)
            with zipfile.ZipFile(source, "r") as archive:
                names = set(archive.namelist())
                if "manifest.json" not in names:
                    raise ValueError("backup has no manifest")
                for info in archive.infolist():
                    _validate_archive_entry(info)
                archive.extractall(temporary)
            manifest = json.loads((temporary / "manifest.json").read_text(encoding="utf-8"))
            for name, expected_hash in manifest.get("files", {}).items():
                restored = temporary / name
                if not restored.is_file() or _sha256(restored) != expected_hash:
                    raise ValueError(f"backup checksum failed for {name}")
            expected_names = set(manifest.get("files", {})) | {"manifest.json"}
            if names != expected_names:
                raise ValueError("backup contains files not declared by the manifest")
            database = temporary / self.experience_store.path.name
            if database.is_file():
                ExperienceStore(database)  # schema validation and forward migration
                os.replace(database, self.experience_store.path)
            traces = temporary / self.trace_store.path.name
            if traces.is_file():
                self.trace_store.path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(traces, self.trace_store.path)
            source_database = temporary / "source-evolution.db"
            if source_database.is_file():
                SourceProposalStore(source_database)
                SourceExperimentStore(source_database)
                os.replace(source_database, self.root / "source-evolution.db")
            restored_directories = []
            for name in ("skill-drafts", "executable-policies"):
                restored = temporary / name
                if restored.is_dir():
                    _replace_directory(restored, self.root / name)
                    restored_directories.append(name)
            installed_skills = temporary / "installed-skills"
            if installed_skills.is_dir():
                skills_root = self.root.parent.parent / "skills"
                for restored in sorted(installed_skills.iterdir()):
                    if not restored.is_dir() or not re.fullmatch(
                        r"[a-z0-9]+(?:-[a-z0-9]+)*", restored.name
                    ):
                        raise ValueError("backup contains an invalid generated skill name")
                    _replace_directory(restored, skills_root / restored.name)
                    restored_directories.append(f"installed-skills/{restored.name}")
            restored_audit = temporary / self.audit_path.name
            if restored_audit.is_file():
                os.replace(restored_audit, self.audit_path)
        self._audit(
            "restore",
            {
                "file": source.name,
                "pre_restore_backup": safety_backup.name,
                "directories": restored_directories,
            },
        )
        return {
            "restored": source.name,
            "pre_restore_backup": safety_backup.name,
            "directories": restored_directories,
        }

    def purge(self, confirmation: str) -> Dict:
        if confirmation != self.CONFIRM_PURGE:
            raise PermissionError("purge confirmation phrase does not match")
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        trash = self.root.with_name(f"{self.root.name}.deleted-{timestamp}-{time.time_ns()}")
        if self.root.exists():
            self.root.rename(trash)
        self.root.mkdir(parents=True, exist_ok=True)
        ExperienceStore(self.experience_store.path)
        self._audit("purge", {"recoverable_from": str(trash)})
        return {"recoverable": True, "trash_path": str(trash)}

    def managed_artifacts(self) -> List[Dict]:
        rows = []
        for name, path in self._managed_files():
            rows.append({
                "path": name,
                "kind": _artifact_kind(name),
                "size": path.stat().st_size,
                "modified_at": path.stat().st_mtime,
            })
        return rows

    def cleanup(
        self,
        *,
        retention_days: int = 90,
        dry_run: bool = True,
        confirmation: str = "",
        source_repo: Path | str | None = None,
        now: float | None = None,
    ) -> Dict:
        if not dry_run and confirmation != self.CONFIRM_CLEANUP:
            raise PermissionError("cleanup confirmation phrase does not match")
        timestamp = time.time() if now is None else float(now)
        cutoff = timestamp - max(1, int(retention_days)) * 86400
        targets = self._cleanup_targets(cutoff, source_repo)
        removed = []
        if not dry_run:
            for target in targets:
                path = Path(target["absolute_path"])
                if target["kind"] == "source-worktree":
                    _remove_git_worktree(Path(source_repo).resolve(), path)
                else:
                    _remove_managed_directory(path, Path(target["managed_root"]))
                removed.append(target["path"])
        report = {
            "dry_run": dry_run,
            "retention_days": max(1, int(retention_days)),
            "targets": [
                {key: value for key, value in item.items() if key not in {"absolute_path", "managed_root"}}
                for item in targets
            ],
            "removed": removed,
        }
        if not dry_run:
            self._audit("cleanup", report)
        return report

    def _managed_files(self) -> List[tuple[str, Path]]:
        files: Dict[str, Path] = {}
        direct = (
            (self.experience_store.path.name, self.experience_store.path),
            (self.trace_store.path.name, self.trace_store.path),
            (self.audit_path.name, self.audit_path),
            ("source-evolution.db", self.root / "source-evolution.db"),
        )
        for name, path in direct:
            if path.is_file():
                files[name] = path
        for directory_name in ("skill-drafts", "executable-policies"):
            root = self.root / directory_name
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    continue
                if path.is_file():
                    name = f"{directory_name}/{path.relative_to(root).as_posix()}"
                    files[name] = path
        drafts_root = self.root / "skill-drafts"
        skills_root = self.root.parent.parent / "skills"
        if drafts_root.is_dir():
            for metadata in drafts_root.glob("*/metadata.json"):
                try:
                    payload = json.loads(metadata.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                name = str(payload.get("name", ""))
                if (
                    payload.get("status") != "approved"
                    or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                ):
                    continue
                installed = skills_root / name
                if not installed.is_dir() or installed.is_symlink():
                    continue
                for path in sorted(installed.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        relative = path.relative_to(installed).as_posix()
                        files[f"installed-skills/{name}/{relative}"] = path
        return sorted(files.items())

    def _cleanup_targets(
        self, cutoff: float, source_repo: Path | str | None
    ) -> List[Dict]:
        targets: List[Dict] = []
        for root, kind in (
            (self.root / "skill-drafts", "skill-draft"),
            (self.root / "executable-policies" / "drafts", "executable-policy-draft"),
        ):
            if not root.is_dir():
                continue
            for metadata in root.glob("*/metadata.json"):
                try:
                    payload = json.loads(metadata.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                status = str(payload.get("status", "draft"))
                created_at = float(payload.get("created_at", metadata.stat().st_mtime))
                if status == "approved" or created_at >= cutoff:
                    continue
                targets.append({
                    "kind": kind,
                    "path": metadata.parent.relative_to(self.root).as_posix(),
                    "status": status,
                    "created_at": created_at,
                    "absolute_path": str(metadata.parent.resolve()),
                    "managed_root": str(root.resolve()),
                })
        repo = Path(source_repo).resolve() if source_repo else None
        database = self.root / "source-evolution.db"
        worktrees_root = self.root / "source-evolution" / "worktrees"
        if repo is not None and (repo / ".git").exists() and database.is_file():
            for experiment in SourceExperimentStore(database).list(limit=10000):
                status = str(experiment.get("status", ""))
                updated_at = float(experiment.get("updated_at", 0))
                worktree = Path(str(experiment.get("worktree", ""))).resolve()
                try:
                    relative = worktree.relative_to(worktrees_root.resolve())
                except ValueError:
                    continue
                if (
                    status not in {"failed", "promoted", "rolled_back"}
                    or updated_at >= cutoff
                    or not worktree.is_dir()
                ):
                    continue
                targets.append({
                    "kind": "source-worktree",
                    "path": f"source-evolution/worktrees/{relative.as_posix()}",
                    "status": status,
                    "created_at": updated_at,
                    "absolute_path": str(worktree),
                    "managed_root": str(worktrees_root.resolve()),
                })
        return sorted(targets, key=lambda item: (item["kind"], item["path"]))

    def list_backups(self) -> List[Dict]:
        if not self.backup_dir.exists():
            return []
        return [
            {"name": path.name, "size": path.stat().st_size, "modified_at": path.stat().st_mtime}
            for path in sorted(self.backup_dir.glob("*.zip"), reverse=True)
        ]

    def audit_events(self, limit: int = 100) -> List[Dict]:
        if not self.audit_path.exists():
            return []
        rows = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def _audit(self, action: str, detail: Dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        event = {"action": action, "detail": detail, "timestamp": time.time()}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_archive_entry(info: zipfile.ZipInfo) -> None:
    name = info.filename.replace("\\", "/")
    path = Path(name)
    mode = (info.external_attr >> 16) & 0xFFFF
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or (path.drive and path.drive != "")
        or stat.S_ISLNK(mode)
    ):
        raise ValueError("backup contains unsafe paths")


def _replace_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = destination.with_name(
        f"{destination.name}.restore-old-{uuid.uuid4().hex}"
    )
    moved_previous = False
    try:
        if destination.exists():
            destination.rename(previous)
            moved_previous = True
        source.rename(destination)
    except Exception:
        if moved_previous and previous.exists() and not destination.exists():
            previous.rename(destination)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def _artifact_kind(name: str) -> str:
    if name.endswith(".db"):
        return "database"
    if name.endswith(".jsonl"):
        return "event-log"
    if name.startswith("skill-drafts/"):
        return "skill-draft"
    if name.startswith("installed-skills/"):
        return "installed-skill"
    if name.startswith("executable-policies/"):
        return "executable-policy"
    return "managed-file"


def _remove_managed_directory(path: Path, managed_root: Path) -> None:
    resolved = path.resolve()
    root = managed_root.resolve()
    if resolved == root or root not in resolved.parents or path.is_symlink():
        raise PermissionError("cleanup target is outside its managed artifact root")
    if resolved.is_dir():
        shutil.rmtree(resolved)


def _remove_git_worktree(repo: Path, worktree: Path) -> None:
    completed = subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip() or "failed to remove managed source worktree"
        )


def _collect_secrets(value: Any) -> set[str]:
    secrets: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            secrets.update(_collect_secrets(item))
    elif isinstance(value, list):
        for item in value:
            secrets.update(_collect_secrets(item))
    elif isinstance(value, str):
        secrets.update(
            match.group(2)
            for match in re.finditer(
                r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*([^\s,;]+)",
                value,
            )
        )
        secrets.update(
            match.group(1)
            for match in re.finditer(r"(?i)bearer\s+([a-z0-9._-]+)", value)
        )
    return {secret for secret in secrets if len(secret) >= 4}


def _redact_value(value: Any, secrets: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: _redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, str):
        value = re.sub(
            r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*[^\s,;]+",
            r"\1=[REDACTED]",
            value,
        )
        value = re.sub(r"(?i)bearer\s+[a-z0-9._-]+", "Bearer [REDACTED]", value)
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return value
    return value
