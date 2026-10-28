"""Exercise real detached workers with harmless Python children, never a GPU."""
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import psutil

from miniclaw.settings import SimulationConfig
from miniclaw.simulation.local import LocalTarget
from miniclaw.simulation.local_queue import LocalGpuQueue
from miniclaw.simulation.service import SimulationService


def eventually(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.03)
    assert predicate(), "detached worker did not reach expected state"


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    raw = {"scheduler": "local", "engine": "gpumd", "executable": "/test/gpumd",
           "wsl_distribution": "TestLinux"}
    settings = SimulationConfig(enabled=True, submission_check_delay_sec=0,
                                targets={"local-gpumd": raw, "alias": dict(raw)})
    service = SimulationService(settings, tmp_path / "home")
    monkeypatch.setattr(LocalTarget, "stage", lambda *args: None)
    # Keep the production launcher and queue metadata; replace only the executable.
    def argv(self, entry, workdir):
        return [sys.executable, "-c",
                "import time,sys; from pathlib import Path; "
                "Path('started').write_text(str(time.monotonic())); "
                "deadline=time.monotonic()+20; "
                "exec(\"while not Path('release').exists() and time.monotonic()<deadline: time.sleep(0.02)\"); "
                "Path('finished').write_text(str(time.monotonic())); "
                "sys.exit(7 if Path('fail').exists() else 0)"]
    monkeypatch.setattr(LocalTarget, "_argv", argv)
    created = []

    def prepare(name, target="local-gpumd", svc=service):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "run.in").write_text("run 100\n")
        prepared = svc.prepare_local("run.in", target, workspace=folder)
        created.append((folder, svc, prepared["run_id"]))
        return folder, prepared["run_id"]

    yield service, prepare
    for folder, _, _ in created:
        (folder / "release").touch()
    # Wait for every actually submitted worker so no background jobs leak from tests.
    for folder, svc, run_id in created:
        if svc.store.get(run_id).get("job_id"):
            eventually(lambda: (Path(svc.store.get(run_id)["remote_dir"]) / ".miniclaw-exit.json").exists()
                       or svc.get(run_id)["status"] == "FAILED")


def test_default_one_slot_fifo_across_projects_targets_and_service_restart(jobs):
    service, prepare = jobs
    a, aid = prepare("project A")
    b, bid = prepare("project B", "alias")
    c, cid = prepare("project C")
    service.submit(aid)
    eventually(lambda: (a / "started").exists())
    service.submit(bid)
    service.submit(cid)
    assert service.get(aid)["status"] == "RUNNING"
    for rid, position in ((bid, 1), (cid, 2)):
        status = service.get(rid)
        assert status["status"] == "PENDING"
        assert status["queue_position"] == position
        assert status["max_concurrent"] == 1
        assert "no GPUMD process has started" in status["reason"]
    assert not (b / "started").exists() and not (c / "started").exists()
    restarted = SimulationService(service.settings, service.home)
    # No status polling is needed for queue advancement.
    (a / "release").touch()
    eventually(lambda: (b / "started").exists())
    assert not (c / "started").exists()
    assert restarted.get(aid)["status"] == "SUCCEEDED"
    (b / "release").touch()
    eventually(lambda: (c / "started").exists())
    assert float((b / "started").read_text()) >= float((a / "finished").read_text())
    assert float((c / "started").read_text()) >= float((b / "finished").read_text())


def test_two_slots_allow_overlap_without_starting_a_third(jobs):
    service, prepare = jobs
    service.settings.local_gpumd_max_concurrent = 2
    LocalGpuQueue(service.gpu_queue_path).configure(2)
    a, aid = prepare("A")
    b, bid = prepare("B")
    c, cid = prepare("C")
    for rid in (aid, bid, cid):
        service.submit(rid)
    eventually(lambda: (a / "started").exists() and (b / "started").exists())
    assert not (c / "started").exists()
    assert service.get(cid)["status"] == "PENDING"
    (b / "release").touch()
    eventually(lambda: (c / "started").exists())
    assert not (a / "finished").exists()


