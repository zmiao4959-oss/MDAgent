"""Process identity and WSL recovery probes; no GPUMD execution."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import psutil
import pytest

from miniclaw.simulation import local_process
from miniclaw.simulation.local import LocalTarget


def launcher(folder, cwd, executable="/home/user/bin/gpumd"):
    folder.mkdir()
    argv = ["wsl.exe", "-d", "TestLinux", "--cd", cwd, "-e", "/bin/bash",
            "-lic", 'exec "$@"', "bash", executable]
    (folder / "launch.json").write_text(json.dumps({"argv": argv, "working_dir": cwd}))
    return argv


def test_pid_reused_by_unrelated_process_is_not_a_worker(tmp_path):
    assert local_process.worker_is_alive(tmp_path, os.getpid()) is False


def test_wsl_probe_matches_directory_and_shares_one_scan(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    executable = "/home/user/a b;$(no)/gpumd"
    launcher(a, r"C:\projects\a", executable)
    launcher(b, r"C:\projects\b", executable)
    monkeypatch.setattr(local_process.psutil, "process_iter", lambda *args: [])
    calls = []
    def scan(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b"C:\\projects\\a\0\0MINICLAW_PROBE_OK\0")
    monkeypatch.setattr(local_process.subprocess, "run", scan)
    probes = {}
    assert local_process.child_is_alive(a, probes) is True
    assert local_process.child_is_alive(b, probes) is False
    assert len(calls) == 1
    assert calls[0][-1] == executable  # A literal argv, never interpolated shell text.


@pytest.mark.parametrize("outcome", ["timeout", "error", "incomplete"])
def test_inconclusive_wsl_probe_retains_slot(tmp_path, monkeypatch, outcome):
    launcher(tmp_path / "job", r"C:\projects\job")
    monkeypatch.setattr(local_process.psutil, "process_iter", lambda *args: [])
    def scan(argv, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        return SimpleNamespace(returncode=3 if outcome == "error" else 0, stdout=b"")
    monkeypatch.setattr(local_process.subprocess, "run", scan)
    assert local_process.child_is_alive(tmp_path / "job") is None


def test_live_wsl_launcher_is_retained_before_linux_process_appears(tmp_path, monkeypatch):
    argv = launcher(tmp_path / "job", r"C:\projects\job")
    process = SimpleNamespace(info={"name": "wsl.exe"}, cmdline=lambda: argv, status=lambda: "running")
    monkeypatch.setattr(local_process.psutil, "process_iter", lambda *args: [process])
    monkeypatch.setattr(local_process.subprocess, "run", lambda *args, **kwargs: pytest.fail("unneeded WSL probe"))
    assert local_process.child_is_alive(tmp_path / "job") is True


def test_native_child_identity_includes_birth_time(tmp_path):
    (tmp_path / "launch.json").write_text(json.dumps({"argv": [sys.executable, "-c", "pass"]}))
    birth = psutil.Process().create_time()
    marker = tmp_path / ".miniclaw-child.json"
    marker.write_text(json.dumps({"pid": os.getpid(), "birth": birth}))
    assert local_process.child_is_alive(tmp_path) is True
    marker.write_text(json.dumps({"pid": os.getpid(), "birth": birth - 100}))
    assert local_process.child_is_alive(tmp_path) is False


def test_missing_launch_metadata_is_not_proof_of_absence(tmp_path):
    assert local_process.child_is_alive(tmp_path) is None


@pytest.mark.parametrize("child, expected", [(False, "FAILED"), (True, "RUNNING"), (None, "UNKNOWN")])
def test_legacy_status_recovers_without_inventing_an_exit_code(tmp_path, monkeypatch, child, expected):
    folder = tmp_path / "legacy"
    launcher(folder, r"C:\projects\legacy")
    target = LocalTarget("local-gpumd", {"scheduler": "local", "engine": "gpumd",
                         "executable": "/test/gpumd", "wsl_distribution": "TestLinux"}, tmp_path)
    monkeypatch.setattr("miniclaw.simulation.local.worker_is_alive", lambda *args: False)
    monkeypatch.setattr("miniclaw.simulation.local.child_is_alive", lambda *args: child)
    status = target.status("9999999", str(folder))
    assert status["scheduler_state"] == expected
    assert "exit_code" not in status
