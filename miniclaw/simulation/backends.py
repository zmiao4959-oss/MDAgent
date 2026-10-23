"""Small registry for independently switchable simulation execution backends."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from .local import LocalTarget
from .lsf import LsfTarget
from .slurm import SlurmTarget


class SimulationBackend(Protocol):
    engine: str
    script_name: str

    def remote_dir(self, run_id: str) -> str: ...
    def render_script(self, *, run_id: str, entry: str) -> str: ...
    def stage(self, remote_dir: str, archive: Path) -> None: ...
    def submit(self, remote_dir: str) -> str: ...
    def recover_job_id(self, remote_dir: str, run_id: str, created_at: str) -> str | None: ...
    def status(self, job_id: str, remote_dir: str | None = None) -> dict[str, str]: ...
    def missing_outputs(self, remote_dir: str, paths: list[str]) -> list[str]: ...
    def log_tail(self, remote_dir: str, job_id: str, *, lines: int = 30) -> str: ...


BackendFactory = Callable[[str, dict[str, Any], Path], SimulationBackend]
BACKENDS: dict[str, BackendFactory] = {
    "local": lambda name, raw, home: LocalTarget(name, raw, home),
    "lsf": lambda name, raw, home: LsfTarget(name, raw),
    "slurm": lambda name, raw, home: SlurmTarget(name, raw),
}


def register_backend(scheduler: str, factory: BackendFactory) -> None:
    if not scheduler or scheduler in BACKENDS:
        raise ValueError(f"simulation backend already registered or invalid: {scheduler}")
    BACKENDS[scheduler] = factory


def create_backend(name: str, raw: dict[str, Any], home: Path) -> SimulationBackend:
    scheduler = raw.get("scheduler")
    factory = BACKENDS.get(scheduler)
    if factory is None:
        raise ValueError(f"unsupported simulation scheduler: {scheduler}")
    return factory(name, raw, home)