def test_failed_child_releases_slot_and_is_reported(jobs):
    service, prepare = jobs
    a, aid = prepare("bad")
    b, bid = prepare("next")
    (a / "fail").touch()
    service.submit(aid)
    service.submit(bid)
    (a / "release").touch()
    eventually(lambda: (b / "started").exists())
    result = service.get(aid)
    assert result["status"] == "FAILED" and result["exit_code"] == "7:0"


def test_changed_queued_script_fails_without_executing_and_next_starts(jobs):
    service, prepare = jobs
    a, aid = prepare("A")
    b, bid = prepare("changed")
    c, cid = prepare("C")
    service.submit(aid)
    eventually(lambda: (a / "started").exists())
    service.submit(bid)
    service.submit(cid)
    (b / "run.in").write_text("run 99999\n")
    (a / "release").touch()
    eventually(lambda: (c / "started").exists())
    assert not (b / "started").exists()
    result = service.get(bid)
    assert result["status"] == "FAILED"
    assert "queued input was changed" in result["reason"]


def test_repeated_submit_keeps_same_queued_ticket_and_worker(jobs):
    service, prepare = jobs
    a, aid = prepare("A")
    b, bid = prepare("B")
    service.submit(aid)
    eventually(lambda: (a / "started").exists())
    first = service.submit(bid)
    second = service.submit(bid)
    assert first["job_id"] == second["job_id"]
    assert first["status"] == second["status"] == "PENDING"
    with sqlite3.connect(service.gpu_queue_path) as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE run_id=?", (bid,)).fetchone()[0] == 1


def test_pre_upgrade_running_job_blocks_queue_without_being_restarted(jobs):
    service, prepare = jobs
    a, aid = prepare("legacy")
    # Represent a worker submitted before this feature, still holding the GPU.
    record = service.store.get(aid)
    service.store.update(aid, {"status": "RUNNING", "job_id": str(os.getpid())})
    b, bid = prepare("new")
    service.submit(bid)
    assert service.get(bid)["status"] == "PENDING"
    time.sleep(1.2)
    assert not (b / "started").exists()
    marker = Path(record["remote_dir"]) / ".miniclaw-exit.json"
    marker.write_text('{"exit_code":0,"error":""}')
    eventually(lambda: (b / "started").exists())
    # The legacy record is unchanged; admission only reads its completion proof.
    assert service.store.get(aid)["status"] == "RUNNING"


def queue_job(queue, tmp_path, rid):
    folder = tmp_path / rid
    folder.mkdir()
    queue.register(rid, folder)
    queue.attach(rid, os.getpid())
    return folder


def test_atomic_cross_process_reservation_and_fifo(tmp_path):
    queue = LocalGpuQueue(tmp_path / "queue.sqlite3")
    for rid in ("a", "b", "c"):
        queue_job(queue, tmp_path, rid)
    # Each caller creates an independent queue connection, as different workers do.
    def take(rid):
        return LocalGpuQueue(queue.path).acquire(rid, tmp_path / "missing.sqlite3")
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(take, ("c", "b", "a"))) == [False, False, True]
    assert queue.status("b", "missing")["queue_position"] == 1


def test_unknown_running_owner_retains_gpu_slot_and_exit_proof_releases_it(tmp_path, monkeypatch):
    queue = LocalGpuQueue(tmp_path / "queue.sqlite3")
    folder = queue_job(queue, tmp_path, "a")
    queue_job(queue, tmp_path, "b")
    assert queue.acquire("a", tmp_path / "missing")
    with sqlite3.connect(queue.path) as db:
        db.execute("UPDATE jobs SET pid=9999999 WHERE run_id='a'")
    monkeypatch.setattr(LocalGpuQueue, "_alive", staticmethod(lambda pid, birth: pid != 9999999))
    assert queue.status("a", "missing")["scheduler_state"] == "UNKNOWN"
    monkeypatch.setattr(LocalGpuQueue, "_alive", staticmethod(lambda *args: True))
    assert not queue.acquire("b", tmp_path / "missing")
    (folder / ".miniclaw-exit.json").write_text('{"exit_code":0}')
    assert queue.acquire("b", tmp_path / "missing")


