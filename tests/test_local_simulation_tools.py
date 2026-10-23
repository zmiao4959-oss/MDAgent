import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from miniclaw.agents import AgentCatalog
from miniclaw.settings import SimulationConfig, workspace_scope
from miniclaw.simulation.local import LocalTarget
from miniclaw.simulation.remote import RemoteError
from miniclaw.simulation.service import SimulationService
from miniclaw.tools import ensure_tools_loaded, local_simulation_tools
from miniclaw.tools.registry import tool_registry
from miniclaw.tools.file_tools import read_tool


@pytest.fixture
def local_setup(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    targets = {
        "local-gpumd": {"scheduler": "local", "engine": "gpumd", "executable": "/opt/gpumd",
                        "wsl_distribution": "TestLinux"},
        "local-lammps": {"scheduler": "local", "engine": "lammps", "executable": "lmp",
                         "mpi_launcher": "mpiexec", "cores": 4},
    }
    service = SimulationService(SimulationConfig(enabled=True, submission_check_delay_sec=0, targets=targets), tmp_path / "home")
    monkeypatch.setattr(local_simulation_tools, "get_simulation_service", lambda: service)
    monkeypatch.setattr(local_simulation_tools, "active_workspace_dir", lambda: project)
    calls = []
    monkeypatch.setattr(LocalTarget, "stage", lambda *args: None)
    monkeypatch.setattr(LocalTarget, "submit", lambda *args: calls.append("submit") or "12345")
    monkeypatch.setattr(LocalTarget, "status", lambda *args: {"scheduler_state": "RUNNING", "source": "local"})
    return project, service, calls


def script(project, engine):
    entry = "run.in" if engine == "gpumd" else "in.sim"
    (project / entry).write_text("run 200000\n", encoding="utf-8")
    return entry


@pytest.mark.parametrize("engine", ["gpumd", "lammps"])
def test_local_tools_run_project_root_without_modifying_scripts(local_setup, engine):
    project, service, calls = local_setup
    entry = script(project, engine)
    tool = getattr(local_simulation_tools, f"run_{engine}")
    # An unrelated stale manifest must not affect local execution.
    (project / "simulation.yaml").write_text("invalid: [", encoding="utf-8")
    result = json.loads(tool(entry))
    assert result["target"] == f"local-{engine}"
    assert result["engine"] == engine
    assert result["status"] == "RUNNING" and result["job_id"] == "12345"
    assert service.get(result["run_id"], refresh=False)["job_id"] == "12345"
    assert (project / entry).read_text(encoding="utf-8") == "run 200000\n"
    logs = project / ".miniclaw" / "simulations" / result["run_id"] / "input"
    assert result["run_dir"] == str(project)
    assert result["log_dir"] == str(logs)
    with workspace_scope(project):
        assert "run 200000" in read_tool(str(project / entry))
    assert not (logs / entry).exists()
    launch = json.loads((logs / "launch.json").read_text(encoding="utf-8"))
    assert launch["working_dir"] == str(project)
    argv = launch["argv"]
    if engine == "gpumd":
        assert argv[:3] == ["wsl.exe", "-d", "TestLinux"]
        assert argv[-1] == "/opt/gpumd"
        assert argv[argv.index("--cd") + 1] == str(project)
    else:
        assert argv == ["mpiexec", "-np", "4", "lmp", "-in", "in.sim"]
    assert calls == ["submit"]


def test_gpumd_requires_run_in(local_setup):
    project, _, calls = local_setup
    entry = script(project, "lammps")
    assert "GPUMD entry must be run.in" in local_simulation_tools.run_gpumd(entry)
    assert calls == []


def test_generator_can_run_a_prepared_short_script_in_a_subdirectory(local_setup):
    project, _, calls = local_setup
    trial = project / "trial"
    trial.mkdir()
    script(trial, "gpumd")
    (trial / "run.in").write_text("run 10\n", encoding="utf-8")
    result = json.loads(local_simulation_tools.run_gpumd(str(trial / "run.in")))
    assert result["project_dir"] == str(trial)
    assert result["status"] == "RUNNING"
    assert calls == ["submit"]


def test_local_tool_requires_script_and_stays_inside_workspace(local_setup):
    _, _, calls = local_setup
    assert "existing file" in local_simulation_tools.run_gpumd("run.in")
    assert "inside the active workspace" in local_simulation_tools.run_gpumd("../run.in")
    assert "existing file" in local_simulation_tools.run_gpumd(".")
    assert calls == []


def test_failed_staging_returns_original_run_id(local_setup, monkeypatch):
    project, service, calls = local_setup
    script(project, "gpumd")
    def failed_stage(*args):
        raise RemoteError("wsl.exe not found")
    monkeypatch.setattr(LocalTarget, "stage", failed_stage)
    result = json.loads(local_simulation_tools.run_gpumd("run.in"))
    assert result["status"] == "PREPARED"
    assert result["error"] == "wsl.exe not found"
    assert service.get(result["run_id"], refresh=False)["status"] == "PREPARED"
    assert calls == []


def test_local_tools_never_use_a_remote_target(local_setup):
    project, service, calls = local_setup
    script(project, "gpumd")
    service.settings.targets["local-gpumd"]["scheduler"] = "slurm"
    assert "must be configured as a local gpumd target" in local_simulation_tools.run_gpumd("run.in")
    assert calls == []


def test_peer_tools_are_registered_and_generator_uses_shared_execution():
    ensure_tools_loaded()
    assert tool_registry.get("run_gpumd_trial") is None
    for name in ("run_gpumd", "run_lammps"):
        definition = tool_registry.get(name)
        assert definition is not None
        assert set(definition.schema["function"]["parameters"]["properties"]) == {"input_file"}
        assert definition.schema["function"]["parameters"]["required"] == ["input_file"]
        assert "simulation.yaml" not in definition.schema["function"]["description"]
        result = asyncio.run(tool_registry.execute(name, {"input_file": "run.in", "working_dir": "."}))
        assert "Unexpected argument" in result
    generator = AgentCatalog().require("gpumd-generator")
    assert generator.allows_tool("run_gpumd")
    assert generator.allows_tool("get_simulation")


def test_local_worker_uses_script_directory_and_recovers_after_restart(local_setup, monkeypatch):
    project, service, _ = local_setup
    trial = project / "trial with spaces"
    trial.mkdir()
    entry = script(trial, "lammps")
    (trial / "model.data").write_text("model", encoding="utf-8")
    # Exercise the real detached worker without requiring MPI or a GPU.
    monkeypatch.undo()
    monkeypatch.setattr(LocalTarget, "stage", lambda *args: None)
    monkeypatch.setattr(LocalTarget, "render_script", lambda self, *, run_id, entry, working_dir: json.dumps({
        "argv": [sys.executable, "-c",
                 "from pathlib import Path; Path('result.txt').write_text(Path('model.data').read_text())"],
        "working_dir": working_dir,
    }))
    prepared = service.prepare_local(str(trial / entry), "local-lammps", workspace=project)
    service.submit(prepared["run_id"])
    restarted = SimulationService(service.settings, service.home)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = restarted.get(prepared["run_id"])
        if result["status"] in {"SUCCEEDED", "FAILED"}:
            break
        time.sleep(0.05)
    assert result["status"] == "SUCCEEDED"
    assert (trial / "result.txt").read_text() == "model"
    assert result["run_dir"] == str(trial)
    assert Path(result["log_dir"], ".miniclaw-exit.json").is_file()
