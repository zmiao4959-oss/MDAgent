"""Evidence-based experience lifecycle for controlled self-evolution."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .trace import EvolutionTrace


@dataclass
class Experience:
    experience_id: str
    fingerprint: str
    situation: str
    lesson: str
    task_pattern: str = ""
    scope: List[str] = field(default_factory=list)
    failure_pattern: str = ""
    status: str = "candidate"
    positive_evidence: int = 0
    negative_evidence: int = 0
    observations: int = 1
    usage_count: int = 0
    confidence: float = 0.5
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)


class ExperienceStore:
    """SQLite-backed experience store with evidence-controlled promotion."""

    PROMOTION_EVIDENCE = 3
    REJECTION_EVIDENCE = 2
    HALF_LIFE_DAYS = 90.0

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiences (
                    experience_id TEXT PRIMARY KEY,
                    fingerprint TEXT UNIQUE NOT NULL,
                    situation TEXT NOT NULL,
                    lesson TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    failure_pattern TEXT NOT NULL,
                    status TEXT NOT NULL,
                    positive_evidence INTEGER NOT NULL,
                    negative_evidence INTEGER NOT NULL,
                    observations INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_used_at REAL NOT NULL,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    task_pattern TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(experiences)")
            }
            if "usage_count" not in columns:
                connection.execute(
                    "ALTER TABLE experiences ADD COLUMN usage_count INTEGER NOT NULL DEFAULT 0"
                )
            if "task_pattern" not in columns:
                connection.execute(
                    "ALTER TABLE experiences ADD COLUMN task_pattern TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    """
                    UPDATE experiences
                    SET status = 'candidate', confidence = MIN(confidence, 0.5)
                    WHERE task_pattern = '' AND status = 'verified'
                    """
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experience_evaluations (
                    evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experience_id TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_versions (
                    version INTEGER PRIMARY KEY AUTOINCREMENT,
                    experience_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experience_reflections (
                    reflection_id TEXT PRIMARY KEY,
                    experience_id TEXT NOT NULL,
                    situation TEXT NOT NULL,
                    lesson TEXT NOT NULL,
                    failure_pattern TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    reviewed_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS canary_trials (
                    experience_id TEXT PRIMARY KEY,
                    uses INTEGER NOT NULL DEFAULT 0,
                    successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS replay_reports (
                    replay_id TEXT PRIMARY KEY,
                    experience_id TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS maintenance_reports (
                    report_id TEXT PRIMARY KEY,
                    report_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experience_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    experience_id TEXT NOT NULL,
                    project_id TEXT NOT NULL DEFAULT '',
                    chat_id TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    positive INTEGER NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    success INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    tokens INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experience_evidence_experience
                ON experience_evidence(experience_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experience_imports (
                    source_path TEXT PRIMARY KEY,
                    imported_at REAL NOT NULL,
                    imported_count INTEGER NOT NULL
                )
                """
            )

    def observe(
        self,
        candidate: Experience,
        *,
        positive: bool = False,
        evidence: Optional[Dict] = None,
    ) -> Experience:
        """Insert a candidate or merge another observation into the same strategy."""
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM experiences WHERE fingerprint = ?", (candidate.fingerprint,)
            ).fetchone()
            if row:
                current = self._from_row(row)
                current.observations += 1
                if positive:
                    current.positive_evidence += 1
                current.updated_at = time.time()
                self._reclassify(current)
                self._update(connection, current)
                self._record_evidence(connection, current.experience_id, positive, evidence)
                return current

            if positive:
                candidate.positive_evidence = 1
            self._reclassify(candidate)
            self._insert(connection, candidate)
            self._record_evidence(connection, candidate.experience_id, positive, evidence)
            return candidate

    def record_feedback(
        self,
        experience_id: str,
        *,
        positive: bool,
        evidence: Optional[Dict] = None,
    ) -> Optional[Experience]:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM experiences WHERE experience_id = ?", (experience_id,)
            ).fetchone()
            if not row:
                return None
            experience = self._from_row(row)
            if positive:
                experience.positive_evidence += 1
            else:
                experience.negative_evidence += 1
            experience.updated_at = time.time()
            experience.last_used_at = experience.updated_at
            self._reclassify(experience)
            self._update(connection, experience)
            self._record_evidence(connection, experience.experience_id, positive, evidence)
            return experience

    def evidence_sources(self, experience_id: str) -> Dict:
        """Return non-sensitive provenance for evidence accumulated across projects."""
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT project_id, COUNT(*) AS evidence_count,
                       SUM(positive) AS positive_count
                FROM experience_evidence
                WHERE experience_id = ?
                GROUP BY project_id ORDER BY evidence_count DESC, project_id
                """,
                (experience_id,),
            ).fetchall()
        sources = [
            {
                "project_id": row["project_id"] or "legacy-or-default",
                "evidence_count": int(row["evidence_count"]),
                "positive_count": int(row["positive_count"] or 0),
            }
            for row in rows
        ]
        return {
            "source_project_count": len({row["project_id"] for row in rows if row["project_id"]}),
            "sources": sources,
        }

    def import_database(self, source_path: Path | str) -> int:
        """Idempotently merge a legacy project-local experience database."""
        source = Path(source_path).resolve()
        if source == self.path.resolve() or not source.is_file():
            return 0
        source_key = str(source)
        with self._lock, closing(self._connect()) as connection:
            if connection.execute(
                "SELECT 1 FROM experience_imports WHERE source_path = ?", (source_key,)
            ).fetchone():
                return 0
        imported = ExperienceStore(source).all()
        with self._lock, closing(self._connect()) as connection, connection:
            if connection.execute(
                "SELECT 1 FROM experience_imports WHERE source_path = ?", (source_key,)
            ).fetchone():
                return 0
            for candidate in imported:
                row = connection.execute(
                    "SELECT * FROM experiences WHERE fingerprint = ?", (candidate.fingerprint,)
                ).fetchone()
                if row:
                    current = self._from_row(row)
                    current.positive_evidence += candidate.positive_evidence
                    current.negative_evidence += candidate.negative_evidence
                    current.observations += candidate.observations
                    current.usage_count += candidate.usage_count
                    current.created_at = min(current.created_at, candidate.created_at)
                    current.updated_at = max(current.updated_at, candidate.updated_at)
                    current.last_used_at = max(current.last_used_at, candidate.last_used_at)
                    if current.status != "verified" and candidate.status == "verified":
                        current.status = "verified"
                    self._reclassify(current)
                    self._update(connection, current)
                else:
                    self._insert(connection, candidate)
            connection.execute(
                "INSERT INTO experience_imports VALUES (?, ?, ?)",
                (source_key, time.time(), len(imported)),
            )
        return len(imported)

    @staticmethod
    def _record_evidence(
        connection: sqlite3.Connection,
        experience_id: str,
        positive: bool,
        evidence: Optional[Dict],
    ) -> None:
        payload = dict(evidence or {})
        seed = "|".join([
            experience_id,
            str(payload.get("source", "observation")),
            str(payload.get("project_id", "")),
            str(payload.get("chat_id", "")),
            str(payload.get("run_id", "")),
            str(time.time_ns() if not payload.get("run_id") else ""),
        ])
        evidence_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        connection.execute(
            """
            INSERT OR IGNORE INTO experience_evidence (
                evidence_id, experience_id, project_id, chat_id, run_id, source,
                positive, score, success, error_count, tokens, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                experience_id,
                str(payload.get("project_id", "")),
                str(payload.get("chat_id", "")),
                str(payload.get("run_id", "")),
                str(payload.get("source", "observation")),
                int(positive),
                float(payload.get("score", 0) or 0),
                int(bool(payload.get("success", positive))),
                int(payload.get("error_count", 0) or 0),
                int(payload.get("tokens", 0) or 0),
                float(payload.get("created_at", time.time()) or time.time()),
            ),
        )

    def mark_applied(self, experience_ids: Iterable[str]) -> List[Experience]:
        """Record that verified experiences were actually placed in an Agent prompt."""
        applied: List[Experience] = []
        now = time.time()
        with self._lock, closing(self._connect()) as connection, connection:
            for experience_id in dict.fromkeys(experience_ids):
                row = connection.execute(
                    """
                    SELECT * FROM experiences
                    WHERE experience_id = ? AND status IN ('verified', 'canary')
                    """,
                    (experience_id,),
                ).fetchone()
                if not row:
                    continue
                experience = self._from_row(row)
                experience.usage_count += 1
                experience.last_used_at = now
                experience.updated_at = now
                self._update(connection, experience)
                applied.append(experience)
        return applied

    def record_application_outcome(
        self,
        experience_ids: Iterable[str],
        *,
        positive: bool,
        canary_min_trials: int = 5,
        canary_max_failures: int = 2,
        canary_min_success_rate: float = 0.8,
    ) -> List[Experience]:
        updated = []
        for experience_id in dict.fromkeys(experience_ids):
            current = next(
                (item for item in self.all() if item.experience_id == experience_id),
                None,
            )
            if current is not None and current.status == "canary":
                experience = self.record_canary_outcome(
                    experience_id,
                    positive=positive,
                    min_trials=canary_min_trials,
                    max_failures=canary_max_failures,
                    min_success_rate=canary_min_success_rate,
                )
            else:
                experience = self.record_feedback(experience_id, positive=positive)
            if experience is not None:
                updated.append(experience)
        return updated

    def record_canary_outcome(
        self,
        experience_id: str,
        *,
        positive: bool,
        min_trials: int,
        max_failures: int,
        min_success_rate: float,
    ) -> Optional[Experience]:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM experiences WHERE experience_id = ? AND status = 'canary'",
                (experience_id,),
            ).fetchone()
            if not row:
                return None
            experience = self._from_row(row)
            trial = connection.execute(
                "SELECT * FROM canary_trials WHERE experience_id = ?", (experience_id,)
            ).fetchone()
            uses = int(trial["uses"] if trial else 0) + 1
            successes = int(trial["successes"] if trial else 0) + int(positive)
            failures = int(trial["failures"] if trial else 0) + int(not positive)
            now = time.time()
            connection.execute(
                """
                INSERT INTO canary_trials (
                    experience_id, uses, successes, failures, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(experience_id) DO UPDATE SET
                    uses=excluded.uses, successes=excluded.successes,
                    failures=excluded.failures, updated_at=excluded.updated_at
                """,
                (
                    experience_id, uses, successes, failures,
                    float(trial["started_at"] if trial else now), now,
                ),
            )
            if failures >= max_failures:
                experience.status = "rejected"
                experience.negative_evidence = max(
                    experience.negative_evidence, self.REJECTION_EVIDENCE
                )
                self._record_version(connection, experience, "canary_rollback")
            elif uses >= min_trials and successes / max(1, uses) >= min_success_rate:
                experience.status = "verified"
                self._record_version(connection, experience, "canary_promote")
            experience.updated_at = now
            self._update(connection, experience)
            return experience

    def canary_stats(self, experience_id: Optional[str] = None) -> List[Dict]:
        query = "SELECT * FROM canary_trials"
        params: tuple = ()
        if experience_id:
            query += " WHERE experience_id = ?"
            params = (experience_id,)
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def save_replay_report(self, report: Dict) -> Dict:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO replay_reports (
                    replay_id, experience_id, passed, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    report["replay_id"], report["experience_id"],
                    int(bool(report["passed"])),
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    float(report["created_at"]),
                ),
            )
        return report

    def replay_reports(self, limit: int = 100) -> List[Dict]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT report_json FROM replay_reports ORDER BY created_at DESC LIMIT ?",
                (max(0, limit),),
            ).fetchall()
        return [json.loads(row["report_json"]) for row in rows]

    def maintain_experiences(
        self,
        *,
        now: float,
        candidate_ttl_days: int,
        verified_review_days: int,
    ) -> Dict[str, int]:
        archived = 0
        review_due = 0
        merged = 0
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute("SELECT * FROM experiences").fetchall()
            experiences = [self._from_row(row) for row in rows]
            mergeable = [
                item for item in experiences
                if item.status in {"candidate", "pending_evaluation"}
            ]
            consumed = set()
            for index, keeper in enumerate(mergeable):
                if keeper.experience_id in consumed:
                    continue
                keeper_words = _words(keeper.lesson)
                for duplicate in mergeable[index + 1:]:
                    if duplicate.experience_id in consumed:
                        continue
                    if keeper.task_pattern != duplicate.task_pattern:
                        continue
                    duplicate_words = _words(duplicate.lesson)
                    union = keeper_words | duplicate_words
                    similarity = len(keeper_words & duplicate_words) / max(1, len(union))
                    if similarity < 0.85:
                        continue
                    keeper.positive_evidence += duplicate.positive_evidence
                    keeper.negative_evidence += duplicate.negative_evidence
                    keeper.observations += duplicate.observations
                    keeper.usage_count += duplicate.usage_count
                    keeper.updated_at = now
                    self._reclassify(keeper)
                    duplicate.status = "archived"
                    duplicate.updated_at = now
                    self._update(connection, duplicate)
                    consumed.add(duplicate.experience_id)
                    merged += 1
                self._update(connection, keeper)

            candidate_cutoff = now - candidate_ttl_days * 86400
            review_cutoff = now - verified_review_days * 86400
            for experience in experiences:
                if experience.experience_id in consumed:
                    continue
                if (
                    experience.status in {"candidate", "rejected"}
                    and experience.updated_at < candidate_cutoff
                ):
                    experience.status = "archived"
                    experience.updated_at = now
                    self._update(connection, experience)
                    archived += 1
                elif experience.status == "verified" and experience.last_used_at < review_cutoff:
                    experience.status = "pending_evaluation"
                    experience.updated_at = now
                    self._update(connection, experience)
                    review_due += 1
        return {"merged": merged, "archived": archived, "review_due": review_due}

    def save_maintenance_report(self, report: Dict) -> Dict:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO maintenance_reports VALUES (?, ?, ?)",
                (
                    report["report_id"],
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    float(report["created_at"]),
                ),
            )
        return report

    def maintenance_reports(self, limit: int = 100) -> List[Dict]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT report_json FROM maintenance_reports ORDER BY created_at DESC LIMIT ?",
                (max(0, limit),),
            ).fetchall()
        return [json.loads(row["report_json"]) for row in rows]

    def apply_evaluation(self, report: Dict) -> Optional[Experience]:
        experience_id = str(report.get("experience_id", ""))
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM experiences WHERE experience_id = ?", (experience_id,)
            ).fetchone()
            if not row:
                return None
            experience = self._from_row(row)
            target_status = str(report.get("target_status", "verified"))
            experience.status = target_status if report.get("passed") else "candidate"
            experience.updated_at = time.time()
            self._update(connection, experience)
            connection.execute(
                """
                INSERT INTO experience_evaluations (
                    experience_id, passed, report_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    experience_id,
                    int(bool(report.get("passed"))),
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    time.time(),
                ),
            )
            if report.get("passed"):
                action = "canary_start" if target_status == "canary" else "promote"
                self._record_version(connection, experience, action)
                if target_status == "canary":
                    now = time.time()
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO canary_trials (
                            experience_id, uses, successes, failures, started_at, updated_at
                        ) VALUES (?, 0, 0, 0, ?, ?)
                        """,
                        (experience.experience_id, now, now),
                    )
            return experience

    def rollback(self, experience_id: str) -> Optional[Experience]:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM experiences WHERE experience_id = ?", (experience_id,)
            ).fetchone()
            if not row:
                return None
            experience = self._from_row(row)
            self._record_version(connection, experience, "rollback")
            experience.status = "rejected"
            experience.negative_evidence = max(
                experience.negative_evidence, self.REJECTION_EVIDENCE
            )
            experience.updated_at = time.time()
            self._reclassify(experience)
            self._update(connection, experience)
            return experience

    def evaluations(self, limit: int = 100) -> List[Dict]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT evaluation_id, experience_id, passed, report_json, created_at
                FROM experience_evaluations ORDER BY evaluation_id DESC LIMIT ?
                """,
                (max(0, limit),),
            ).fetchall()
        return [
            {
                "evaluation_id": row["evaluation_id"],
                "experience_id": row["experience_id"],
                "passed": bool(row["passed"]),
                "report": json.loads(row["report_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def policy_versions(self, limit: int = 100) -> List[Dict]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT version, experience_id, action, snapshot_json, created_at
                FROM policy_versions ORDER BY version DESC LIMIT ?
                """,
                (max(0, limit),),
            ).fetchall()
        return [
            {
                "version": row["version"],
                "experience_id": row["experience_id"],
                "action": row["action"],
                "snapshot": json.loads(row["snapshot_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def save_reflection(self, proposal: Dict) -> Dict:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO experience_reflections (
                    reflection_id, experience_id, situation, lesson, failure_pattern,
                    scope_json, confidence, model, status, created_at, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    proposal["reflection_id"], proposal["experience_id"],
                    proposal["situation"], proposal["lesson"],
                    proposal.get("failure_pattern", ""),
                    json.dumps(proposal.get("scope", []), ensure_ascii=False),
                    float(proposal.get("confidence", 0.5)), proposal.get("model", ""),
                    "proposed", float(proposal.get("created_at", time.time())),
                ),
            )
        return proposal

    def reflections(self, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        query = "SELECT * FROM experience_reflections"
        params: List = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, limit))
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [
            {
                "reflection_id": row["reflection_id"],
                "experience_id": row["experience_id"],
                "situation": row["situation"],
                "lesson": row["lesson"],
                "failure_pattern": row["failure_pattern"],
                "scope": json.loads(row["scope_json"]),
                "confidence": row["confidence"],
                "model": row["model"],
                "status": row["status"],
                "created_at": row["created_at"],
                "reviewed_at": row["reviewed_at"],
            }
            for row in rows
        ]

    def review_reflection(
        self, reflection_id: str, *, approve: bool
    ) -> Optional[Experience]:
        with self._lock, closing(self._connect()) as connection, connection:
            reflection = connection.execute(
                "SELECT * FROM experience_reflections WHERE reflection_id = ?",
                (reflection_id,),
            ).fetchone()
            if not reflection or reflection["status"] != "proposed":
                return None
            row = connection.execute(
                "SELECT * FROM experiences WHERE experience_id = ?",
                (reflection["experience_id"],),
            ).fetchone()
            if not row:
                return None
            experience = self._from_row(row)
            if approve:
                self._record_version(connection, experience, "reflection_approved")
                experience.situation = reflection["situation"]
                experience.lesson = reflection["lesson"]
                experience.failure_pattern = reflection["failure_pattern"]
                experience.scope = json.loads(reflection["scope_json"])
                experience.status = "pending_evaluation"
                experience.updated_at = time.time()
                self._update(connection, experience)
            connection.execute(
                """
                UPDATE experience_reflections SET status = ?, reviewed_at = ?
                WHERE reflection_id = ?
                """,
                ("approved" if approve else "rejected", time.time(), reflection_id),
            )
            return experience

    @staticmethod
    def _record_version(
        connection: sqlite3.Connection,
        experience: Experience,
        action: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO policy_versions (
                experience_id, action, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                experience.experience_id,
                action,
                json.dumps(experience.__dict__, ensure_ascii=False, sort_keys=True),
                time.time(),
            ),
        )

    def verified_for(self, query: str, limit: int = 5) -> List[Experience]:
        return self.search_for(query, statuses=("verified",), limit=limit)

    def search_for(
        self,
        query: str,
        *,
        statuses: Iterable[str],
        limit: int = 5,
    ) -> List[Experience]:
        query_words = _words(query)
        now = time.time()
        allowed = tuple(dict.fromkeys(statuses))
        if not allowed:
            return []
        placeholders = ",".join("?" for _ in allowed)
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM experiences WHERE status IN ({placeholders}) AND task_pattern != ''",
                allowed,
            ).fetchall()
        scored = []
        for row in rows:
            experience = self._from_row(row)
            candidate_words = _words(
                " ".join([
                    experience.task_pattern,
                    experience.situation,
                    experience.lesson,
                    *experience.scope,
                ])
            )
            overlap = len(query_words & candidate_words)
            if not overlap:
                continue
            similarity = overlap / max(1, len(query_words | candidate_words))
            age_days = max(0.0, (now - experience.last_used_at) / 86400.0)
            freshness = math.exp(-math.log(2) * age_days / self.HALF_LIFE_DAYS)
            scored.append((similarity * experience.confidence * freshness, experience))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[: max(0, limit)]]

    def all(self, status: Optional[str] = None) -> List[Experience]:
        query = "SELECT * FROM experiences"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC"
        with self._lock, closing(self._connect()) as connection:
            return [self._from_row(row) for row in connection.execute(query, params).fetchall()]

    @staticmethod
    def _reclassify(experience: Experience) -> None:
        total = experience.positive_evidence + experience.negative_evidence
        experience.confidence = round(
            (experience.positive_evidence + 1) / (total + 2), 3
        )
        if experience.negative_evidence >= ExperienceStore.REJECTION_EVIDENCE:
            experience.status = "rejected"
        elif (
            experience.positive_evidence >= ExperienceStore.PROMOTION_EVIDENCE
            and experience.positive_evidence > experience.negative_evidence * 2
        ):
            if experience.status != "verified":
                experience.status = "pending_evaluation"
        else:
            experience.status = "candidate"

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Experience:
        return Experience(
            experience_id=row["experience_id"],
            fingerprint=row["fingerprint"],
            situation=row["situation"],
            lesson=row["lesson"],
            task_pattern=row["task_pattern"],
            scope=json.loads(row["scope_json"]),
            failure_pattern=row["failure_pattern"],
            status=row["status"],
            positive_evidence=row["positive_evidence"],
            negative_evidence=row["negative_evidence"],
            observations=row["observations"],
            usage_count=row["usage_count"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_used_at=row["last_used_at"],
        )

    @staticmethod
    def _insert(connection: sqlite3.Connection, experience: Experience) -> None:
        connection.execute(
            """
            INSERT INTO experiences (
                experience_id, fingerprint, situation, lesson, scope_json,
                failure_pattern, status, positive_evidence, negative_evidence,
                observations, confidence, created_at, updated_at, last_used_at,
                usage_count, task_pattern
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _values(experience),
        )

    @staticmethod
    def _update(connection: sqlite3.Connection, experience: Experience) -> None:
        connection.execute(
            """
            UPDATE experiences SET
                situation=?, lesson=?, scope_json=?, failure_pattern=?, status=?,
                positive_evidence=?, negative_evidence=?, observations=?, confidence=?,
                created_at=?, updated_at=?, last_used_at=?, usage_count=?, task_pattern=?
            WHERE experience_id=?
            """,
            (
                experience.situation, experience.lesson, json.dumps(experience.scope),
                experience.failure_pattern, experience.status, experience.positive_evidence,
                experience.negative_evidence, experience.observations, experience.confidence,
                experience.created_at, experience.updated_at, experience.last_used_at,
                experience.usage_count,
                experience.task_pattern,
                experience.experience_id,
            ),
        )


class ExperienceEngine:
    """Converts completed traces into conservative, reusable candidates."""

    def __init__(self, store: ExperienceStore):
        self.store = store

    def observe_trace(self, trace: EvolutionTrace) -> Optional[Experience]:
        candidate = self._candidate_from_trace(trace)
        if candidate is None:
            return None
        return self.store.observe(
            candidate,
            positive=bool(trace.success and trace.score >= 0.95),
            evidence={
                "source": "task_observation",
                "project_id": trace.metadata.get("project_id", ""),
                "chat_id": trace.chat_id,
                "run_id": trace.run_id,
                "score": trace.score,
                "success": trace.success,
                "error_count": len(trace.errors),
                "tokens": trace.prompt_tokens + trace.completion_tokens,
                "created_at": trace.ended_at or trace.started_at,
            },
        )

    @staticmethod
    def _candidate_from_trace(trace: EvolutionTrace) -> Optional[Experience]:
        tool_names = [item.name for item in trace.tool_calls]
        task_pattern = _task_pattern(trace.objective)
        context_label = task_pattern or "general"
        if trace.success and tool_names:
            signature = " -> ".join(tool_names)
            situation = f"successful task pattern [{context_label}] using {signature}"
            lesson = (
                f"For tasks matching [{context_label}], the tool sequence {signature} "
                "produced a successful result."
            )
            failure_pattern = ""
            key = f"success|{task_pattern}|{signature}"
        elif trace.errors:
            failed_tool = next((item.name for item in reversed(trace.tool_calls) if not item.success), "agent")
            situation = f"failure for task pattern [{context_label}] while using {failed_tool}"
            failure_pattern = _normalize_error(trace.errors[-1])
            lesson = (
                f"When {failed_tool} fails with this pattern, inspect the error and change "
                "the approach instead of repeating identical actions."
            )
            key = f"failure|{task_pattern}|{failed_tool}|{failure_pattern}"
        else:
            return None

        fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        return Experience(
            experience_id=fingerprint,
            fingerprint=fingerprint,
            situation=situation,
            lesson=lesson,
            task_pattern=task_pattern,
            scope=sorted(set(tool_names)),
            failure_pattern=failure_pattern,
        )


def _values(experience: Experience) -> tuple:
    return (
        experience.experience_id, experience.fingerprint, experience.situation,
        experience.lesson, json.dumps(experience.scope), experience.failure_pattern,
        experience.status, experience.positive_evidence, experience.negative_evidence,
        experience.observations, experience.confidence, experience.created_at,
        experience.updated_at, experience.last_used_at, experience.usage_count,
        experience.task_pattern,
    )


def _words(text: str) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]{2,}", text.lower()))


def _normalize_error(error: str) -> str:
    text = re.sub(r"\d+", "#", error.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return text[:160]


_TASK_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "please", "task",
    "file", "files", "using", "into", "then", "一个", "这个", "那个", "请帮",
    "帮我", "进行", "需要", "任务", "文件", "一下",
}


def _task_pattern(objective: str, max_terms: int = 8) -> str:
    """Build a stable, privacy-conscious task-family signature without an LLM call."""
    normalized = re.sub(r"(?:[a-zA-Z]:)?[/\\][^\s]+", " ", objective.lower())
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "#", normalized)
    terms = re.findall(r"[a-z][a-z0-9_.-]{1,}", normalized)
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        terms.extend(sequence[index:index + 2] for index in range(len(sequence) - 1))
    filtered = sorted({term for term in terms if term not in _TASK_STOPWORDS})
    return " ".join(filtered[:max_terms])