@pytest.mark.parametrize("child", [True, None, False])
def test_dead_running_owner_releases_only_after_child_absence_is_proven(tmp_path, monkeypatch, child):
    queue = LocalGpuQueue(tmp_path / "queue.sqlite3")
    queue_job(queue, tmp_path, "a")
    queue_job(queue, tmp_path, "b")
    assert queue.acquire("a", tmp_path / "missing")
    with sqlite3.connect(queue.path) as db:
        db.execute("UPDATE jobs SET pid=9999999 WHERE run_id='a'")
    monkeypatch.setattr(LocalGpuQueue, "_alive", staticmethod(lambda pid, birth: pid != 9999999))
    monkeypatch.setattr("miniclaw.simulation.local_queue.child_is_alive", lambda *args: child)
    assert queue.acquire("b", tmp_path / "missing") is (child is False)
    status = queue.status("a", tmp_path / "missing")
    assert status["scheduler_state"] == ("FAILED" if child is False else "UNKNOWN")
    assert "exit_code" not in status


def test_twenty_stale_legacy_runs_do_not_block_and_recovery_is_cached(jobs, monkeypatch):
    service, prepare = jobs
    legacy = []
    for i in range(20):
        _, rid = prepare(f"legacy-{i}")
        service.store.update(rid, {"status": "RUNNING", "job_id": "9999999"})
        legacy.append(rid)
    b, bid = prepare("new")
    monkeypatch.setattr("miniclaw.simulation.local_queue.worker_is_alive", lambda *args: False)
    probes = []
    monkeypatch.setattr("miniclaw.simulation.local_queue.child_is_alive", lambda *args: probes.append(args[0]) or False)
    # Direct admission avoids fixture teardown expecting nonexistent legacy workers.
    queue = LocalGpuQueue(service.gpu_queue_path)
    queue.register(bid, Path(service.store.get(bid)["remote_dir"]))
    queue.attach(bid, os.getpid())
    assert queue.acquire(bid, service.store.path)
    assert len(probes) == 20
    with sqlite3.connect(queue.path) as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE state='LOST'").fetchone()[0] == 20
    queue.status(bid, service.store.path)
    assert len(probes) == 20  # Historical jobs are not probed on every poll.
    for rid in legacy:
        service.store.update(rid, {"status": "FAILED", "job_id": ""})


def test_recovery_probe_does_not_hold_queue_write_lock(tmp_path, monkeypatch):
    queue = LocalGpuQueue(tmp_path / "queue.sqlite3")
    queue_job(queue, tmp_path, "a")
    queue_job(queue, tmp_path, "b")
    assert queue.acquire("a", tmp_path / "missing")
    with sqlite3.connect(queue.path) as db:
        db.execute("UPDATE jobs SET pid=9999999 WHERE run_id='a'")
    monkeypatch.setattr(LocalGpuQueue, "_alive", staticmethod(lambda pid, birth: pid != 9999999))
    def probe(*args):
        # A second process can still acquire the write lock during a slow probe.
        with sqlite3.connect(queue.path, timeout=0.1) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE policy SET capacity=1 WHERE id=1")
        return False
    monkeypatch.setattr("miniclaw.simulation.local_queue.child_is_alive", probe)
    assert queue.acquire("b", tmp_path / "missing")


def test_killed_worker_does_not_release_live_child_but_recovers_after_child_exits(jobs):
    service, prepare = jobs
    a, aid = prepare("orphan")
    b, bid = prepare("next")
    service.submit(aid)
    record = service.store.get(aid)
    metadata = Path(record["remote_dir"])
    eventually(lambda: (a / "started").exists() and (metadata / ".miniclaw-child.json").exists())
    service.submit(bid)
    worker = psutil.Process(int(record["job_id"]))
    worker.kill()
    worker.wait(timeout=5)
    assert service.get(aid)["status"] == "UNKNOWN"
    assert service.get(bid)["status"] == "PENDING"
    assert not (b / "started").exists()
    (a / "release").touch()
    eventually(lambda: (b / "started").exists())
    result = service.get(aid)
    assert result["status"] == "FAILED"
    assert result["exit_code"] == ""
    assert "exit code unknown" in result["reason"]
    assert not (metadata / ".miniclaw-exit.json").exists()


