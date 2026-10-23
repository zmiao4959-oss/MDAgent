"""Small agent-facing API for simulation submission and on-demand status."""
from __future__ import annotations

import asyncio
import json
import math
import shutil
from functools import lru_cache

from ..settings import APP_PATHS, active_workspace_dir, config
from ..simulation.service import SimulationService, TERMINAL_STATES
from .registry import tool_registry


_SIMULATION_POLL_INTERVAL_SEC = 5.0


@lru_cache(maxsize=1)
def get_simulation_service() -> SimulationService:
    return SimulationService(config.simulation, APP_PATHS.home)


def _result(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


@tool_registry.register(
    name="list_simulation_backends",
    schema={
        "type": "function",
        "function": {
            "name": "list_simulation_backends",
            "description": "List local simulation capabilities and configured remote scheduler targets with availability; use before choosing where to run.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    tags=["simulation"],
)
def list_simulation_backends(**kwargs) -> str:
    targets = {"local": {}, "remote": {}}
    for name, raw in config.simulation.targets.items():
        scheduler = raw.get("scheduler")
        if scheduler == "local":
            required = ("executable", "wsl_distribution") if raw.get("engine") == "gpumd" else ("executable", "mpi_launcher")
        elif scheduler == "lsf":
            required = ("remote_base", "executable", "queue")
        else:
            required = ("remote_base", "executable", "partition")
        missing = [key for key in required if not raw.get(key)]
        if scheduler == "local" and raw.get("engine") == "lammps":
            missing += [key for key in ("executable", "mpi_launcher") if raw.get(key) and not shutil.which(raw[key])]
        if scheduler == "local" and raw.get("engine") == "gpumd" and not shutil.which("wsl.exe"):
            missing.append("wsl.exe")
        targets["local" if scheduler == "local" else "remote"][name] = {
            "engine": raw.get("engine", "lammps"),
            "scheduler": scheduler,
            "available": bool(config.simulation.enabled and raw.get("enabled", True) and not missing),
            "missing_requirements": sorted(set(missing)),
        }
    return _result(targets)


@tool_registry.register(
    name="prepare_simulation",
    schema={
        "type": "function",
        "function": {
            "name": "prepare_simulation",
            "description": (
                "Validate simulation.yaml in project_dir, freeze inputs, and preview the configured launcher. "
                "Required fields: engine (gpumd or lammps), entry (input script path), "
                "inputs (non-empty list of all required input file paths). "
                "Paths are relative to project_dir; GPUMD entry must be run.in. Example:\n"
                "engine: gpumd\nentry: run.in\ninputs: [run.in, model.xyz, nep.txt]\n"
                "Use actual filenames. Optional expected_outputs is a list of relative output paths. "
                "Does not start a job; pass the returned run_id to submit_simulation."
            ),
            "parameters": {
                "type": "object", "required": ["target"], "additionalProperties": False,
                "properties": {
                    "target": {"type": "string", "description": "Configured target name from list_simulation_backends"},
                    "project_dir": {"type": "string", "description": "Project directory inside the active workspace; defaults to ."},
                },
            },
        },
    },
    tags=["simulation"],
)
def prepare_simulation(target: str, project_dir: str = ".", **kwargs) -> str:
    try:
        return _result(get_simulation_service().prepare(
            project_dir, target,
            workspace=active_workspace_dir(),
        ))
    except (ValueError, OSError) as exc:
        return f"Error: {exc}"


@tool_registry.register(
    name="submit_simulation",
    schema={
        "type": "function",
        "function": {
            "name": "submit_simulation",
            "description": "Start a prepared local or remote job once, wait the configured startup buffer, then check its status once. Return the run ID, process/job ID and observed state; UNKNOWN means unconfirmed, not safe to resubmit. Use only after the user requested that run.",
            "parameters": {
                "type": "object", "required": ["run_id"], "additionalProperties": False,
                "properties": {"run_id": {"type": "string", "description": "run_id returned by prepare_simulation"}},
            },
        },
    },
    require_approval=True,
    risk_level="high",
    tags=["simulation", "remote_execution"],
)
def submit_simulation(run_id: str, **kwargs) -> str:
    try:
        return _result(get_simulation_service().submit(run_id))
    except (ValueError, OSError, RuntimeError) as exc:
        return f"Error: {exc}"


@tool_registry.register(
    name="get_simulation",
    schema={
        "type": "function",
        "function": {
            "name": "get_simulation",
            "description": (
                "Refresh one local, LSF or Slurm simulation and return its status, reason, "
                "and short terminal log tail. Default: query once. Set wait_timeout > 0 to "
                "wait inside this tool for SUCCEEDED, FAILED or CANCELLED, polling every "
                "5 seconds without LLM calls. A PREPARED run returns immediately because "
                "it has not been submitted. On timeout, return the last observed status "
                "and original run_id with wait_timed_out=true; UNKNOWN remains unconfirmed. "
                "Waiting never submits or stops a simulation. Cancellation stops waiting "
                "and further polling. Choose a wait shorter than the remaining Agent task "
                "timeout; use it when the task requires completion instead of repeatedly "
                "reading logs through the LLM."
            ),
            "parameters": {
                "type": "object", "required": ["run_id"], "additionalProperties": False,
                "properties": {
                    "run_id": {"type": "string", "description": "Persistent run_id"},
                    "wait_timeout": {
                        "type": "number", "minimum": 0, "default": 0,
                        "description": "Maximum wait in seconds. 0 queries once; e.g. 300 waits up to five minutes. Returns early on a terminal state.",
                    },
                },
            },
        },
    },
    tags=["simulation"],
)
async def get_simulation(run_id: str, wait_timeout: float = 0, **kwargs) -> str:
    """异步等待现有作业；同步状态查询放在线程中，取消后不再发起下一次查询。"""
    if (isinstance(wait_timeout, bool) or not isinstance(wait_timeout, (int, float))
            or not math.isfinite(wait_timeout) or wait_timeout < 0):
        return "Error: wait_timeout must be a finite non-negative number"
    try:
        service = get_simulation_service()
        if wait_timeout == 0:
            # 保持单次查询的原有返回结构。
            return _result(await asyncio.to_thread(service.get, run_id))

        # 状态查询本身超时时仍有可靠的已保存状态可以返回。
        result = await asyncio.to_thread(service.get, run_id, refresh=False)
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + wait_timeout
        timed_out = False
        while result["status"] not in TERMINAL_STATES and result["status"] != "PREPARED":
            remaining = deadline - loop.time()
            if remaining <= 0:
                timed_out = True
                break
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(service.get, run_id), timeout=remaining,
                )
            except asyncio.TimeoutError:
                if loop.time() < deadline:
                    raise  # 后端自身的查询错误，不能伪装成等待期限到达。
                timed_out = True
                break
            if result["status"] in TERMINAL_STATES or result["status"] == "PREPARED":
                break
            remaining = deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(min(_SIMULATION_POLL_INTERVAL_SEC, remaining))

        return _result({
            **result,
            "wait_timed_out": timed_out,
            "waited_seconds": round(loop.time() - started, 3),
        })
    except (ValueError, OSError, RuntimeError) as exc:
        return f"Error: {exc}"
