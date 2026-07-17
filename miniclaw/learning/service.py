"""Workspace-scoped facade for tracing, learning, retrieval, and feedback."""
from __future__ import annotations

from pathlib import Path
import hashlib
from typing import Dict, List, Optional

from .experience import Experience, ExperienceEngine, ExperienceStore
from .evaluation import ExperienceEvaluator
from .trace import EvolutionTrace, TraceStore
from .reflection import StructuredReflector
from .replay import IsolatedReplayRunner, ReplayCase, ReplayExecutor
from .maintenance import MaintenanceManager
from .governance import GovernanceManager
from .skill_evolution import SkillSynthesizer
from .strategy import render_strategy
from .executable_policy import ExecutablePolicyManager, PolicyExecutor


class EvolutionService:
    def __init__(
        self,
        workspace: Path | str,
        *,
        canary_enabled: Optional[bool] = None,
        canary_traffic_percent: Optional[int] = None,
        canary_min_trials: Optional[int] = None,
        canary_max_failures: Optional[int] = None,
        canary_min_success_rate: Optional[float] = None,
        shared_workspace: Optional[Path | str] = None,
    ):
        if canary_enabled is None:
            from ..config import config

            canary_enabled = config.evolution.canary_enabled
            canary_traffic_percent = config.evolution.canary_traffic_percent
            canary_min_trials = config.evolution.canary_min_trials
            canary_max_failures = config.evolution.canary_max_failures
            canary_min_success_rate = config.evolution.canary_min_success_rate
        self.canary_enabled = bool(canary_enabled)
        self.canary_traffic_percent = int(
            10 if canary_traffic_percent is None else canary_traffic_percent
        )
        self.canary_min_trials = int(5 if canary_min_trials is None else canary_min_trials)
        self.canary_max_failures = int(
            2 if canary_max_failures is None else canary_max_failures
        )
        self.canary_min_success_rate = float(
            0.8 if canary_min_success_rate is None else canary_min_success_rate
        )
        self.workspace = Path(workspace).resolve()
        if shared_workspace is None:
            from ..config import WORKSPACE_DIR

            configured_workspace = WORKSPACE_DIR.resolve()
            shared_workspace = (
                configured_workspace
                if self.workspace == configured_workspace
                or configured_workspace in self.workspace.parents
                else self.workspace
            )
        self.shared_workspace = Path(shared_workspace).resolve()
        self.root = self.workspace / ".miniclaw" / "evolution"
        self.shared_root = self.shared_workspace / ".miniclaw" / "evolution"
        self.trace_store = TraceStore(self.root / "traces.jsonl")
        self.experience_store = ExperienceStore(self.shared_root / "experiences.db")
        self._migrate_project_experiences()
        self.engine = ExperienceEngine(self.experience_store)
        self.evaluator = ExperienceEvaluator()

    def complete_trace(
        self,
        trace: EvolutionTrace,
        *,
        learn: bool = True,
        evaluate_applied: bool = True,
    ) -> Optional[Experience]:
        learned = self.engine.observe_trace(trace) if learn else None
        if learned is not None:
            trace.metadata["learned_experience_id"] = learned.experience_id
        self.trace_store.append(trace)
        if evaluate_applied:
            applied_ids = list(trace.metadata.get("applied_experience_ids", []))
            if learned is not None:
                applied_ids = [item for item in applied_ids if item != learned.experience_id]
            self.experience_store.record_application_outcome(
                applied_ids,
                positive=bool(trace.success and trace.score >= 0.95),
                canary_min_trials=self.canary_min_trials,
                canary_max_failures=self.canary_max_failures,
                canary_min_success_rate=self.canary_min_success_rate,
            )
        if learned is not None and learned.status == "pending_evaluation":
            learned = self._evaluate_if_ready(learned)
        if learned is not None and learned.status == "verified":
            self._synthesize_skill_if_ready(learned.task_pattern)
        return learned

    def relevant(self, query: str, limit: int = 3) -> List[Experience]:
        return self.experience_store.verified_for(query, limit=limit)

    def prompt_prefix(self, query: str, limit: int = 3) -> str:
        prefix, _ = self.prompt_context(query, limit)
        return prefix

    def prompt_context(self, query: str, limit: int = 3) -> tuple[str, List[str]]:
        experiences: List[Experience] = []
        if self.canary_enabled and limit > 0:
            candidates = self.experience_store.search_for(
                query, statuses=("canary",), limit=limit
            )
            for candidate in candidates:
                bucket = int.from_bytes(
                    hashlib.sha256(
                        f"{query}|{candidate.experience_id}".encode("utf-8")
                    ).digest()[:4],
                    "big",
                ) % 100
                if bucket < self.canary_traffic_percent:
                    experiences.append(candidate)
                    break
        experiences.extend(self.relevant(query, max(0, limit - len(experiences))))
        if not experiences:
            return "", []
        experience_ids = [item.experience_id for item in experiences]
        self.experience_store.mark_applied(experience_ids)
        lines = [
            "[Verified Agent Experience]",
            "These strategies are advisory and never override user instructions or safety rules.",
        ]
        for index, item in enumerate(experiences, 1):
            lines.append(f"Strategy {index}: {item.lesson}")
            if item.strategy:
                lines.extend(render_strategy(item.strategy))
        return "\n".join(lines) + "\n\n", experience_ids

    def feedback(
        self,
        experience_id: str,
        positive: bool,
        *,
        project_id: str = "",
        chat_id: str = "",
    ) -> Optional[Experience]:
        current = next(
            (
                item for item in self.experience_store.all()
                if item.experience_id == experience_id
            ),
            None,
        )
        if current is not None and current.status == "canary":
            return self.experience_store.record_canary_outcome(
                experience_id,
                positive=positive,
                min_trials=self.canary_min_trials,
                max_failures=self.canary_max_failures,
                min_success_rate=self.canary_min_success_rate,
            )
        experience = self.experience_store.record_feedback(
            experience_id,
            positive=positive,
            evidence={
                "source": "manual_feedback",
                "project_id": project_id,
                "chat_id": chat_id,
                "success": positive,
            },
        )
        if experience is not None and experience.status == "pending_evaluation":
            experience = self._evaluate_if_ready(experience)
        if experience is not None and experience.status == "verified":
            self._synthesize_skill_if_ready(experience.task_pattern)
        return experience

    def rollback(self, experience_id: str) -> Optional[Experience]:
        return self.experience_store.rollback(experience_id)

    def evaluations(self, limit: int = 100) -> List[Dict]:
        return self.experience_store.evaluations(limit)

    def policy_versions(self, limit: int = 100) -> List[Dict]:
        return self.experience_store.policy_versions(limit)

    def canary_stats(self, experience_id: Optional[str] = None) -> List[Dict]:
        return self.experience_store.canary_stats(experience_id)

    async def run_replay(
        self,
        experience_id: str,
        cases: List[ReplayCase],
        executor: ReplayExecutor,
        *,
        timeout_sec: float = 120,
        rollback_on_failure: bool = True,
    ) -> Optional[Dict]:
        experience = next(
            (
                item for item in self.experience_store.all()
                if item.experience_id == experience_id
            ),
            None,
        )
        if experience is None:
            return None
        report = await IsolatedReplayRunner(timeout_sec=timeout_sec).run(
            experience, cases, executor
        )
        payload = self.experience_store.save_replay_report(report.to_dict())
        if rollback_on_failure and not report.passed and experience.status in {
            "verified", "canary"
        }:
            self.rollback(experience_id)
        return payload

    def replay_reports(self, limit: int = 100) -> List[Dict]:
        return self.experience_store.replay_reports(limit)

    def maintain(
        self,
        *,
        candidate_ttl_days: int = 90,
        verified_review_days: int = 180,
        trace_retention_days: int = 90,
        now: Optional[float] = None,
    ) -> Dict:
        return MaintenanceManager(self.experience_store, self.trace_store).run(
            candidate_ttl_days=candidate_ttl_days,
            verified_review_days=verified_review_days,
            trace_retention_days=trace_retention_days,
            now=now,
        ).to_dict()

    def maintain_if_due(
        self,
        *,
        interval_hours: int = 24,
        candidate_ttl_days: int = 90,
        verified_review_days: int = 180,
        trace_retention_days: int = 90,
    ) -> Optional[Dict]:
        reports = self.maintenance_reports(limit=1)
        now = __import__("time").time()
        if reports and now - float(reports[0]["created_at"]) < interval_hours * 3600:
            return None
        return self.maintain(
            candidate_ttl_days=candidate_ttl_days,
            verified_review_days=verified_review_days,
            trace_retention_days=trace_retention_days,
            now=now,
        )

    def maintenance_reports(self, limit: int = 100) -> List[Dict]:
        return self.experience_store.maintenance_reports(limit)

    def export_data(self, *, redact: bool = True) -> Dict:
        return self._governance().export(redact=redact)

    def backup_data(self) -> Dict:
        path = self._governance().backup()
        return {"name": path.name, "path": str(path), "size": path.stat().st_size}

    def restore_data(self, backup_name: str) -> Dict:
        return self._governance().restore(backup_name)

    def purge_data(self, confirmation: str) -> Dict:
        return self._governance().purge(confirmation)

    def list_backups(self) -> List[Dict]:
        return self._governance().list_backups()

    def audit_events(self, limit: int = 100) -> List[Dict]:
        return self._governance().audit_events(limit)

    def synthesize_skill(self, task_pattern: str, min_experiences: int = 2) -> Dict:
        return self._skills().synthesize(
            self.experience_store.all(),
            task_pattern=task_pattern,
            min_experiences=min_experiences,
        ).to_dict()

    def _synthesize_skill_if_ready(self, task_pattern: str) -> Optional[Dict]:
        from ..config import config

        if not config.evolution.auto_skill_drafts_enabled:
            return None
        draft = self._skills().synthesize_if_ready(
            self.experience_store.all(),
            task_pattern=task_pattern,
            min_experiences=config.evolution.skill_min_experiences,
        )
        return draft.to_dict() if draft is not None else None

    def list_skill_drafts(self) -> List[Dict]:
        return self._skills().list_drafts()

    def test_skill_draft(self, draft_id: str) -> Dict:
        return self._skills().isolated_test(draft_id)

    def review_skill_draft(
        self,
        draft_id: str,
        *,
        approve: bool,
        confirmation: str = "",
    ) -> Dict:
        if approve:
            result = self._skills().approve(draft_id, confirmation)
            task_pattern = result["draft"]["task_pattern"]
            executable = self._executables().compile(
                self.experience_store.all(),
                task_pattern=task_pattern,
            )
            result["executable_policy_draft"] = executable.to_dict()
            return result
        return self._skills().reject(draft_id)

    def compile_executable_policy(
        self, task_pattern: str, min_experiences: int = 2
    ) -> Dict:
        return self._executables().compile(
            self.experience_store.all(),
            task_pattern=task_pattern,
            min_experiences=min_experiences,
        ).to_dict()

    def list_executable_policies(self, *, approved: bool = False) -> List[Dict]:
        manager = self._executables()
        return manager.list_approved() if approved else manager.list_drafts()

    async def test_executable_policy(self, policy_id: str) -> Dict:
        return await self._executables().isolated_test(policy_id)

    async def review_executable_policy(
        self,
        policy_id: str,
        *,
        approve: bool,
        confirmation: str = "",
    ) -> Dict:
        if approve:
            return await self._executables().approve(policy_id, confirmation)
        return self._executables().reject(policy_id)

    async def run_executable_policy(
        self,
        policy_id: str,
        bindings: Dict[str, Dict],
        executor: PolicyExecutor,
        *,
        workspace: Path | str,
        timeout_sec: float = 30,
    ) -> Dict:
        return await self._executables().run(
            policy_id,
            bindings,
            executor,
            workspace=workspace,
            timeout_sec=timeout_sec,
        )

    def _governance(self) -> GovernanceManager:
        return GovernanceManager(self.shared_root, self.experience_store, self.trace_store)

    def _skills(self) -> SkillSynthesizer:
        return SkillSynthesizer(self.shared_workspace)

    def _executables(self) -> ExecutablePolicyManager:
        return ExecutablePolicyManager(self.shared_workspace)

    async def generate_reflection(
        self,
        trace: EvolutionTrace,
        llm,
        *,
        model: str = "",
    ) -> Optional[Dict]:
        experience_id = str(trace.metadata.get("learned_experience_id", ""))
        if not experience_id:
            return None
        proposal = await StructuredReflector(llm).reflect(
            trace, experience_id, model=model
        )
        return self.experience_store.save_reflection(proposal.to_dict())

    def reflections(self, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        return self.experience_store.reflections(status, limit)

    def review_reflection(
        self, reflection_id: str, *, approve: bool
    ) -> Optional[Experience]:
        experience = self.experience_store.review_reflection(
            reflection_id, approve=approve
        )
        if approve and experience is not None and experience.status == "pending_evaluation":
            experience = self._evaluate_if_ready(experience)
        if approve and experience is not None and experience.status == "verified":
            self._synthesize_skill_if_ready(experience.task_pattern)
        return experience

    def list_experiences(self, status: Optional[str] = None) -> List[Dict]:
        rows = []
        for item in self.experience_store.all(status):
            payload = item.__dict__.copy()
            payload.update(self.experience_store.evidence_sources(item.experience_id))
            rows.append(payload)
        return rows

    def _evaluate_if_ready(self, experience: Experience) -> Experience:
        report = self.evaluator.evaluate(experience, self._evaluation_traces())
        payload = report.to_dict()
        if report.passed and self.canary_enabled:
            payload["target_status"] = "canary"
        return self.experience_store.apply_evaluation(payload) or experience

    def _evaluation_traces(self, limit: int = 5000) -> List[EvolutionTrace]:
        """Read project-local traces as a federated evaluation set without centralizing raw data."""
        paths = {self.trace_store.path.resolve()}
        paths.add((self.shared_root / "traces.jsonl").resolve())
        projects_root = self.shared_workspace / "projects"
        if projects_root.is_dir():
            paths.update(
                path.resolve()
                for path in projects_root.glob("*/.miniclaw/evolution/traces.jsonl")
                if path.is_file()
            )
        traces: Dict[str, EvolutionTrace] = {}
        for path in paths:
            for trace in TraceStore(path).recent(limit):
                traces[trace.run_id] = trace
        return sorted(traces.values(), key=lambda trace: trace.started_at)[-limit:]

    def _migrate_project_experiences(self) -> int:
        """Merge old per-project databases into the shared store exactly once per source."""
        sources = {self.root / "experiences.db"}
        projects_root = self.shared_workspace / "projects"
        if projects_root.is_dir():
            sources.update(projects_root.glob("*/.miniclaw/evolution/experiences.db"))
        return sum(self.experience_store.import_database(path) for path in sources)
