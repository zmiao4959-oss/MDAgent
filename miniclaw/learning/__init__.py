"""Controlled self-evolution primitives for MiniClaw."""

from .trace import EvolutionTrace, ToolTrace, TraceStore
from .experience import Experience, ExperienceEngine, ExperienceStore
from .service import EvolutionService
from .evaluation import EvaluationReport, ExperienceEvaluator
from .reflection import ReflectionProposal, ReflectionValidationError, StructuredReflector
from .replay import IsolatedReplayRunner, ReplayCase, ReplayReport
from .maintenance import MaintenanceManager, MaintenanceReport
from .governance import GovernanceManager
from .skill_evolution import SkillDraft, SkillSynthesizer
from .strategy import SemanticStep, StructuredStrategy, build_strategy, render_strategy
from .executable_policy import ExecutablePolicyDraft, ExecutablePolicyManager
from .source_proposal import RepeatedFailureDetector, SourceProposal, SourceProposalStore
from .source_experiment import (
    PatchActionResult, SingleCandidateExperimentRunner, SourceExperiment,
    SourceExperimentStore, SourcePatchPolicy,
)
from .source_promotion import SourceCandidateEvaluator, SourceCandidatePromoter

__all__ = [
    "EvolutionTrace", "ToolTrace", "TraceStore",
    "Experience", "ExperienceEngine", "ExperienceStore",
    "EvolutionService",
    "EvaluationReport", "ExperienceEvaluator",
    "ReflectionProposal", "ReflectionValidationError", "StructuredReflector",
    "IsolatedReplayRunner", "ReplayCase", "ReplayReport",
    "MaintenanceManager", "MaintenanceReport",
    "GovernanceManager",
    "SkillDraft", "SkillSynthesizer",
    "SemanticStep", "StructuredStrategy", "build_strategy", "render_strategy",
    "ExecutablePolicyDraft", "ExecutablePolicyManager",
    "RepeatedFailureDetector", "SourceProposal", "SourceProposalStore",
    "PatchActionResult", "SingleCandidateExperimentRunner", "SourceExperiment",
    "SourceExperimentStore", "SourcePatchPolicy",
    "SourceCandidateEvaluator", "SourceCandidatePromoter",
]
