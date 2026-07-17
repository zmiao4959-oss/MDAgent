"""Stage 11 acceptance: conservative maintenance and persisted reports."""
import time
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.experience import Experience
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def main():
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        now = time.time()
        old = now - 120 * 86400
        service.experience_store.observe(Experience(
            experience_id="old", fingerprint="old", situation="old",
            lesson="unused candidate", task_pattern="old",
            created_at=old, updated_at=old, last_used_at=old,
        ))
        trace = EvolutionTrace(chat_id="old", objective="old", started_at=old)
        trace.finish(success=True, final_response="done")
        trace.ended_at = old
        service.trace_store.append(trace)

        report = service.maintain(
            candidate_ttl_days=90,
            verified_review_days=180,
            trace_retention_days=90,
            now=now,
        )
        assert report["archived"] == 1 and report["traces_removed"] == 1
        assert service.maintenance_reports()[0]["report_id"] == report["report_id"]
        assert service.maintain_if_due(interval_hours=24) is None
    print("Stage 11 accepted: deduplication, expiry, trace pruning, scheduling, and reports work.")


if __name__ == "__main__":
    main()
