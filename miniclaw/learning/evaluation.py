"""Offline promotion gate for candidate Agent experiences."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .experience import Experience, ExperienceEngine
from .trace import EvolutionTrace


@dataclass
class EvaluationReport:
    experience_id: str
    passed: bool
    sample_count: int
    success_rate: float
    avg_quality_score: float
    error_rate: float
    avg_tokens: float
    baseline_sample_count: int = 0
    baseline_avg_quality_score: float = 0.0
    baseline_avg_tokens: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExperienceEvaluator:
    """Evaluates a strategy against matching historical and baseline traces."""

    def __init__(
        self,
        *,
        min_samples: int = 3,
        min_success_rate: float = 0.8,
        min_quality_score: float = 0.8,
        max_error_rate: float = 0.2,
        max_cost_ratio: float = 1.5,
        quality_regression_tolerance: float = 0.05,
    ):
        self.min_samples = min_samples
        self.min_success_rate = min_success_rate
        self.min_quality_score = min_quality_score
        self.max_error_rate = max_error_rate
        self.max_cost_ratio = max_cost_ratio
        self.quality_regression_tolerance = quality_regression_tolerance

    def evaluate(
        self,
        experience: Experience,
        traces: List[EvolutionTrace],
    ) -> EvaluationReport:
        matching: List[EvolutionTrace] = []
        baseline: List[EvolutionTrace] = []
        for trace in traces:
            candidate = ExperienceEngine._candidate_from_trace(trace)
            if candidate is None or candidate.task_pattern != experience.task_pattern:
                continue
            if candidate.experience_id == experience.experience_id:
                matching.append(trace)
            else:
                baseline.append(trace)

        metrics = _metrics(matching)
        baseline_metrics = _metrics(baseline)
        reasons = []
        if metrics["sample_count"] < self.min_samples:
            reasons.append(f"requires at least {self.min_samples} matching samples")
        if metrics["success_rate"] < self.min_success_rate:
            reasons.append("success rate is below the promotion threshold")
        if metrics["avg_quality_score"] < self.min_quality_score:
            reasons.append("average quality score is below the promotion threshold")
        if metrics["error_rate"] > self.max_error_rate:
            reasons.append("error rate exceeds the promotion threshold")
        if baseline_metrics["sample_count"]:
            if (
                metrics["avg_quality_score"] + self.quality_regression_tolerance
                < baseline_metrics["avg_quality_score"]
            ):
                reasons.append("quality regresses against the alternative-strategy baseline")
            if (
                baseline_metrics["avg_tokens"] > 0
                and metrics["avg_tokens"] > baseline_metrics["avg_tokens"] * self.max_cost_ratio
            ):
                reasons.append("token cost regresses against the alternative-strategy baseline")

        return EvaluationReport(
            experience_id=experience.experience_id,
            passed=not reasons,
            sample_count=metrics["sample_count"],
            success_rate=metrics["success_rate"],
            avg_quality_score=metrics["avg_quality_score"],
            error_rate=metrics["error_rate"],
            avg_tokens=metrics["avg_tokens"],
            baseline_sample_count=baseline_metrics["sample_count"],
            baseline_avg_quality_score=baseline_metrics["avg_quality_score"],
            baseline_avg_tokens=baseline_metrics["avg_tokens"],
            reasons=reasons,
        )


def _metrics(traces: List[EvolutionTrace]) -> Dict[str, float | int]:
    count = len(traces)
    if not count:
        return {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_quality_score": 0.0,
            "error_rate": 0.0,
            "avg_tokens": 0.0,
        }
    return {
        "sample_count": count,
        "success_rate": round(sum(1 for trace in traces if trace.success) / count, 3),
        "avg_quality_score": round(sum(trace.score for trace in traces) / count, 3),
        "error_rate": round(sum(1 for trace in traces if trace.errors) / count, 3),
        "avg_tokens": round(
            sum(trace.prompt_tokens + trace.completion_tokens for trace in traces) / count,
            1,
        ),
    }
