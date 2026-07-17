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
from typing import Iterable, List, Optional

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

    def observe(self, candidate: Experience, *, positive: bool = False) -> Experience:
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
                return current

            if positive:
                candidate.positive_evidence = 1
            self._reclassify(candidate)
            self._insert(connection, candidate)
            return candidate

    def record_feedback(self, experience_id: str, *, positive: bool) -> Optional[Experience]:
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
            return experience

    def mark_applied(self, experience_ids: Iterable[str]) -> List[Experience]:
        """Record that verified experiences were actually placed in an Agent prompt."""
        applied: List[Experience] = []
        now = time.time()
        with self._lock, closing(self._connect()) as connection, connection:
            for experience_id in dict.fromkeys(experience_ids):
                row = connection.execute(
                    "SELECT * FROM experiences WHERE experience_id = ? AND status = 'verified'",
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
        self, experience_ids: Iterable[str], *, positive: bool
    ) -> List[Experience]:
        updated = []
        for experience_id in dict.fromkeys(experience_ids):
            experience = self.record_feedback(experience_id, positive=positive)
            if experience is not None:
                updated.append(experience)
        return updated

    def verified_for(self, query: str, limit: int = 5) -> List[Experience]:
        query_words = _words(query)
        now = time.time()
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM experiences WHERE status = 'verified' AND task_pattern != ''"
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
            experience.status = "verified"
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
        return self.store.observe(candidate, positive=bool(trace.success and trace.score >= 0.95))

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
