"""Background local LAMMPS/GPUMD adapter with durable run records."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

from .remote import RemoteError, positive
from .local_queue import LocalGpuQueue
from .local_process import child_is_alive, worker_is_alive


class LocalLaunchError(RemoteError):
    """The OS rejected process creation; no simulation could have started."""


class LocalTarget:
    script_name = "launch.json"

    def __init__(self, name: str, raw: dict[str, Any], home: Path):
        if raw.get("scheduler") != "local" or raw.get("engine") not in {"lammps", "gpumd"}:
            raise ValueError("local target needs scheduler: local and engine: lammps or gpumd")
        self.name = name
        self.engine = raw["engine"]
        self.home = Path(home)
        self.gpu_queue = {"database": str(self.home / "simulations" / "local-gpu-queue.sqlite3"),
                          "runs_database": str(self.home / "simulations" / "runs.sqlite3")}
        self.executable = str(raw.get("executable", ""))
        if not self.executable:
            raise ValueError("local executable must be configured")
        self.cores = positive(raw.get("cores", 1), "cores")
        self.mpi_launcher = str(raw.get("mpi_launcher", "mpiexec"))
        self.wsl_distribution = str(raw.get("wsl_distribution", ""))
        if self.engine == "gpumd" and (not self.wsl_distribution or not self.executable.startswith("/")):
            raise ValueError("local GPUMD needs a WSL distribution and absolute Linux executable")

    def remote_dir(self, run_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{16}", run_id):
            raise ValueError("invalid run_id")
        return str(self.home / "simulations" / run_id / "input")

    def _argv(self, entry: str, workdir: str) -> list[str]:
        if self.engine == "lammps":
            return [self.mpi_launcher, "-np", str(self.cores), self.executable, "-in", entry]
        if entry != "run.in":
            raise ValueError("GPUMD entry must be run.in")
        return ["wsl.exe", "-d", self.wsl_distribution, "--cd", workdir, "-e",
                "/bin/bash", "-lic", 'exec "$@"', "bash", self.executable]

    def render_script(self, *, run_id: str, entry: str, working_dir: str | None = None) -> str:
        settings = {"argv": self._argv(entry, working_dir or self.remote_dir(run_id))}
        if working_dir is not None:
            settings["working_dir"] = working_dir
        if self.engine == "gpumd":
            settings["gpu_queue"] = {**self.gpu_queue, "run_id": run_id}
        return json.dumps(settings, ensure_ascii=False, indent=2)

    def stage(self, remote_dir: str, archive: Path) -> None:
        # Launcher metadata lives separately from the simulation working directory.
        settings = json.loads((Path(remote_dir) / self.script_name).read_text(encoding="utf-8"))
        if self.engine == "gpumd" and not settings.get("gpu_queue"):
            raise RemoteError("GPUMD launcher predates GPU queue support; prepare a new run before submitting")
        argv = settings["argv"]
        for binary in (argv[0], argv[3] if self.engine == "lammps" else ""):
            if binary and not (Path(binary).is_file() or shutil.which(binary)):
                raise RemoteError(f"local executable not found: {binary}")

    def submit(self, remote_dir: str) -> str:
        workdir = Path(remote_dir)
        if (workdir / ".miniclaw-job-id").exists():
            job_id = (workdir / ".miniclaw-job-id").read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[0-9]+", job_id):
                return job_id
        queue = None
        queue_settings = None
        if self.engine == "gpumd":
            settings = json.loads((workdir / self.script_name).read_text(encoding="utf-8"))
            queue_settings = settings.get("gpu_queue")
            if not queue_settings:
                raise RemoteError("GPUMD launcher predates GPU queue support; prepare a new run before submitting")
            queue = LocalGpuQueue(queue_settings["database"])
            queue.register(queue_settings["run_id"], workdir)
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP |
                 subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("local_worker.py")), str(workdir / self.script_name)],
                cwd=workdir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            if queue is not None:
                queue.abandon(queue_settings["run_id"])
            raise LocalLaunchError(f"local launch failed: {exc}") from exc
        return str(process.pid)

    def recover_job_id(self, remote_dir: str, run_id: str, created_at: str) -> str | None:
        marker = Path(remote_dir) / ".miniclaw-job-id"
        if not marker.is_file():
            return None
        job_id = marker.read_text(encoding="utf-8").strip()
        return job_id if re.fullmatch(r"[0-9]+", job_id) else None

    def status(self, job_id: str, remote_dir: str | None = None) -> dict[str, Any]:
        if remote_dir is None:
            raise ValueError("local status requires the run directory")
        workdir = Path(remote_dir)
        marker = workdir / ".miniclaw-exit.json"
        if marker.is_file():
            result = json.loads(marker.read_text(encoding="utf-8"))
            code = int(result.get("exit_code", -1))
            return {"source": "local", "scheduler_state": "COMPLETED" if code == 0 else "FAILED",
                    "exit_code": f"{code}:0", "reason": str(result.get("error", ""))}
        try:
            if worker_is_alive(workdir, job_id) is True:
                settings = json.loads((workdir / self.script_name).read_text(encoding="utf-8"))
                queue_settings = settings.get("gpu_queue")
                if queue_settings:
                    info = LocalGpuQueue(queue_settings["database"]).status(
                        queue_settings["run_id"], queue_settings["runs_database"])
                    if info["scheduler_state"] != "DONE":
                        return info
                return {"source": "local", "scheduler_state": "RUNNING"}
        except (psutil.Error, ValueError, OSError):
            pass
        settings = json.loads((workdir / self.script_name).read_text(encoding="utf-8"))
        if settings.get("gpu_queue"):
            queue_settings = settings["gpu_queue"]
            info = LocalGpuQueue(queue_settings["database"]).status(
                queue_settings["run_id"], queue_settings["runs_database"])
            if info["scheduler_state"] in {"UNKNOWN", "FAILED"}:
                return info
        if self.engine == "gpumd" and worker_is_alive(workdir, job_id) is False:
            child = child_is_alive(workdir)
            if child is False:
                return {"source": "local", "scheduler_state": "FAILED",
                        "reason": "GPU worker and simulation process are absent; exit code unknown; slot released"}
            if child is True:
                return {"source": "local", "scheduler_state": "RUNNING",
                        "reason": "GPU worker is absent but its simulation process is still running"}
        return {"source": "local", "scheduler_state": "UNKNOWN",
                "reason": "local worker is absent and no exit marker was written"}

    def missing_outputs(self, remote_dir: str, paths: list[str]) -> list[str]:
        settings = json.loads((Path(remote_dir) / self.script_name).read_text(encoding="utf-8"))
        workdir = Path(settings.get("working_dir", remote_dir))
        return [path for path in paths if not (workdir / path).exists()]

    def log_tail(self, remote_dir: str, job_id: str, *, lines: int = 30) -> str:
        chunks = []
        for suffix in ("err", "out"):
            path = Path(remote_dir) / f"{job_id}.{suffix}"
            if path.is_file():
                chunks.append(f"== {path.name} ==\n" + "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]))
        return "\n".join(chunks)[-4000:]
