"""SQLite records for prepared and submitted simulations."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "run_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
                "data TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS runs_status ON runs(status)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def create(self, record: dict[str, Any]) -> None:
        record = dict(record)
        record.setdefault("created_at", _now())
        record["updated_at"] = _now()
        with self._connect() as db:
            db.execute(
                "INSERT INTO runs(run_id,status,data,updated_at) VALUES(?,?,?,?)",
                (record["run_id"], record["status"], json.dumps(record, ensure_ascii=False), record["updated_at"]),
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT data FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def update(
        self,
        run_id: str,
        changes: dict[str, Any],
        *,
        expected: set[str] | None = None,
    ) -> dict[str, Any] | None:
        """Atomically change one record; expected guards duplicate submission."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return None
            record = json.loads(row["data"])
            if expected is not None and record["status"] not in expected:
                return None
            record.update(changes)
            record["updated_at"] = _now()
            db.execute(
                "UPDATE runs SET status=?,data=?,updated_at=? WHERE run_id=?",
                (record["status"], json.dumps(record, ensure_ascii=False), record["updated_at"], run_id),
            )
            return record

