"""Machine-local FIFO admission for GPUMD workers, shared across projects/targets.

Detached workers wait here before starting GPUMD. SQLite transactions reserve
slots across processes. Missing workers release slots only after their native
or WSL children are confirmed absent. Inconclusive probes retain reservations.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import os
from contextlib import closing, contextmanager
from pathlib import Path

import psutil

if __package__:
    from .local_process import child_is_alive, worker_is_alive
else:
    from local_process import child_is_alive, worker_is_alive


class LocalGpuQueue:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, capacity INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO policy VALUES (1, 1)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "ticket INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE NOT NULL, "
                "workdir TEXT NOT NULL, state TEXT NOT NULL, pid INTEGER, birth REAL, "
                "submitter_pid INTEGER, submitter_birth REAL)"
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def configure(self, capacity: int):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("local GPUMD concurrency must be a positive integer")
        with self._connect() as db:
            db.execute("UPDATE policy SET capacity=? WHERE id=1", (capacity,))

    def register(self, run_id: str, workdir: Path | str):
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO jobs(run_id,workdir,state,submitter_pid,submitter_birth) "
                       "VALUES(?,?,'WAITING',?,?)",
                       (run_id, str(workdir), os.getpid(), psutil.Process().create_time()))

    def attach(self, run_id: str, pid: int):
        birth = psutil.Process(pid).create_time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["state"] != "WAITING" or row["pid"] is not None:
                raise RuntimeError("GPUMD queue ticket is missing or already owned; refusing duplicate execution")
            db.execute("UPDATE jobs SET pid=?,birth=? WHERE run_id=?", (pid, birth, run_id))

    @staticmethod
    def _alive(pid, birth=None):
        if pid is None:
            return False
        try:
            process = psutil.Process(int(pid))
            return (process.is_running() and process.status() != psutil.STATUS_ZOMBIE
                    and (birth is None or abs(process.create_time() - birth) < 0.01))
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True  # Unknown ownership must not cause oversubscription.

    @staticmethod
    def _reap(db, checks):
        rows = db.execute("SELECT * FROM jobs WHERE state IN ('WAITING','RUNNING','UNKNOWN')").fetchall()
        for row in rows:
            if (Path(row["workdir"]) / ".miniclaw-exit.json").is_file():
                db.execute("UPDATE jobs SET state='DONE' WHERE run_id=?", (row["run_id"],))
            elif row["pid"] is not None and not LocalGpuQueue._alive(row["pid"], row["birth"]):
                state = "ABANDONED" if row["state"] == "WAITING" else "UNKNOWN"
                if state == "UNKNOWN" and checks.get((row["workdir"], row["pid"], row["birth"])) is False:
                    state = "LOST"
                db.execute("UPDATE jobs SET state=? WHERE run_id=?", (state, row["run_id"]))
            elif (row["state"] == "WAITING" and row["pid"] is None
                  and not LocalGpuQueue._alive(row["submitter_pid"], row["submitter_birth"])):
                db.execute("UPDATE jobs SET state='ABANDONED' WHERE run_id=?", (row["run_id"],))

    @staticmethod
    def _legacy_records(runs_database: str | Path) -> list[dict]:
        path = Path(runs_database)
        if not path.is_file():
            return []
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=15)) as db:
            records = db.execute("SELECT data FROM runs WHERE status NOT IN ('PREPARED','SUCCEEDED','FAILED','CANCELLED')").fetchall()
        return [json.loads(data) for (data,) in records]

    @staticmethod
    def _legacy_pid(record):
        pid = record.get("job_id")
        marker = Path(record["remote_dir"]) / ".miniclaw-job-id"
        if not pid and marker.is_file():
            pid = marker.read_text(encoding="utf-8").strip()
        return pid

    def _recovery_checks(self, runs_database: str | Path) -> dict:
        """Slow WSL probes must never hold the queue's cross-process write lock."""
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs").fetchall()
        checks, probes = {}, {}
        known = {row["run_id"] for row in rows}
        for row in rows:
            if (row["state"] in {"RUNNING", "UNKNOWN"} and row["pid"] is not None
                    and not self._alive(row["pid"], row["birth"])):
                folder = Path(row["workdir"])
                if not (folder / ".miniclaw-exit.json").is_file():
                    checks[(row["workdir"], row["pid"], row["birth"])] = child_is_alive(folder, probes)
        for record in self._legacy_records(runs_database):
            if (record["run_id"] in known or record.get("scheduler") != "local"
                    or record.get("engine") != "gpumd"):
                continue
            folder = Path(record["remote_dir"])
            if (folder / ".miniclaw-exit.json").is_file():
                continue
            pid = self._legacy_pid(record)
            if worker_is_alive(folder, pid) is False:
                checks[(str(folder), pid, None)] = child_is_alive(folder, probes)
        return checks

    @staticmethod
    def _legacy_count(runs_database: str | Path, known: set[str], queue_db, checks) -> int:
        """Include pre-upgrade managed jobs without killing or relaunching them."""
        count = 0
        for record in LocalGpuQueue._legacy_records(runs_database):
            if (record["run_id"] in known or record.get("scheduler") != "local"
                    or record.get("engine") != "gpumd"):
                continue
            folder = Path(record["remote_dir"])
            if (folder / ".miniclaw-exit.json").is_file():
                continue
            pid = LocalGpuQueue._legacy_pid(record)
            if worker_is_alive(folder, pid) is False and checks.get((str(folder), pid, None)) is False:
                # Cache confirmed loss so admission does not repeatedly
                # probe the same historical record. Never relaunch it.
                queue_db.execute(
                    "INSERT OR IGNORE INTO jobs(run_id,workdir,state) VALUES(?,?,'LOST')",
                    (record["run_id"], str(folder)),
                )
                continue
            # A live child or an inconclusive probe must keep its reservation.
            count += 1
        return count

    def acquire(self, run_id: str, runs_database: str | Path) -> bool:
        checks = self._recovery_checks(runs_database)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reap(db, checks)
            row = db.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["state"] != "WAITING":
                raise RuntimeError("GPUMD queue ticket is no longer waiting")
            capacity = db.execute("SELECT capacity FROM policy WHERE id=1").fetchone()[0]
            used = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('RUNNING','UNKNOWN')").fetchone()[0]
            known = {r[0] for r in db.execute("SELECT run_id FROM jobs")}
            used += self._legacy_count(runs_database, known, db, checks)
            first = db.execute("SELECT run_id FROM jobs WHERE state='WAITING' ORDER BY ticket LIMIT 1").fetchone()
            if used >= capacity or first[0] != run_id:
                return False
            db.execute("UPDATE jobs SET state='RUNNING' WHERE run_id=?", (run_id,))
            return True

    def abandon(self, run_id: str):
        """Only a failed launcher (which never created a worker) may withdraw."""
        with self._connect() as db:
            db.execute("UPDATE jobs SET state='ABANDONED' WHERE run_id=? AND state='WAITING' AND pid IS NULL", (run_id,))

    def finish(self, run_id: str):
        with self._connect() as db:
            db.execute("UPDATE jobs SET state='DONE' WHERE run_id=?", (run_id,))

    def status(self, run_id: str, runs_database: str | Path) -> dict:
        checks = self._recovery_checks(runs_database)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reap(db, checks)
            row = db.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            capacity = db.execute("SELECT capacity FROM policy WHERE id=1").fetchone()[0]
            if row is None:
                return {"scheduler_state": "UNKNOWN", "reason": "GPUMD queue ticket is missing"}
            state = row["state"]
            result = {"source": "local-gpu-queue", "scheduler_state": state,
                      "max_concurrent": capacity, "queue_position": 0}
            if state == "WAITING":
                result["scheduler_state"] = "PENDING"
                result["queue_position"] = db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state='WAITING' AND ticket<=?", (row["ticket"],)
                ).fetchone()[0]
                known = {r[0] for r in db.execute("SELECT run_id FROM jobs")}
                active = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('RUNNING','UNKNOWN')").fetchone()[0]
                active += self._legacy_count(runs_database, known, db, checks)
                result["reason"] = (f"Waiting for a local GPU slot ({active} occupied, limit {capacity}); "
                                    "no GPUMD process has started for this run")
            elif state == "LOST":
                result["scheduler_state"] = "FAILED"
                result["reason"] = "GPU worker and simulation process are absent; exit code unknown; slot released"
            elif state in {"UNKNOWN", "ABANDONED"}:
                result["scheduler_state"] = "UNKNOWN" if state == "UNKNOWN" else "FAILED"
                result["reason"] = ("GPU worker disappeared without exit proof; slot retained, do not resubmit"
                                    if state == "UNKNOWN" else "Queued worker disappeared before starting GPUMD")
            return result


def validate_queued_inputs(run_id: str, runs_database: str | Path):
    """Reject changed inputs at admission, rather than run a stale queue request."""
    path = Path(runs_database).resolve()
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=15)) as db:
        row = db.execute("SELECT data FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError("queued simulation record is missing")
    record = json.loads(row[0])
    root = Path(record.get("working_dir") or Path(record["local_dir"]) / "input")
    for item in record["files"]:
        path = root / item["path"]
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise RuntimeError(f"queued input was changed; refusing to start GPUMD: {item['path']}")
