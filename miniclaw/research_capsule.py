"""Portable, integrity-addressed research capsules for project workspaces."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from .learning.trace import EvolutionTrace

if TYPE_CHECKING:
    from .memory.session import Session


CAPSULE_SCHEMA_VERSION = 1
CAPSULES_RELATIVE_ROOT = Path(".miniclaw") / "research-capsules"
HASH_CHUNK_BYTES = 1024 * 1024
MAX_EXPORT_FILE_BYTES = 8 * 1024 * 1024

SKIP_DIR_NAMES = frozenset({
    ".git", ".miniclaw", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "node_modules", "__pycache__",
})
SENSITIVE_FILE_NAMES = frozenset({
    ".env", ".env.local", ".env.production", "credentials.json",
    "secrets.json", "token.json", "auth.json", "id_rsa", "id_ed25519",
})
SENSITIVE_DIR_NAMES = frozenset({".ssh", ".aws", ".azure", ".gnupg"})
SENSITIVE_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})
SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "access_token", "refresh_token", "auth_token",
    "authorization", "password", "passwd", "secret", "credential",
    "credentials", "cookie", "bot_token", "token",
})


@dataclass(frozen=True)
class CapsuleFile:
    path: str
    size: int
    sha256: str
    modified_at: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CapsuleVerification:
    capsule_id: str
    verified_at: float
    status: str
    expected_file_count: int
    checked_file_count: int
    unchanged: List[str] = field(default_factory=list)
    changed: List[Dict[str, Any]] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    unreadable: List[Dict[str, str]] = field(default_factory=list)
    added: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapsuleExport:
    path: Path
    filename: str
    file_count: int
    redacted_file_count: int


class CapsuleIntegrityError(RuntimeError):
    """Raised when a capsule no longer matches its workspace."""

    def __init__(self, report: CapsuleVerification) -> None:
        super().__init__(f"capsule integrity status is {report.status}")
        self.report = report


@dataclass
class ResearchCapsule:
    capsule_id: str
    project_id: str
    title: str
    objective: str
    chat_id: str
    created_at: float
    files: List[CapsuleFile]
    skipped_files: List[Dict[str, str]] = field(default_factory=list)
    conversation: Dict[str, Any] = field(default_factory=dict)
    trace: Dict[str, Any] = field(default_factory=dict)
    environment: Dict[str, Any] = field(default_factory=dict)
    source: Dict[str, Any] = field(default_factory=dict)
    manifest_sha256: str = ""
    schema_version: int = CAPSULE_SCHEMA_VERSION
    status: str = "captured"

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["file_count"] = self.file_count
        payload["total_bytes"] = self.total_bytes
        return payload

    def to_summary(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "capsule_id": self.capsule_id,
            "project_id": self.project_id,
            "title": self.title,
            "created_at": self.created_at,
            "status": self.status,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "skipped_file_count": len(self.skipped_files),
            "manifest_sha256": self.manifest_sha256,
            "trace_run_id": self.trace.get("run_id", ""),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchCapsule":
        payload = dict(data)
        payload.pop("file_count", None)
        payload.pop("total_bytes", None)
        payload["files"] = [CapsuleFile(**item) for item in payload.get("files", [])]
        return cls(**payload)


class ResearchCapsuleManager:
    """Capture and persist deterministic manifests for one project workspace."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        source_root: Path | str | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.source_root = (
            Path(source_root).expanduser().resolve()
            if source_root is not None
            else Path(__file__).resolve().parent.parent
        )
        self.clock = clock
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex[:16])
        self.root = self.workspace / CAPSULES_RELATIVE_ROOT

    def capture(
        self,
        *,
        project_id: str,
        title: str,
        objective: str,
        chat_id: str,
        session: Optional["Session"] = None,
        trace: Optional[EvolutionTrace] = None,
    ) -> ResearchCapsule:
        if not self.workspace.is_dir():
            raise FileNotFoundError(f"project workspace does not exist: {self.workspace}")

        files, skipped = self._capture_files()
        capsule = ResearchCapsule(
            capsule_id=f"capsule:{self.id_factory()}",
            project_id=project_id,
            title=str(_redact_value((title or "Untitled research").strip()[:200])),
            objective=str(_redact_value((objective or "").strip()[:4_000])),
            chat_id=chat_id,
            created_at=float(self.clock()),
            files=files,
            skipped_files=skipped,
            conversation=_conversation_summary(session),
            trace=_trace_summary(trace),
            environment=_environment_summary(),
            source=_source_summary(self.source_root),
        )
        capsule.manifest_sha256 = _manifest_digest(capsule.files)
        self._write_capsule(capsule)
        return capsule

    def list_capsules(self) -> List[ResearchCapsule]:
        if not self.root.is_dir():
            return []
        capsules: List[ResearchCapsule] = []
        for manifest in sorted(self.root.glob("*/capsule.json")):
            capsule = self._read_manifest(manifest)
            if capsule is not None:
                capsules.append(capsule)
        return sorted(capsules, key=lambda item: item.created_at, reverse=True)

    def get_capsule(self, capsule_id: str) -> Optional[ResearchCapsule]:
        safe_id = _safe_capsule_dir(capsule_id)
        capsule = self._read_manifest(self.root / safe_id / "capsule.json")
        if capsule is None or capsule.capsule_id != capsule_id:
            return None
        return capsule

    def get_verification(self, capsule_id: str) -> Optional[CapsuleVerification]:
        capsule = self.get_capsule(capsule_id)
        if capsule is None:
            return None
        path = self._capsule_dir(capsule_id) / "verification.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            return CapsuleVerification(**raw)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def verify(self, capsule_id: str) -> CapsuleVerification:
        capsule = self.get_capsule(capsule_id)
        if capsule is None:
            raise FileNotFoundError("capsule not found")

        expected = {item.path: item for item in capsule.files}
        unchanged: List[str] = []
        changed: List[Dict[str, Any]] = []
        missing: List[str] = []
        unreadable: List[Dict[str, str]] = []

        for relative, item in sorted(expected.items()):
            try:
                resolved = self._resolve_workspace_file(relative)
            except FileNotFoundError:
                missing.append(relative)
                continue
            except (OSError, ValueError) as exc:
                unreadable.append({"path": relative, "reason": _safe_error_reason(exc)})
                continue
            try:
                before = resolved.stat()
                digest = _sha256_file(resolved)
                after = resolved.stat()
            except OSError as exc:
                unreadable.append({"path": relative, "reason": _safe_error_reason(exc)})
                continue
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                unreadable.append({"path": relative, "reason": "changed_during_verification"})
            elif after.st_size != item.size or digest != item.sha256:
                changed.append({
                    "path": relative,
                    "expected_size": item.size,
                    "actual_size": after.st_size,
                    "expected_sha256": item.sha256,
                    "actual_sha256": digest,
                })
            else:
                unchanged.append(relative)

        current_files, skipped = self._capture_files()
        added = [
            {"path": item.path, "size": item.size, "sha256": item.sha256}
            for item in current_files
            if item.path not in expected
        ]
        if unreadable:
            status = "unreadable"
        elif missing:
            status = "missing"
        elif changed or added:
            status = "changed"
        else:
            status = "verified"
        report = CapsuleVerification(
            capsule_id=capsule_id,
            verified_at=float(self.clock()),
            status=status,
            expected_file_count=len(expected),
            checked_file_count=len(unchanged) + len(changed),
            unchanged=unchanged,
            changed=changed,
            missing=missing,
            unreadable=unreadable,
            added=added,
            skipped=skipped,
        )
        self._write_json_atomic(self._capsule_dir(capsule_id) / "verification.json", report.to_dict())
        return report

    def export_capsule(
        self,
        capsule_id: str,
        *,
        confirmation: str,
        include_files: bool = True,
    ) -> CapsuleExport:
        expected_confirmation = f"EXPORT {capsule_id}"
        if include_files and confirmation != expected_confirmation:
            raise ValueError(f"confirmation must exactly match: {expected_confirmation}")
        capsule = self.get_capsule(capsule_id)
        if capsule is None:
            raise FileNotFoundError("capsule not found")
        report = self.verify(capsule_id)
        if report.status != "verified":
            raise CapsuleIntegrityError(report)

        capsule_dir = self._capsule_dir(capsule_id)
        export_dir = capsule_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        suffix = _safe_capsule_dir(capsule_id).removeprefix("capsule_")
        filename = f"research-capsule-{suffix}.zip"
        destination = export_dir / filename
        temporary = export_dir / f".{filename}.tmp"
        entries: List[Dict[str, Any]] = []
        redacted_count = 0
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(capsule_dir / "capsule.json", "capsule.json")
                archive.write(capsule_dir / "README.md", "README.md")
                archive.write(capsule_dir / "verification.json", "verification.json")
                if include_files:
                    for item in capsule.files:
                        if item.size > MAX_EXPORT_FILE_BYTES:
                            raise ValueError(
                                f"file is too large for redacted export: {item.path}; "
                                "use manifest-only export"
                            )
                        source = self._resolve_workspace_file(item.path)
                        content = source.read_bytes()
                        if len(content) != item.size or _sha256_bytes(content) != item.sha256:
                            raise CapsuleIntegrityError(self.verify(capsule_id))
                        exported, redacted = _redact_export_content(content)
                        archive_path = f"files/{item.path}"
                        archive.writestr(archive_path, exported)
                        redacted_count += int(redacted)
                        entries.append({
                            "path": archive_path,
                            "source_sha256": item.sha256,
                            "export_sha256": _sha256_bytes(exported),
                            "source_size": item.size,
                            "export_size": len(exported),
                            "redacted": redacted,
                        })
                export_manifest = {
                    "schema_version": 1,
                    "capsule_id": capsule_id,
                    "exported_at": float(self.clock()),
                    "includes_files": include_files,
                    "integrity_status": report.status,
                    "redacted_file_count": redacted_count,
                    "files": entries,
                }
                archive.writestr(
                    "export.json",
                    json.dumps(export_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
            os.replace(temporary, destination)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return CapsuleExport(destination, filename, len(entries), redacted_count)

    def _capture_files(self) -> tuple[List[CapsuleFile], List[Dict[str, str]]]:
        files: List[CapsuleFile] = []
        skipped: List[Dict[str, str]] = []

        for dirpath, dirnames, filenames in os.walk(self.workspace, followlinks=False):
            directory = Path(dirpath)
            kept_dirs = []
            for name in sorted(dirnames):
                candidate = directory / name
                relative = candidate.relative_to(self.workspace).as_posix()
                if name in SKIP_DIR_NAMES:
                    continue
                if name.lower() in SENSITIVE_DIR_NAMES:
                    skipped.append({"path": relative, "reason": "sensitive_directory"})
                    continue
                if candidate.is_symlink():
                    skipped.append({"path": relative, "reason": "symlink_directory"})
                    continue
                try:
                    candidate.resolve(strict=True).relative_to(self.workspace)
                except (OSError, ValueError):
                    skipped.append({"path": relative, "reason": "outside_workspace_directory"})
                    continue
                kept_dirs.append(name)
            dirnames[:] = kept_dirs

            for name in sorted(filenames):
                path = directory / name
                relative_path = path.relative_to(self.workspace)
                relative = relative_path.as_posix()
                reason = _sensitive_file_reason(relative_path)
                if reason:
                    skipped.append({"path": relative, "reason": reason})
                    continue
                if path.is_symlink():
                    skipped.append({"path": relative, "reason": "symlink_file"})
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(self.workspace)
                    before = resolved.stat()
                    digest = _sha256_file(resolved)
                    after = resolved.stat()
                except (OSError, ValueError):
                    skipped.append({"path": relative, "reason": "unreadable_or_outside_workspace"})
                    continue
                if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                    skipped.append({"path": relative, "reason": "changed_during_capture"})
                    continue
                files.append(CapsuleFile(
                    path=relative,
                    size=after.st_size,
                    sha256=digest,
                    modified_at=after.st_mtime,
                ))

        files.sort(key=lambda item: item.path)
        skipped.sort(key=lambda item: (item["path"], item["reason"]))
        return files, skipped

    def _write_capsule(self, capsule: ResearchCapsule) -> None:
        capsule_dir = self._capsule_dir(capsule.capsule_id)
        capsule_dir.mkdir(parents=True, exist_ok=False)
        manifest = capsule_dir / "capsule.json"
        temporary = capsule_dir / "capsule.json.tmp"
        temporary.write_text(
            json.dumps(capsule.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, manifest)
        (capsule_dir / "README.md").write_text(_render_readme(capsule), encoding="utf-8")

    def _capsule_dir(self, capsule_id: str) -> Path:
        return self.root / _safe_capsule_dir(capsule_id)

    def _resolve_workspace_file(self, relative: str) -> Path:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("unsafe manifest path")
        candidate = self.workspace / relative_path
        if candidate.is_symlink():
            raise ValueError("symlink file")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(self.workspace)
        if not resolved.is_file():
            raise OSError("not a regular file")
        return resolved

    @staticmethod
    def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    @staticmethod
    def _read_manifest(path: Path) -> Optional[ResearchCapsule]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            return ResearchCapsule.from_dict(raw)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None


def latest_trace_for_chat(workspace: Path | str, chat_id: str) -> Optional[EvolutionTrace]:
    from .learning.trace import TraceStore

    trace_path = Path(workspace).resolve() / ".miniclaw" / "evolution" / "traces.jsonl"
    matches = [trace for trace in TraceStore(trace_path).recent(limit=1_000) if trace.chat_id == chat_id]
    if not matches:
        return None
    return max(matches, key=lambda item: item.ended_at or item.started_at)


def _conversation_summary(session: Optional["Session"]) -> Dict[str, Any]:
    if session is None:
        return {"message_count": 0, "roles": {}, "created_at": 0.0, "last_active": 0.0}
    roles: Dict[str, int] = {}
    for message in session.messages:
        roles[message.role] = roles.get(message.role, 0) + 1
    return {
        "message_count": len(session.messages),
        "roles": dict(sorted(roles.items())),
        "created_at": float(session.created_at),
        "last_active": float(session.last_active),
    }


def _trace_summary(trace: Optional[EvolutionTrace]) -> Dict[str, Any]:
    if trace is None:
        return {}
    return {
        "run_id": trace.run_id,
        "started_at": trace.started_at,
        "ended_at": trace.ended_at,
        "success": trace.success,
        "score": trace.score,
        "rounds": trace.rounds,
        "prompt_tokens": trace.prompt_tokens,
        "completion_tokens": trace.completion_tokens,
        "hit_max_rounds": trace.hit_max_rounds,
        "error_count": len(trace.errors),
        "tool_calls": [
            {
                "name": tool.name,
                "arguments": _redact_value(tool.arguments),
                "success": tool.success,
                "timestamp": tool.timestamp,
                "error_present": bool(tool.error),
            }
            for tool in trace.tool_calls
        ],
    }


def _environment_summary() -> Dict[str, Any]:
    packages: Dict[str, str] = {}
    for package in ("fastapi", "openai", "pyyaml", "uvicorn", "websockets"):
        try:
            packages[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            continue
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "packages": packages,
    }


def _source_summary(source_root: Path) -> Dict[str, Any]:
    def git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(source_root), *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    status = git("status", "--porcelain")
    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(status),
    }


def _manifest_digest(files: List[CapsuleFile]) -> str:
    canonical = [
        {"path": item.path, "size": item.size, "sha256": item.sha256}
        for item in files
    ]
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_error_reason(exc: BaseException) -> str:
    if isinstance(exc, ValueError):
        return str(exc)[:160] or "unsafe_path"
    return exc.__class__.__name__.lower()


def _redact_export_content(content: bytes) -> tuple[bytes, bool]:
    if b"\x00" in content:
        return content, False
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content, False
    redacted = str(_redact_value(text))
    if redacted == text:
        return content, False
    return redacted.encode("utf-8"), True


def _safe_capsule_dir(capsule_id: str) -> str:
    value = capsule_id.strip()
    if not value.startswith("capsule:"):
        raise ValueError("invalid capsule id")
    suffix = value.removeprefix("capsule:")
    if not suffix or not suffix.replace("-", "").replace("_", "").isalnum():
        raise ValueError("invalid capsule id")
    return f"capsule_{suffix}"


def _sensitive_file_reason(relative: Path) -> str:
    name = relative.name.lower()
    if name in SENSITIVE_FILE_NAMES or relative.suffix.lower() in SENSITIVE_SUFFIXES:
        return "sensitive_path"
    if name.startswith(".env.") or "credential" in name or "secret" in name:
        return "sensitive_path"
    return ""


def _redact_value(value: Any, key: str = "") -> Any:
    normalized_key = key.lower().replace("-", "_").strip()
    if normalized_key in SENSITIVE_KEYS or normalized_key.endswith(("_api_key", "_password", "_secret")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        value = re.sub(
            r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*[^\s,;]+",
            r"\1=[REDACTED]",
            value,
        )
        return re.sub(r"(?i)bearer\s+[a-z0-9._-]+", "Bearer [REDACTED]", value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return repr(value)


def _render_readme(capsule: ResearchCapsule) -> str:
    lines = [
        f"# Research Capsule: {capsule.title}",
        "",
        f"- Capsule ID: `{capsule.capsule_id}`",
        f"- Project ID: `{capsule.project_id}`",
        f"- Created at: `{capsule.created_at:.3f}`",
        f"- Files: {capsule.file_count}",
        f"- Total bytes: {capsule.total_bytes}",
        f"- Manifest SHA-256: `{capsule.manifest_sha256}`",
        "",
        "## Objective",
        "",
        capsule.objective or "No objective recorded.",
        "",
        "## Evidence",
        "",
        f"- Conversation messages: {capsule.conversation.get('message_count', 0)}",
        f"- Evolution trace: `{capsule.trace.get('run_id', 'not available')}`",
        f"- Source commit: `{capsule.source.get('commit', 'not available')}`",
        f"- Source dirty: `{capsule.source.get('dirty', False)}`",
        "",
        "## File manifest",
        "",
        "| Path | Bytes | SHA-256 |",
        "|---|---:|---|",
    ]
    for item in capsule.files[:200]:
        safe_path = item.path.replace("|", "\\|")
        lines.append(f"| `{safe_path}` | {item.size} | `{item.sha256}` |")
    if len(capsule.files) > 200:
        lines.extend(["", f"Only the first 200 of {len(capsule.files)} files are shown here. See `capsule.json` for the complete manifest."])
    if capsule.skipped_files:
        lines.extend(["", "## Skipped paths", ""])
        for item in capsule.skipped_files[:100]:
            lines.append(f"- `{item['path']}` — {item['reason']}")
    lines.append("")
    return "\n".join(lines)
