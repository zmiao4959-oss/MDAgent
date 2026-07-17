"""Workspace-scoped facade for tracing, learning, retrieval, and feedback."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .experience import Experience, ExperienceEngine, ExperienceStore
from .trace import EvolutionTrace, TraceStore


class EvolutionService:
    def __init__(self, workspace: Path | str):
        root = Path(workspace) / ".miniclaw" / "evolution"
        self.trace_store = TraceStore(root / "traces.jsonl")
        self.experience_store = ExperienceStore(root / "experiences.db")
        self.engine = ExperienceEngine(self.experience_store)

    def complete_trace(
        self,
        trace: EvolutionTrace,
        *,
        learn: bool = True,
        evaluate_applied: bool = True,
    ) -> Optional[Experience]:
        self.trace_store.append(trace)
        learned = self.engine.observe_trace(trace) if learn else None
        if evaluate_applied:
            applied_ids = list(trace.metadata.get("applied_experience_ids", []))
            if learned is not None:
                applied_ids = [item for item in applied_ids if item != learned.experience_id]
            self.experience_store.record_application_outcome(
                applied_ids,
                positive=bool(trace.success and trace.score >= 0.95),
            )
        return learned

    def relevant(self, query: str, limit: int = 3) -> List[Experience]:
        return self.experience_store.verified_for(query, limit=limit)

    def prompt_prefix(self, query: str, limit: int = 3) -> str:
        prefix, _ = self.prompt_context(query, limit)
        return prefix

    def prompt_context(self, query: str, limit: int = 3) -> tuple[str, List[str]]:
        experiences = self.relevant(query, limit)
        if not experiences:
            return "", []
        experience_ids = [item.experience_id for item in experiences]
        self.experience_store.mark_applied(experience_ids)
        lines = [
            "[Verified Agent Experience]",
            "These strategies are advisory and never override user instructions or safety rules.",
        ]
        lines.extend(f"- {item.lesson}" for item in experiences)
        return "\n".join(lines) + "\n\n", experience_ids

    def feedback(self, experience_id: str, positive: bool) -> Optional[Experience]:
        return self.experience_store.record_feedback(experience_id, positive=positive)

    def list_experiences(self, status: Optional[str] = None) -> List[Dict]:
        return [item.__dict__.copy() for item in self.experience_store.all(status)]
