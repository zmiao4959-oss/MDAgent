"""Privacy, export, backup, restore, purge, and audit controls."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from .experience import ExperienceStore
from .trace import TraceStore


class GovernanceManager:
    CONFIRM_PURGE = "DELETE EVOLUTION DATA"

    def __init__(self, root: Path, experience_store: ExperienceStore, trace_store: TraceStore):
        self.root = root
        self.experience_store = experience_store
        self.trace_store = trace_store
        self.audit_path = root / "audit.jsonl"
        self.backup_dir = root / "backups"

    def export(self, *, redact: bool = True) -> Dict:
        payload = {
            "schema_version": 1,
            "exported_at": time.time(),
            "experiences": [item.__dict__.copy() for item in self.experience_store.all()],
            "evaluations": self.experience_store.evaluations(),
            "versions": self.experience_store.policy_versions(),
            "canary": self.experience_store.canary_stats(),
            "replays": self.experience_store.replay_reports(),
            "maintenance": self.experience_store.maintenance_reports(),
            "reflections": self.experience_store.reflections(None),
            "traces": [trace.to_dict() for trace in self.trace_store.recent(10000)],
        }
        secrets = _collect_secrets(payload) if redact else set()
        result = _redact_value(payload, secrets) if redact else payload
        self._audit("export", {"redacted": redact})
        return result

    def backup(self) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        destination = self.backup_dir / f"evolution-{timestamp}-{time.time_ns()}.zip"
        files = [
            path for path in (self.experience_store.path, self.trace_store.path, self.audit_path)
            if path.is_file()
        ]
        manifest = {
            "schema_version": 1,
            "created_at": time.time(),
            "files": {path.name: _sha256(path) for path in files},
        }
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, arcname=path.name)
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        self._audit("backup", {"file": destination.name})
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
                if "manifest.json" not in names or any(
                    Path(name).name != name for name in names
                ):
                    raise ValueError("backup contains unsafe paths")
                archive.extractall(temporary)
            manifest = json.loads((temporary / "manifest.json").read_text(encoding="utf-8"))
            for name, expected_hash in manifest.get("files", {}).items():
                restored = temporary / name
                if not restored.is_file() or _sha256(restored) != expected_hash:
                    raise ValueError(f"backup checksum failed for {name}")
            database = temporary / self.experience_store.path.name
            if database.is_file():
                ExperienceStore(database)  # schema validation and forward migration
                os.replace(database, self.experience_store.path)
            traces = temporary / self.trace_store.path.name
            if traces.is_file():
                os.replace(traces, self.trace_store.path)
        self._audit(
            "restore",
            {"file": source.name, "pre_restore_backup": safety_backup.name},
        )
        return {"restored": source.name, "pre_restore_backup": safety_backup.name}

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
