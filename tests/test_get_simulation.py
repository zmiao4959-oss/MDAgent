import asyncio
import json
import sys
import threading
from types import SimpleNamespace

import pytest

from miniclaw.settings import SimulationConfig
from miniclaw.simulation.local import LocalTarget
from miniclaw.simulation.service import SimulationService
from miniclaw.tools import ensure_tools_loaded, simulation_tools
from miniclaw.tools.registry import tool_registry


class StatusService:
    def __init__(self, states, *, saved="RUNNING"):
        self.states = iter(states)
        self.saved = {"run_id": "original-id", "status": saved}
        self.calls = []

    def get(self, run_id, *, refresh=True):
        self.calls.append((run_id, refresh))
        if refresh:
            state = next(self.states, self.saved["status"])
            if isinstance(state, Exception):
                raise state
            self.saved = {**self.saved, "status": state}
        return dict(self.saved)


@pytest.fixture
def install_service(monkeypatch):
    def install(service):
        monkeypatch.setattr(simulation_tools, "get_simulation_service", lambda: service)
        monkeypatch.setattr(simulation_tools, "_SIMULATION_POLL_INTERVAL_SEC", 0.005)
        return service
    return install


def execute(arguments):
    ensure_tools_loaded()
    return asyncio.run(tool_registry.execute("get_simulation", arguments))


def test_default_queries_once_and_preserves_existing_output(install_service):
    service = install_service(StatusService(["RUNNING"]))
    result = json.loads(execute({"run_id": "original-id"}))
    assert result == {"run_id": "original-id", "status": "RUNNING"}
    assert service.calls == [("original-id", True)]


@pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED", "CANCELLED"])
def test_wait_observes_queued_and_running_then_returns_terminal(install_service, terminal):
    service = install_service(StatusService(["PENDING", "RUNNING", terminal]))
    result = json.loads(execute({"run_id": "original-id", "wait_timeout": 1}))
    assert result["status"] == terminal
    assert result["wait_timed_out"] is False
    assert [refresh for _, refresh in service.calls] == [False, True, True, True]
    assert {run_id for run_id, _ in service.calls} == {"original-id"}


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELLED", "PREPARED"])
def test_saved_terminal_or_unsubmitted_run_returns_without_polling(install_service, status):
    service = install_service(StatusService([], saved=status))
    result = json.loads(execute({"run_id": "original-id", "wait_timeout": 300}))
    assert result["status"] == status and result["wait_timed_out"] is False
    assert service.calls == [("original-id", False)]


@pytest.mark.parametrize("status", ["RUNNING", "PENDING", "UNKNOWN"])
def test_wait_timeout_preserves_real_status_and_original_run_id(install_service, status):
    service = install_service(StatusService([status], saved=status))
    result = json.loads(execute({"run_id": "original-id", "wait_timeout": 0.03}))
    assert result["status"] == status
    assert result["run_id"] == "original-id"
    assert result["wait_timed_out"] is True
    assert result["waited_seconds"] >= 0.025
    assert {run_id for run_id, _ in service.calls} == {"original-id"}


def test_unknown_status_can_recover_during_wait(install_service):
    install_service(StatusService(["UNKNOWN", "RUNNING", "SUCCEEDED"]))
    result = json.loads(execute({"run_id": "original-id", "wait_timeout": 1}))
    assert result["status"] == "SUCCEEDED" and result["wait_timed_out"] is False


@pytest.mark.parametrize("value", [-1, True, "5", None, float("nan"), float("inf")])
def test_invalid_wait_timeout_does_not_query_service(install_service, value):
    service = install_service(StatusService([]))
    result = execute({"run_id": "original-id", "wait_timeout": value})
    assert result.startswith("Error: wait_timeout")
    assert service.calls == []


def test_backend_error_is_returned_without_retrying_or_resubmitting(install_service):
    service = install_service(StatusService([RuntimeError("status unavailable")]))
    assert execute({"run_id": "original-id", "wait_timeout": 1}) == "Error: status unavailable"
    assert len(service.calls) == 2


def test_unknown_run_id_is_reported_in_wait_mode(install_service):
    def missing(*args, **kwargs):
        raise ValueError("unknown run_id")
    install_service(SimpleNamespace(get=missing))
    assert execute({"run_id": "missing", "wait_timeout": 1}) == "Error: unknown run_id"


def test_wait_deadline_bounds_a_slow_status_query(install_service):
    release, finished = threading.Event(), threading.Event()

    def get(run_id, *, refresh=True):
        if not refresh:
            return {"run_id": run_id, "status": "RUNNING"}
        try:
            release.wait(2)
            return {"run_id": run_id, "status": "SUCCEEDED"}
        finally:
            finished.set()

    install_service(SimpleNamespace(get=get))

    async def run():
        try:
            # The backend thread may finish its one query, but cannot hold the async wait open.
            result = json.loads(await asyncio.wait_for(
                simulation_tools.get_simulation("original-id", wait_timeout=0.03), 0.5,
            ))
            assert result["status"] == "RUNNING" and result["wait_timed_out"] is True
        finally:
            release.set()
            await asyncio.to_thread(finished.wait, 2)

    asyncio.run(run())


def test_cancellation_stops_polling_during_sleep(install_service):
    service = install_service(StatusService(["RUNNING"]))

    async def run():
        task = asyncio.create_task(simulation_tools.get_simulation("original-id", wait_timeout=60))
        while len(service.calls) < 2:
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        calls = len(service.calls)
        await asyncio.sleep(0.03)
        assert len(service.calls) == calls

    asyncio.run(run())


def test_cancellation_during_backend_query_stops_wait_without_stopping_job(install_service):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    queries = []

    def get(run_id, *, refresh=True):
        queries.append(refresh)
        if refresh:
            started.set()
            release.wait(2)
            finished.set()
        return {"run_id": run_id, "status": "RUNNING"}

    install_service(SimpleNamespace(get=get))

    async def run():
        task = asyncio.create_task(simulation_tools.get_simulation("original-id", wait_timeout=60))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.5)
        finally:
            release.set()
            await asyncio.to_thread(finished.wait, 2)
        await asyncio.sleep(0.03)
        assert queries == [False, True]

    asyncio.run(run())


def test_waits_for_real_detached_local_worker_exit(tmp_path, monkeypatch, install_service):
    project = tmp_path / "project"
    project.mkdir()
    (project / "in.sim").write_text("test launcher\n", encoding="utf-8")
    service = SimulationService(SimulationConfig(
        enabled=True, submission_check_delay_sec=0,
        targets={"local-test": {"scheduler": "local", "engine": "lammps", "executable": sys.executable}},
    ), tmp_path / "home")
    monkeypatch.setattr(LocalTarget, "stage", lambda *args: None)
    monkeypatch.setattr(LocalTarget, "render_script", lambda self, *, run_id, entry, working_dir: json.dumps({
        "argv": [sys.executable, "-c", "import time; from pathlib import Path; time.sleep(0.1); Path('result.txt').write_text('done')"],
        "working_dir": working_dir,
    }))
    prepared = service.prepare_local("in.sim", "local-test", workspace=project)
    submitted = service.submit(prepared["run_id"])
    install_service(service)
    result = json.loads(execute({"run_id": submitted["run_id"], "wait_timeout": 5}))
    assert result["status"] == "SUCCEEDED"
    assert result["wait_timed_out"] is False
    assert result["job_id"] == submitted["job_id"]
    assert (project / "result.txt").read_text() == "done"
