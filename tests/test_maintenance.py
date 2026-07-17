import sqlite3
import time

from miniclaw.learning.experience import Experience
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def test_maintenance_merges_archives_reviews_and_prunes(tmp_path):
    service = EvolutionService(tmp_path)
    now = time.time()
    old = now - 200 * 86400
    store = service.experience_store

    store.observe(Experience(
        experience_id="dup-a", fingerprint="dup-a", situation="same task",
        lesson="Validate input file before running simulation", task_pattern="lammps",
        created_at=now, updated_at=now,
    ))
    store.observe(Experience(
        experience_id="dup-b", fingerprint="dup-b", situation="same task",
        lesson="Validate input file before running simulation", task_pattern="lammps",
        created_at=now, updated_at=now,
    ))
    store.observe(Experience(
        experience_id="stale", fingerprint="stale", situation="old",
        lesson="Old unused candidate", task_pattern="old",
        created_at=old, updated_at=old, last_used_at=old,
    ))

    for _ in range(3):
        trace = EvolutionTrace(chat_id="c", objective="read project documentation")
        trace.add_tool("read", {}, "ok")
        trace.finish(success=True, final_response="done")
        verified = service.complete_trace(trace)
    assert verified is not None
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE experiences SET last_used_at = ? WHERE experience_id = ?",
            (old, verified.experience_id),
        )

    old_trace = EvolutionTrace(chat_id="old", objective="old trace", started_at=old)
    old_trace.finish(success=True, final_response="done")
    old_trace.ended_at = old
    service.trace_store.append(old_trace)

    report = service.maintain(
        candidate_ttl_days=90,
        verified_review_days=180,
        trace_retention_days=90,
        now=now,
    )

    assert report["merged"] == 1
    assert report["archived"] == 1
    assert report["review_due"] == 1
    assert report["traces_removed"] == 1
    statuses = {item.experience_id: item.status for item in store.all()}
    assert statuses["stale"] == "archived"
    assert statuses[verified.experience_id] == "pending_evaluation"
    assert service.maintenance_reports()[0]["report_id"] == report["report_id"]


def test_maintenance_interval_prevents_duplicate_runs(tmp_path):
    service = EvolutionService(tmp_path)
    first = service.maintain_if_due(interval_hours=24)
    second = service.maintain_if_due(interval_hours=24)

    assert first is not None
    assert second is None