def test_dead_waiting_owner_is_removed_without_holding_a_slot(tmp_path, monkeypatch):
    queue = LocalGpuQueue(tmp_path / "queue.sqlite3")
    queue_job(queue, tmp_path, "a")
    queue_job(queue, tmp_path, "b")
    pid = os.getpid()
    with sqlite3.connect(queue.path) as db:
        db.execute("UPDATE jobs SET pid=? WHERE run_id='a'", (pid + 1,))
    monkeypatch.setattr(LocalGpuQueue, "_alive", staticmethod(lambda owner, birth: owner == pid))
    assert queue.acquire("b", tmp_path / "missing")
    assert queue.status("a", "missing")["scheduler_state"] == "FAILED"


def test_duplicate_worker_cannot_take_owned_ticket(tmp_path):
    queue = LocalGpuQueue(tmp_path / "queue.sqlite3")
    queue_job(queue, tmp_path, "a")
    with pytest.raises(RuntimeError, match="already owned"):
        queue.attach("a", os.getpid())


def test_failed_process_creation_is_terminal_and_does_not_poison_fifo(jobs, monkeypatch):
    service, prepare = jobs
    folder, run_id = prepare("launch-failure")
    def fail(*args, **kwargs):
        raise OSError("test process creation failed")
    with monkeypatch.context() as patch:
        patch.setattr("miniclaw.simulation.local.subprocess.Popen", fail)
        result = service.submit(run_id)
    assert result["status"] == "FAILED"
    assert "test process creation failed" in result["reason"]
    next_folder, next_id = prepare("next")
    service.submit(next_id)
    eventually(lambda: (next_folder / "started").exists())


def test_unlaunched_ticket_from_dead_submitter_does_not_block_fifo(tmp_path, monkeypatch):
    queue = LocalGpuQueue(tmp_path / "queue.sqlite3")
    queue.register("a", tmp_path / "a")
    queue_job(queue, tmp_path, "b")
    with sqlite3.connect(queue.path) as db:
        db.execute("UPDATE jobs SET submitter_pid=9999999 WHERE run_id='a'")
    monkeypatch.setattr(LocalGpuQueue, "_alive", staticmethod(lambda pid, birth: pid != 9999999))
    assert queue.acquire("b", tmp_path / "missing")


def test_pre_upgrade_prepared_launcher_fails_before_submission_and_does_not_hold_slot(tmp_path):
    raw = {"scheduler": "local", "engine": "gpumd", "executable": "/test/gpumd", "wsl_distribution": "TestLinux"}
    service = SimulationService(SimulationConfig(enabled=True, targets={"local-gpumd": raw}), tmp_path / "home")
    (tmp_path / "run.in").write_text("run 1\n")
    prepared = service.prepare_local("run.in", "local-gpumd", workspace=tmp_path)
    record = service.store.get(prepared["run_id"])
    launcher = Path(record["remote_dir"]) / "launch.json"
    settings = json.loads(launcher.read_text())
    settings.pop("gpu_queue")
    launcher.write_text(json.dumps(settings))
    from miniclaw.simulation.service import _sha256
    from miniclaw.simulation.remote import RemoteError
    service.store.update(prepared["run_id"], {"script_sha256": _sha256(launcher)})
    with pytest.raises(RemoteError, match="prepare a new run"):
        service.submit(prepared["run_id"])
    assert service.get(prepared["run_id"])["status"] == "PREPARED"


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2", None])
def test_invalid_limit_is_rejected(value):
    with pytest.raises(ValueError, match="positive integer"):
        SimulationConfig(local_gpumd_max_concurrent=value)
