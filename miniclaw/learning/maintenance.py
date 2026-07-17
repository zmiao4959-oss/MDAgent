"""Conservative periodic maintenance for the self-evolution stores."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import Dict

from .experience import ExperienceStore
from .trace import TraceStore


@dataclass
class MaintenanceReport:
    merged: int
    archived: int
    review_due: int
    traces_removed: int
    report_id: str = ""
    created_at: float = 0.0

    def __post_init__(self):
        if not self.report_id:
            self.report_id = uuid.uuid4().hex
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> Dict:
        return asdict(self)


class MaintenanceManager:
    def __init__(self, experience_store: ExperienceStore, trace_store: TraceStore):
        self.experience_store = experience_store
        self.trace_store = trace_store

    def run(
        self,
        *,
        candidate_ttl_days: int = 90,
        verified_review_days: int = 180,
        trace_retention_days: int = 90,
        now: float | None = None,
    ) -> MaintenanceReport:
        current = now or time.time()
        result = self.experience_store.maintain_experiences(
            now=current,
            candidate_ttl_days=candidate_ttl_days,
            verified_review_days=verified_review_days,
        )
        traces_removed = self.trace_store.prune_before(
            current - trace_retention_days * 86400
        )
        report = MaintenanceReport(
            merged=result["merged"],
            archived=result["archived"],
            review_due=result["review_due"],
            traces_removed=traces_removed,
            created_at=current,
        )
        self.experience_store.save_maintenance_report(report.to_dict())
        return report
