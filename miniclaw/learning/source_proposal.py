"""Detect repeated failures and persist privacy-safe source improvement proposals."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .trace import EvolutionTrace


@dataclass
class SourceProposal:
    title: str
    problem_signature: str
    error_category: str
    evidence_run_ids: List[str]
    source_projects: List[str]
    suspected_files: List[str]
    sanitized_examples: List[str]
    hypothesis: str
    expected_benefit: str
    risk: str
    rollback_plan: str
    proposal_id: str = ""
    status: str = "open"
    experiment_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.proposal_id:
            self.proposal_id = hashlib.sha256(
                self.problem_signature.encode("utf-8")
            ).hexdigest()[:24]

    def to_dict(self) -> Dict:
        return asdict(self)


class SourceProposalStore:
    STATUSES = frozenset({"open", "approved", "experimenting", "ready", "promoted", "rejected", "failed", "rolled_back"})

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    problem_signature TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_proposal_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

    def upsert(self, proposal: SourceProposal) -> SourceProposal:
        existing = self.get(proposal.proposal_id)
        if existing is not None:
            existing.evidence_run_ids = sorted(set(existing.evidence_run_ids) | set(proposal.evidence_run_ids))
            existing.source_projects = sorted(set(existing.source_projects) | set(proposal.source_projects))
            existing.suspected_files = sorted(set(existing.suspected_files) | set(proposal.suspected_files))
            existing.sanitized_examples = list(dict.fromkeys(
                [*existing.sanitized_examples, *proposal.sanitized_examples]
            ))[:5]
            existing.updated_at = time.time()
            proposal = existing
            action = "evidence_updated"
        else:
            action = "created"
        payload = proposal.to_dict()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO source_proposals (
                    proposal_id, problem_signature, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    status=excluded.status, payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    proposal.proposal_id, proposal.problem_signature, proposal.status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    proposal.created_at, proposal.updated_at,
                ),
            )
            self._event(connection, proposal.proposal_id, action, {
                "evidence_count": len(proposal.evidence_run_ids),
                "project_count": len(proposal.source_projects),
            })
        return proposal

    def update_status(
        self,
        proposal_id: str,
        status: str,
        *,
        experiment_id: str = "",
        detail: Optional[Dict] = None,
    ) -> Optional[SourceProposal]:
        if status not in self.STATUSES:
            raise ValueError(f"unsupported source proposal status: {status}")
        proposal = self.get(proposal_id)
        if proposal is None:
            return None
        proposal.status = status
        if experiment_id:
            proposal.experiment_id = experiment_id
        proposal.updated_at = time.time()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE source_proposals SET status=?, payload_json=?, updated_at=? WHERE proposal_id=?",
                (
                    status,
                    json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True),
                    proposal.updated_at,
                    proposal_id,
                ),
            )
            self._event(connection, proposal_id, status, detail or {})
        return proposal

    def get(self, proposal_id: str) -> Optional[SourceProposal]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM source_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        return SourceProposal(**json.loads(row["payload_json"])) if row else None

    def list(self, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        query = "SELECT payload_json FROM source_proposals"
        params: List[object] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(0, limit))
        with closing(self._connect()) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def events(self, proposal_id: str, limit: int = 100) -> List[Dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT action, detail_json, created_at FROM source_proposal_events
                WHERE proposal_id=? ORDER BY event_id DESC LIMIT ?
                """,
                (proposal_id, max(0, limit)),
            ).fetchall()
        return [
            {
                "action": row["action"],
                "detail": json.loads(row["detail_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        proposal_id: str,
        action: str,
        detail: Dict,
    ) -> None:
        connection.execute(
            "INSERT INTO source_proposal_events (proposal_id, action, detail_json, created_at) VALUES (?, ?, ?, ?)",
            (proposal_id, action, json.dumps(detail, ensure_ascii=False, sort_keys=True), time.time()),
        )


class RepeatedFailureDetector:
    def __init__(
        self,
        store: SourceProposalStore,
        repo_root: Path | str,
        *,
        min_occurrences: int = 3,
        min_projects: int = 2,
    ):
        self.store = store
        self.repo_root = Path(repo_root).resolve()
        self.min_occurrences = max(2, min_occurrences)
        self.min_projects = max(1, min_projects)

    def scan(self, traces: Iterable[EvolutionTrace]) -> List[SourceProposal]:
        groups: Dict[str, Dict] = {}
        for trace in traces:
            if not trace.errors:
                continue
            error = trace.errors[-1]
            sanitized = _sanitize_error(error)
            category = _error_category(sanitized)
            normalized = _normalize_error(sanitized)
            signature = f"{category}|{normalized}"
            group = groups.setdefault(signature, {
                "runs": set(), "projects": set(), "files": set(), "examples": [],
                "category": category,
            })
            group["runs"].add(trace.run_id)
            project_id = str(trace.metadata.get("project_id", "") or trace.chat_id)
            if project_id:
                group["projects"].add(project_id)
            group["files"].update(_suspected_files(error, self.repo_root))
            if sanitized not in group["examples"]:
                group["examples"].append(sanitized[:300])

        proposals = []
        for signature, group in groups.items():
            if len(group["runs"]) < self.min_occurrences:
                continue
            if len(group["projects"]) < self.min_projects:
                continue
            category = group["category"]
            proposal = SourceProposal(
                title=f"Investigate repeated {category} failures",
                problem_signature=signature,
                error_category=category,
                evidence_run_ids=sorted(group["runs"]),
                source_projects=sorted(group["projects"]),
                suspected_files=sorted(group["files"]),
                sanitized_examples=group["examples"][:5],
                hypothesis=(
                    f"A repeatable defect in the suspected {category} path causes matching tasks to fail."
                ),
                expected_benefit="Reduce repeated task failures without changing unrelated behavior.",
                risk="A broad fix could regress other task families; require a reproduction test and full regression.",
                rollback_plan="Revert the candidate merge commit and retain the reproduction test evidence.",
            )
            proposals.append(self.store.upsert(proposal))
        return proposals


def _sanitize_error(text: str) -> str:
    value = re.sub(
        r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        str(text),
    )
    value = re.sub(r"(?i)bearer\s+[a-z0-9._-]+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?:[a-zA-Z]:)?[/\\](?:[^\s:\"']+[/\\])*[^\s:\"']+", "<path>", value)
    return re.sub(r"\s+", " ", value).strip()[:500]


def _normalize_error(text: str) -> str:
    value = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", text.lower())
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "#", value)
    return value[:240]


def _error_category(text: str) -> str:
    lowered = text.lower()
    for token, category in (
        ("timeout", "timeout"), ("permission", "permission"),
        ("not found", "missing-resource"), ("no such file", "missing-resource"),
        ("syntax", "syntax"), ("typeerror", "type-error"),
        ("valueerror", "value-error"), ("keyerror", "key-error"),
        ("exit code", "nonzero-exit"),
    ):
        if token in lowered:
            return category
    return "runtime-error"


def _suspected_files(error: str, repo_root: Path) -> List[str]:
    files = []
    for raw in re.findall(r"File\s+[\"']([^\"']+)[\"']", error):
        try:
            path = Path(raw).resolve()
            relative = path.relative_to(repo_root)
        except (OSError, ValueError):
            continue
        if relative.suffix == ".py" and ".." not in relative.parts:
            files.append(relative.as_posix())
    return files
