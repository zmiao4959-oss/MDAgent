"""Prepare inputs, submit once, check startup, and query status on demand."""
from __future__ import annotations

import hashlib
import re
import shutil
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ..settings import SimulationConfig
from .backends import SimulationBackend, create_backend
from .remote import RemoteError
from .store import RunStore


TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


def _relative_file(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("input paths must be non-empty strings")
    normalized = value.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (not parts or PurePosixPath(normalized).is_absolute() or
            any(part in ("", ".", "..") for part in parts) or
            ":" in normalized or "\n" in normalized or "\r" in normalized):
        raise ValueError(f"unsafe relative path: {value}")
    return PurePosixPath(*parts).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(project: Path) -> dict[str, Any]:
    path = project / "simulation.yaml"
    if not path.is_file():
        raise ValueError(f"simulation.yaml not found in {project}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("simulation.yaml must be a mapping")
    engine = raw.get("engine")
    if engine not in {"lammps", "gpumd"}:
        raise ValueError("simulation.yaml engine must be lammps or gpumd")
    entry = _relative_file(raw.get("entry"))
    if engine == "gpumd" and entry != "run.in":
        raise ValueError("GPUMD entry must be run.in at the project root")
    inputs = raw.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("simulation.yaml needs a non-empty inputs list")
    files = sorted({_relative_file(item) for item in inputs} | {entry, "simulation.yaml"})
    if {"job.slurm", "job.lsf", "launch.json", ".miniclaw-job-id", ".miniclaw-exit.json"}.intersection(files):
        raise ValueError("simulation inputs contain a reserved launcher or status filename")
    outputs = raw.get("expected_outputs", [])
    if not isinstance(outputs, list):
        raise ValueError("expected_outputs must be a list")
    return {"engine": engine, "entry": entry, "inputs": files,
            "expected_outputs": [_relative_file(item) for item in outputs]}


def _project_path(workspace: Path, project_dir: str) -> Path:
    if not isinstance(project_dir, str) or not project_dir.strip():
        raise ValueError("project_dir must be a directory")
    root = workspace.resolve()
    candidate = Path(project_dir)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not path.is_relative_to(root) or not path.is_dir():
        raise ValueError("project_dir must be an existing directory inside the active workspace")
    return path


def _inputs(project: Path, names: list[str]) -> list[tuple[str, Path]]:
    result = []
    for name in names:
        candidate = project / name
        if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != project):
            raise ValueError(f"symlink input is not allowed: {name}")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(project) or not resolved.is_file():
            raise ValueError(f"input file missing or outside project: {name}")
        result.append((name, resolved))
    return result


def _public(record: dict[str, Any]) -> dict[str, Any]:
    keys = ("run_id", "status", "target", "engine", "scheduler", "project_dir", "remote_dir", "job_id",
            "created_at", "updated_at", "entry", "files", "expected_outputs",
            "scheduler_state", "reason", "exit_code", "last_error", "log_tail")
    result = {key: record[key] for key in keys if key in record}
    if record.get("scheduler") == "local":
        result["run_dir"] = result.pop("remote_dir")
        if record.get("working_dir"):
            result["log_dir"] = result["run_dir"]
            result["run_dir"] = record["working_dir"]
    return result


class SimulationService:
    def __init__(self, settings: SimulationConfig, home: Path, *, store: RunStore | None = None):
        self.settings = settings
        self.home = Path(home)
        self.store = store or RunStore(self.home / "simulations" / "runs.sqlite3")

    def _target(self, name: str, raw: dict[str, Any] | None = None, *, run_home: Path | None = None) -> SimulationBackend:
        target_raw = raw if raw is not None else self.settings.targets.get(name)
        if target_raw is None:
            raise ValueError(f"simulation target '{name}' is not configured")
        if not target_raw.get("enabled", True):
            raise ValueError(f"simulation target '{name}' is disabled until its paths are configured")
        return create_backend(name, target_raw, run_home if run_home is not None else self.home)

    def prepare_local(self, input_file: str, target: str, *, workspace: Path) -> dict[str, Any]:
        """Prepare a local launcher; execute beside the original input without a manifest."""
        if not self.settings.enabled:
            raise ValueError("simulation submission is disabled in config")
        if not isinstance(input_file, str) or not input_file.strip():
            raise ValueError("input_file must be an input script path")
        root = workspace.resolve()
        path = Path(input_file)
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("input_file must be an existing file inside the active workspace")
        project = path.parent
        run_home = project / ".miniclaw"
        if not (run_home / "simulations").resolve().is_relative_to(project):
            raise ValueError("local run directory must remain inside the project")
        if (self.settings.targets.get(target) or {}).get("scheduler") != "local":
            raise ValueError("prepare_local requires a local target")
        backend = self._target(target, run_home=run_home)
        run_id = uuid.uuid4().hex[:16]
        script = backend.render_script(run_id=run_id, entry=path.name, working_dir=str(project))
        folder = run_home / "simulations" / run_id
        launcher = folder / "input" / backend.script_name
        launcher.parent.mkdir(parents=True, exist_ok=False)
        launcher.write_text(script, encoding="utf-8")
        record = {
            "run_id": run_id, "status": "PREPARED", "target": target,
            "engine": backend.engine, "scheduler": "local",
            "target_config": dict(self.settings.targets[target]),
            "project_dir": str(project), "working_dir": str(project),
            "remote_dir": backend.remote_dir(run_id), "local_dir": str(folder),
            "entry": path.name,
            "files": [{"path": path.name, "size": path.stat().st_size, "sha256": _sha256(path)}],
            "script_name": backend.script_name, "script_sha256": _sha256(launcher),
            "expected_outputs": [],
        }
        self.store.create(record)
        return _public(self.store.get(run_id) or record)

    def prepare(
        self, project_dir: str, target: str, *, workspace: Path,
    ) -> dict[str, Any]:
        if not self.settings.enabled:
            raise ValueError("simulation submission is disabled in config")
        project = _project_path(workspace, project_dir)
        manifest = _load_manifest(project)
        sources = _inputs(project, manifest["inputs"])
        # Keep local outputs inside the project so the Agent's file tools can read them.
        raw_target = self.settings.targets.get(target) or {}
        run_home = project / ".miniclaw" if raw_target.get("scheduler") == "local" else self.home
        if raw_target.get("scheduler") == "local" and not (run_home / "simulations").resolve().is_relative_to(project):
            raise ValueError("local run directory must remain inside the project")
        target_obj = self._target(target, run_home=run_home)
        if manifest["engine"] != target_obj.engine:
            raise ValueError(f"target '{target}' runs {target_obj.engine}, not {manifest['engine']}")
        run_id = uuid.uuid4().hex[:16]
        folder = run_home / "simulations" / run_id
        snapshot = folder / "input"
        snapshot.mkdir(parents=True, exist_ok=False)
        try:
            files = []
            for relative, source in sources:
                destination = snapshot / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                sha = _sha256(destination)
                if sha != _sha256(source):
                    raise ValueError(f"input changed while preparing: {relative}")
                files.append({"path": relative, "size": destination.stat().st_size, "sha256": sha})
            script = target_obj.render_script(run_id=run_id, entry=manifest["entry"])
            script_path = snapshot / target_obj.script_name
            script_path.write_text(script, encoding="utf-8", newline="\n")
            with tarfile.open(folder / "input.tar", "w") as archive:
                for item in files:
                    archive.add(snapshot / item["path"], arcname=item["path"], recursive=False)
                archive.add(script_path, arcname=target_obj.script_name, recursive=False)
            record = {
                "run_id": run_id,
                "status": "PREPARED",
                "target": target,
                "engine": manifest["engine"],
                "scheduler": self.settings.targets[target]["scheduler"],
                "target_config": dict(self.settings.targets[target]),
                "project_dir": str(project),
                "remote_dir": target_obj.remote_dir(run_id),
                "local_dir": str(folder),
                "entry": manifest["entry"],
                "files": files,
                "script_sha256": _sha256(script_path),
                "script_name": target_obj.script_name,
                "archive_sha256": _sha256(folder / "input.tar"),
                "expected_outputs": manifest["expected_outputs"],
            }
            self.store.create(record)
            result = _public(self.store.get(run_id) or record)
            result["launch_preview" if record["scheduler"] == "local" else "submit_script"] = script
            return result
        except Exception:
            if folder.resolve().is_relative_to((run_home / "simulations").resolve()):
                shutil.rmtree(folder)
            raise

    def submit(self, run_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{16}", run_id):
            raise ValueError("invalid run_id")
        record = self.store.get(run_id)
        if record is None:
            raise ValueError("unknown run_id")
        if record["status"] != "PREPARED":
            return _public(self.refresh(run_id) if record["status"] in {"SUBMITTING", "SUBMITTED", "UNKNOWN"} else record)
        if record["target_config"] != self.settings.targets.get(record["target"]):
            raise ValueError("target configuration changed since preparation; prepare a new run")
        self._target(record["target"])
        folder = Path(record["local_dir"])
        for item in record["files"]:
            path = (Path(record["working_dir"]) if record.get("working_dir") else folder / "input") / item["path"]
            if not path.is_file() or _sha256(path) != item["sha256"]:
                raise ValueError(f"prepared input was changed or removed: {item['path']}")
        if _sha256(folder / "input" / record.get("script_name", "job.slurm")) != record["script_sha256"]:
            raise ValueError("prepared submission script was changed")
        if not record.get("working_dir") and _sha256(folder / "input.tar") != record["archive_sha256"]:
            raise ValueError("prepared input archive was changed")
        claimed = self.store.update(run_id, {"status": "SUBMITTING", "last_error": ""}, expected={"PREPARED"})
        if claimed is None:
            raise ValueError("this prepared run is already being submitted")
        target = self._target(record["target"], record["target_config"])
        try:
            target.stage(record["remote_dir"], folder / "input.tar")
        except RemoteError as exc:
            self.store.update(run_id, {"status": "PREPARED", "last_error": str(exc)}, expected={"SUBMITTING"})
            raise
        try:
            job_id = target.submit(record["remote_dir"])
        except RemoteError as exc:
            self.store.update(run_id, {"last_error": str(exc)})
        else:
            # A returned ID alone does not prove that the scheduler accepted/started the job.
            self.store.update(run_id, {"status": "SUBMITTED", "job_id": job_id, "last_error": ""})
        if self.settings.submission_check_delay_sec:
            time.sleep(self.settings.submission_check_delay_sec)
        return _public(self.refresh(run_id))

    def refresh(self, run_id: str) -> dict[str, Any]:
        record = self.store.get(run_id)
        if record is None:
            raise ValueError("unknown run_id")
        if record["status"] in TERMINAL_STATES or record["status"] == "PREPARED":
            return record
        target = self._target(record["target"], record["target_config"])
        job_id = record.get("job_id")
        try:
            if not job_id:
                job_id = target.recover_job_id(
                    record["remote_dir"], run_id, record["created_at"]
                )
                if not job_id:
                    return self.store.update(run_id, {
                        "status": "UNKNOWN",
                        "last_error": record.get("last_error") or "job ID not yet recoverable; no second submission was made",
                    }) or record
                self.store.update(run_id, {"job_id": job_id})
            info = target.status(job_id, record["remote_dir"])
            scheduler_state = info["scheduler_state"].split()[0].split("+")[0].upper()
            exit_code = info.get("exit_code", "")
            if scheduler_state in {"PENDING", "CONFIGURING", "SUSPENDED", "RESV_DEL_HOLD", "PEND", "PSUSP", "USUSP", "SSUSP"}:
                status = "PENDING"
            elif scheduler_state in {"RUNNING", "COMPLETING", "STAGE_OUT", "RUN"}:
                status = "RUNNING"
            elif (scheduler_state == "COMPLETED" and exit_code == "0:0") or scheduler_state == "DONE":
                status = "SUCCEEDED"
            elif scheduler_state.startswith("CANCELLED"):
                status = "CANCELLED"
            elif scheduler_state in {"FAILED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED", "BOOT_FAIL", "DEADLINE", "EXIT"}:
                status = "FAILED"
            elif scheduler_state == "COMPLETED" and exit_code:
                status = "FAILED"
            else:
                status = "UNKNOWN"
            reason = info.get("reason", "")
            if status == "SUCCEEDED":
                missing = target.missing_outputs(record["remote_dir"], record["expected_outputs"])
                if missing:
                    status = "FAILED"
                    reason = "expected output missing: " + ", ".join(missing)
            changes: dict[str, Any] = {
                "status": status,
                "job_id": job_id,
                "scheduler_state": info["scheduler_state"],
                "reason": reason,
                "exit_code": exit_code,
                "last_error": "",
            }
            if status in TERMINAL_STATES:
                try:
                    changes["log_tail"] = target.log_tail(record["remote_dir"], job_id)
                except RemoteError:
                    pass
            return self.store.update(run_id, changes) or record
        except RemoteError as exc:
            return self.store.update(run_id, {"status": "UNKNOWN", "last_error": str(exc)}) or record

    def get(self, run_id: str, *, refresh: bool = True) -> dict[str, Any]:
        record = self.refresh(run_id) if refresh else self.store.get(run_id)
        if record is None:
            raise ValueError("unknown run_id")
        return _public(record)
