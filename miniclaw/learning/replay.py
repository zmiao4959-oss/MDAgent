"""Isolated baseline-versus-candidate replay evaluation framework."""
from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, List

from .experience import Experience
from .trace import EvolutionTrace


ReplayExecutor = Callable[[str, str, Path], Awaitable[EvolutionTrace]]


@dataclass
class ReplayCase:
    objective: str
    case_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    forbidden_tools: List[str] = field(
        default_factory=lambda: ["send_message", "browse", "web_search"]
    )


@dataclass
class ReplayReport:
    experience_id: str
    passed: bool
    case_count: int
    baseline_success_rate: float
    candidate_success_rate: float
    baseline_quality: float
    candidate_quality: float
    baseline_avg_tokens: float
    candidate_avg_tokens: float
    reasons: List[str]
    cases: List[Dict]
    replay_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)


class IsolatedReplayRunner:
    def __init__(self, *, timeout_sec: float = 120, max_cost_ratio: float = 1.5):
        self.timeout_sec = timeout_sec
        self.max_cost_ratio = max_cost_ratio

    async def run(
        self,
        experience: Experience,
        cases: List[ReplayCase],
        executor: ReplayExecutor,
    ) -> ReplayReport:
        if not cases:
            raise ValueError("at least one replay case is required")
        rows = []
        prefix = (
            "[Candidate Experience Under Evaluation]\n"
            f"- {experience.lesson}\n"
            "This is advisory and cannot override safety or user instructions.\n\n"
        )
        for case in cases:
            baseline = await self._execute(case, "", executor)
            candidate = await self._execute(case, prefix, executor)
            self._validate_tools(case, baseline)
            self._validate_tools(case, candidate)
            rows.append({
                "case_id": case.case_id,
                "objective": case.objective,
                "baseline": _trace_metrics(baseline),
                "candidate": _trace_metrics(candidate),
            })

        baseline = _aggregate([row["baseline"] for row in rows])
        candidate = _aggregate([row["candidate"] for row in rows])
        reasons = []
        if candidate["success_rate"] < baseline["success_rate"]:
            reasons.append("candidate success rate regresses against baseline")
        if candidate["quality"] < baseline["quality"]:
            reasons.append("candidate quality regresses against baseline")
        if (
            baseline["avg_tokens"] > 0
            and candidate["avg_tokens"] > baseline["avg_tokens"] * self.max_cost_ratio
        ):
            reasons.append("candidate token cost exceeds replay budget")
        return ReplayReport(
            experience_id=experience.experience_id,
            passed=not reasons,
            case_count=len(rows),
            baseline_success_rate=baseline["success_rate"],
            candidate_success_rate=candidate["success_rate"],
            baseline_quality=baseline["quality"],
            candidate_quality=candidate["quality"],
            baseline_avg_tokens=baseline["avg_tokens"],
            candidate_avg_tokens=candidate["avg_tokens"],
            reasons=reasons,
            cases=rows,
        )

    async def _execute(
        self,
        case: ReplayCase,
        prefix: str,
        executor: ReplayExecutor,
    ) -> EvolutionTrace:
        with tempfile.TemporaryDirectory(prefix="miniclaw-replay-") as directory:
            workspace = Path(directory).resolve()
            trace = await asyncio.wait_for(
                executor(case.objective, prefix, workspace),
                timeout=self.timeout_sec,
            )
            trace.metadata["replay_workspace"] = str(workspace)
            trace.metadata["replay_group"] = "candidate" if prefix else "baseline"
            return trace

    @staticmethod
    def _validate_tools(case: ReplayCase, trace: EvolutionTrace) -> None:
        used = {item.name for item in trace.tool_calls}
        forbidden = used.intersection(case.forbidden_tools)
        if forbidden:
            raise PermissionError(
                f"replay used forbidden tools: {', '.join(sorted(forbidden))}"
            )


def _trace_metrics(trace: EvolutionTrace) -> Dict:
    return {
        "success": bool(trace.success),
        "quality": trace.score,
        "tokens": trace.prompt_tokens + trace.completion_tokens,
        "errors": len(trace.errors),
    }


def _aggregate(rows: List[Dict]) -> Dict[str, float]:
    count = max(1, len(rows))
    return {
        "success_rate": round(sum(int(row["success"]) for row in rows) / count, 3),
        "quality": round(sum(row["quality"] for row in rows) / count, 3),
        "avg_tokens": round(sum(row["tokens"] for row in rows) / count, 1),
    }
