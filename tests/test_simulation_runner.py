import json
import sys
import tarfile
import time
from pathlib import Path

import pytest

from miniclaw.settings import SimulationConfig
from miniclaw.agents import AgentCatalog
from miniclaw.simulation.local import LocalTarget
from miniclaw.simulation.lsf import LsfTarget
from miniclaw.simulation.service import SimulationService
from miniclaw.simulation.slurm import RemoteError, SlurmTarget


TARGET = {
    "scheduler": "slurm",
    "ssh_alias": "testhost",
    "remote_base": "/remote/simulations",
    "partition": "normal",
    "executable": "/opt/lammps/bin/lmp",
    "env_script": "/opt/lammps/env.sh",
    "mpi": "pmix_v3",
    "nodes": 1,
    "ntasks_per_node": 4,
    "cpus_per_task": 8,
    "gpus": 2,
}


def setup(tmp_path: Path, *, inputs=None, expected_outputs=None):
    project = tmp_path / "project"
    project.mkdir()
    (project / "in.cuni").write_text("read_data data.cuni\n", encoding="utf-8")
    (project / "data.cuni").write_text("LAMMPS data\n", encoding="utf-8")
    paths = inputs if inputs is not None else ["in.cuni", "data.cuni"]
    lines = ["engine: lammps", "entry: in.cuni", "inputs:"]
    lines.extend(f"  - {path}" for path in paths)
    if expected_outputs:
        lines += ["expected_outputs:"]
        lines.extend(f"  - {path}" for path in expected_outputs)
    else:
        lines += ["expected_outputs: []"]
    (project / "simulation.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    config = SimulationConfig(enabled=True, submission_check_delay_sec=0, targets={"sugon": dict(TARGET)})
    service = SimulationService(config, tmp_path / "home")
    return project, service, config


def test_prepare_freezes_inputs_and_script_without_remote_calls(tmp_path, monkeypatch):
    project, service, _ = setup(tmp_path, expected_outputs=["log.lammps"])
    monkeypatch.setattr(SlurmTarget, "stage", lambda *_: pytest.fail("prepare contacted SSH"))

    prepared = service.prepare(".", "sugon", workspace=project)

    assert prepared["status"] == "PREPARED"
    assert prepared["remote_dir"].endswith(prepared["run_id"])
    assert "#SBATCH --gres=gpu:2" in prepared["submit_script"]
    assert "srun --mpi=pmix_v3 /opt/lammps/bin/lmp -i in.cuni" in prepared["submit_script"]
    archive = tmp_path / "home" / "simulations" / prepared["run_id"] / "input.tar"
    with tarfile.open(archive) as bundle:
        assert set(bundle.getnames()) == {"in.cuni", "data.cuni", "simulation.yaml", "job.slurm"}
    record = service.store.get(prepared["run_id"])
    assert record["expected_outputs"] == ["log.lammps"]


def test_submit_is_idempotent_and_status_survives_restart(tmp_path, monkeypatch):
    project, service, config = setup(tmp_path)
    prepared = service.prepare(".", "sugon", workspace=project)
    calls = []
    monkeypatch.setattr(SlurmTarget, "stage", lambda self, remote, archive: calls.append(("stage", remote)))
    monkeypatch.setattr(SlurmTarget, "submit", lambda self, remote: calls.append(("submit", remote)) or "12345")
    monkeypatch.setattr(SlurmTarget, "status", lambda *args: {"scheduler_state": "PENDING", "source": "squeue"})

    first = service.submit(prepared["run_id"])
    second = service.submit(prepared["run_id"])
    restarted = SimulationService(config, tmp_path / "home")

    assert first["job_id"] == second["job_id"] == "12345"
    assert [item[0] for item in calls] == ["stage", "submit"]
    assert restarted.get(prepared["run_id"], refresh=False)["status"] == "PENDING"


def test_broken_submit_connection_reconciles_without_second_submit(tmp_path, monkeypatch):
    project, service, _ = setup(tmp_path)
    prepared = service.prepare(".", "sugon", workspace=project)
    calls = []
    monkeypatch.setattr(SlurmTarget, "stage", lambda *args: calls.append("stage"))

    def broken_submit(*args):
        calls.append("submit")
        raise RemoteError("connection closed after sbatch")

    monkeypatch.setattr(SlurmTarget, "submit", broken_submit)
    monkeypatch.setattr(SlurmTarget, "recover_job_id", lambda *args: None)
    uncertain = service.submit(prepared["run_id"])
    assert uncertain["status"] == "UNKNOWN"
    assert uncertain["run_id"] == prepared["run_id"]
    assert "connection closed" in uncertain["last_error"]

    monkeypatch.setattr(SlurmTarget, "recover_job_id", lambda *args: "12345")
    monkeypatch.setattr(SlurmTarget, "status", lambda *args: {"scheduler_state": "RUNNING", "source": "squeue"})
    result = service.get(prepared["run_id"])
    assert result["job_id"] == "12345"
    assert result["status"] == "RUNNING"
    assert calls == ["stage", "submit"]


def test_submission_checks_running_once_and_later_status_is_on_demand(tmp_path, monkeypatch):
    project, service, config = setup(tmp_path, expected_outputs=["log.lammps"])
    prepared = service.prepare(".", "sugon", workspace=project)
    monkeypatch.setattr(SlurmTarget, "stage", lambda *args: None)
    monkeypatch.setattr(SlurmTarget, "submit", lambda *args: "12345")
    state = {"value": {"scheduler_state": "RUNNING", "source": "squeue"}}
    checked = []
    def status(*args):
        checked.append(state["value"])
        return state["value"]
    monkeypatch.setattr(SlurmTarget, "status", status)
    monkeypatch.setattr(SlurmTarget, "missing_outputs", lambda *args: [])
    monkeypatch.setattr(SlurmTarget, "log_tail", lambda *args: "finished normally")
    assert service.submit(prepared["run_id"])["status"] == "RUNNING"
    assert len(checked) == 1

    state["value"] = {"scheduler_state": "COMPLETED", "source": "sacct", "exit_code": "0:0"}
    restarted = SimulationService(config, tmp_path / "home")
    assert restarted.get(prepared["run_id"], refresh=False)["status"] == "RUNNING"
    assert len(checked) == 1
    assert restarted.get(prepared["run_id"])["status"] == "SUCCEEDED"
    assert len(checked) == 2


def test_rejects_unsafe_and_missing_inputs(tmp_path):
    project, service, _ = setup(tmp_path, inputs=["../secret.txt"])
    with pytest.raises(ValueError, match="unsafe relative path"):
        service.prepare(".", "sugon", workspace=project)
    (project / "simulation.yaml").write_text(
        "engine: lammps\nentry: in.cuni\ninputs:\n  - missing.dat\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="input file missing"):
        service.prepare(".", "sugon", workspace=project)


def test_slurm_status_uses_accounting_when_job_leaves_queue(monkeypatch):
    target = SlurmTarget("sugon", TARGET)
    monkeypatch.setattr(target, "_ssh", lambda script: "ACCOUNT|12345|COMPLETED|0:0")
    assert target.status("12345") == {
        "source": "sacct", "scheduler_state": "COMPLETED", "exit_code": "0:0"
    }


def test_staging_failure_can_retry_same_prepared_run(tmp_path, monkeypatch):
    project, service, _ = setup(tmp_path)
    prepared = service.prepare(".", "sugon", workspace=project)

    def failed_stage(*args):
        raise RemoteError("network unavailable")

    monkeypatch.setattr(SlurmTarget, "stage", failed_stage)
    with pytest.raises(RemoteError, match="network unavailable"):
        service.submit(prepared["run_id"])
    assert service.get(prepared["run_id"], refresh=False)["status"] == "PREPARED"


def test_tampered_snapshot_is_not_submitted(tmp_path, monkeypatch):
    project, service, _ = setup(tmp_path)
    prepared = service.prepare(".", "sugon", workspace=project)
    archive = tmp_path / "home" / "simulations" / prepared["run_id"] / "input.tar"
    archive.write_bytes(b"changed")
    monkeypatch.setattr(SlurmTarget, "stage", lambda *args: pytest.fail("tampered snapshot uploaded"))
    with pytest.raises(ValueError, match="archive was changed"):
        service.submit(prepared["run_id"])


def test_missing_declared_output_marks_job_failed(tmp_path, monkeypatch):
    project, service, _ = setup(tmp_path, expected_outputs=["result.dat"])
    prepared = service.prepare(".", "sugon", workspace=project)
    monkeypatch.setattr(SlurmTarget, "stage", lambda *args: None)
    monkeypatch.setattr(SlurmTarget, "submit", lambda *args: "12345")
    monkeypatch.setattr(
        SlurmTarget, "status",
        lambda *args: {"scheduler_state": "COMPLETED", "source": "sacct", "exit_code": "0:0"},
    )
    monkeypatch.setattr(SlurmTarget, "missing_outputs", lambda *args: ["result.dat"])
    monkeypatch.setattr(SlurmTarget, "log_tail", lambda *args: "")
    service.submit(prepared["run_id"])
    result = service.get(prepared["run_id"])
    assert result["status"] == "FAILED"
    assert "result.dat" in result["reason"]


def test_submit_waits_buffer_before_checking_early_failure(tmp_path, monkeypatch):
    project, service, config = setup(tmp_path)
    config.submission_check_delay_sec = 7
    prepared = service.prepare(".", "sugon", workspace=project)
    events = []
    monkeypatch.setattr(SlurmTarget, "stage", lambda *args: None)
    monkeypatch.setattr(SlurmTarget, "submit", lambda *args: events.append("submit") or "12345")
    monkeypatch.setattr("miniclaw.simulation.service.time.sleep", lambda seconds: events.append(("wait", seconds)))
    def status(*args):
        events.append("check")
        assert service.store.get(prepared["run_id"])["status"] == "SUBMITTED"
        return {"scheduler_state": "FAILED", "source": "sacct", "exit_code": "1:0"}
    monkeypatch.setattr(SlurmTarget, "status", status)
    monkeypatch.setattr(SlurmTarget, "log_tail", lambda *args: "invalid input")
    result = service.submit(prepared["run_id"])
    assert events == ["submit", ("wait", 7), "check"]
    assert result["status"] == "FAILED"
    assert result["log_tail"] == "invalid input"


def test_disconnected_status_returns_unknown_with_recovered_job_id(tmp_path, monkeypatch):
    project, service, _ = setup(tmp_path)
    prepared = service.prepare(".", "sugon", workspace=project)
    monkeypatch.setattr(SlurmTarget, "stage", lambda *args: None)
    calls = []
    def disconnected_submit(*args):
        calls.append("submit")
        raise RemoteError("connection closed after sbatch")
    def disconnected_status(*args):
        raise RemoteError("status query unavailable")
    monkeypatch.setattr(SlurmTarget, "submit", disconnected_submit)
    monkeypatch.setattr(SlurmTarget, "recover_job_id", lambda *args: "12345")
    monkeypatch.setattr(SlurmTarget, "status", disconnected_status)
    result = service.submit(prepared["run_id"])
    assert result["status"] == "UNKNOWN"
    assert result["job_id"] == "12345"
    assert result["run_id"] == prepared["run_id"]
    assert "status query unavailable" in result["last_error"]
    assert calls == ["submit"]


def test_scheduler_modules_render_only_their_engine(tmp_path):
    lsf = LsfTarget("hpc", {
        "scheduler": "lsf", "engine": "lammps", "ssh_alias": "hpc",
        "remote_base": "/scratch/sim", "executable": "/opt/lmp",
        "queue": "cpu", "cores": 8,
    })
    script = lsf.render_script(run_id="a" * 16, entry="in.test")
    assert "#BSUB -q cpu" in script
    assert "mpirun -np 8 /opt/lmp -in in.test" in script
    lsf._ssh = lambda script: "DONE|-"
    assert lsf.status("123")["scheduler_state"] == "DONE"
    lsf._ssh = lambda script, **kwargs: "" if "bjobs" in script else "Job <123>, Status <EXIT>\nExited with exit code 7."
    assert lsf.status("123") == {"source": "bacct", "scheduler_state": "EXIT", "exit_code": "7"}

    gpumd = SlurmTarget("sugon", {**TARGET, "engine": "gpumd"})
    script = gpumd.render_script(run_id="a" * 16, entry="run.in")
    assert "srun --mpi=pmix_v3 /opt/lammps/bin/lmp" in script
    assert " -i " not in script
    plain_gpumd = SlurmTarget("sugon", {k: v for k, v in {**TARGET, "engine": "gpumd"}.items() if k != "mpi"})
    assert "srun /opt/lammps/bin/lmp" in plain_gpumd.render_script(run_id="b" * 16, entry="run.in")
    with pytest.raises(ValueError, match="run.in"):
        gpumd.render_script(run_id="a" * 16, entry="other.in")


def test_runner_exposes_local_execution_tools_without_skill_dependency():
    profile = AgentCatalog().require("simulation-runner")
    assert not profile.skills
    for tool in ("run_gpumd", "run_lammps", "get_simulation", "list_simulation_backends"):
        assert profile.allows_tool(tool)
    assert not profile.allows_tool("execute")
    assert not profile.allows_tool("read_skill")


def test_gpumd_manifest_prepares_local_launcher(tmp_path):
    project = tmp_path / "gpumd"
    project.mkdir()
    (project / "run.in").write_text("run 100\n", encoding="utf-8")
    (project / "model.xyz").write_text("1\ncomment\nCu 0 0 0\n", encoding="utf-8")
    (project / "simulation.yaml").write_text(
        "engine: gpumd\nentry: run.in\ninputs: [run.in, model.xyz]\n", encoding="utf-8"
    )
    raw = {"scheduler": "local", "engine": "gpumd", "executable": "/home/zm/bin/gpumd",
           "wsl_distribution": "Ubuntu-24.04"}
    service = SimulationService(SimulationConfig(enabled=True, targets={"local-gpumd": raw}), tmp_path / "home")
    prepared = service.prepare(".", "local-gpumd", workspace=project)
    assert prepared["engine"] == "gpumd"
    assert "wsl.exe" in prepared["launch_preview"]
    assert " -i " not in prepared["launch_preview"]


def test_disabled_target_and_engine_mismatch_stop_before_snapshot(tmp_path):
    project, service, _ = setup(tmp_path)
    service.settings.targets["sugon"]["enabled"] = False
    with pytest.raises(ValueError, match="disabled"):
        service.prepare(".", "sugon", workspace=project)
    service.settings.targets["sugon"]["enabled"] = True
    service.settings.targets["sugon"]["engine"] = "gpumd"
    with pytest.raises(ValueError, match="runs gpumd"):
        service.prepare(".", "sugon", workspace=project)


def test_local_job_runs_in_snapshot_and_survives_service_restart(tmp_path, monkeypatch):
    project, _, _ = setup(tmp_path, expected_outputs=["done.txt"])
    local_raw = {"scheduler": "local", "engine": "lammps", "executable": sys.executable,
                 "mpi_launcher": sys.executable, "cores": 1}
    service = SimulationService(SimulationConfig(enabled=True, submission_check_delay_sec=0, targets={"local-lammps": local_raw}), tmp_path / "home")
    monkeypatch.setattr(LocalTarget, "render_script", lambda self, *, run_id, entry: json.dumps({
        "argv": [sys.executable, "-c", "from pathlib import Path; Path('done.txt').write_text('ok')"]
    }))
    monkeypatch.setattr(LocalTarget, "stage", lambda *args: None)
    prepared = service.prepare(".", "local-lammps", workspace=project)
    submitted = service.submit(prepared["run_id"])
    assert submitted["scheduler"] == "local"
    assert "run_dir" in submitted and "remote_dir" not in submitted
    restarted = SimulationService(service.settings, tmp_path / "home")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = restarted.get(prepared["run_id"])
        if result["status"] in {"SUCCEEDED", "FAILED"}:
            break
        time.sleep(0.1)
    assert result["status"] == "SUCCEEDED", result
    assert (project / "done.txt").exists() is False
    assert (Path(result["run_dir"]) / "done.txt").read_text() == "ok"
