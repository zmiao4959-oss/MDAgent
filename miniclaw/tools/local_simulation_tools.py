"""Peer local execution tools backed by the shared simulation lifecycle."""
from __future__ import annotations

from ..settings import active_workspace_dir
from .registry import tool_registry
from .simulation_tools import _result, get_simulation_service


def _parameters() -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["input_file"],
        "properties": {
            "input_file": {
                "type": "string",
                "description": "Existing input script path, relative to the active workspace or absolute inside it. Its parent directory is the working directory.",
            },
        },
    }


def _run_local(engine: str, input_file: str) -> str:
    service = get_simulation_service()
    target = f"local-{engine}"
    raw = service.settings.targets.get(target) or {}
    if raw.get("scheduler") != "local" or raw.get("engine") != engine:
        return f"Error: {target} must be configured as a local {engine} target"
    prepared = None
    try:
        prepared = service.prepare_local(input_file, target, workspace=active_workspace_dir())
        result = service.submit(prepared["run_id"])
        return _result(result)
    except (ValueError, OSError, RuntimeError) as exc:
        # Preserve a prepared run ID if staging failed, so callers can query/retry it.
        if prepared is not None:
            return _result({**service.get(prepared["run_id"], refresh=False), "error": str(exc)})
        return f"Error: {exc}"


@tool_registry.register(
    name="run_gpumd",
    schema={
        "type": "function",
        "function": {
            "name": "run_gpumd",
            "description": (
                "Start GPUMD from run.in in the input script's directory using the configured WSL executable. "
                "Return the run ID, directories and status after one startup check. "
                "Each call starts a new simulation."
            ),
            "parameters": _parameters(),
        },
    },
    require_approval=True, risk_level="high", tags=["execution", "simulation", "gpumd"],
)
def run_gpumd(input_file: str, **kwargs) -> str:
    return _run_local("gpumd", input_file)


@tool_registry.register(
    name="run_lammps",
    schema={
        "type": "function",
        "function": {
            "name": "run_lammps",
            "description": (
                "Start LAMMPS in the input script's directory using the configured local executable and MPI launcher. "
                "Return the run ID, directories and status after one startup check. "
                "Each call starts a new simulation."
            ),
            "parameters": _parameters(),
        },
    },
    require_approval=True, risk_level="high", tags=["execution", "simulation", "lammps"],
)
def run_lammps(input_file: str, **kwargs) -> str:
    return _run_local("lammps", input_file)
