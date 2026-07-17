from miniclaw.learning.evaluation import ExperienceEvaluator
from miniclaw.learning.experience import ExperienceEngine, ExperienceStore
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def _trace(*, tool_result: str = "ok", tokens: int = 100) -> EvolutionTrace:
    trace = EvolutionTrace(chat_id="c", objective="prepare a LAMMPS simulation")
    trace.add_tool("execute", {"command": "lammps"}, tool_result)
    trace.finish(
        success=True,
        final_response="done",
        prompt_tokens=tokens,
        completion_tokens=10,
    )
    return trace


def test_good_candidate_passes_gate_and_creates_policy_version(tmp_path):
    service = EvolutionService(tmp_path)

    for _ in range(3):
        experience = service.complete_trace(_trace())

    assert experience is not None and experience.status == "verified"
    evaluation = service.evaluations()[0]
    assert evaluation["passed"] is True
    assert evaluation["report"]["sample_count"] == 3
    version = service.policy_versions()[0]
    assert version["action"] == "promote"
    assert version["experience_id"] == experience.experience_id


def test_error_prone_candidate_fails_offline_gate(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.db")
    engine = ExperienceEngine(store)
    good = _trace()
    for _ in range(3):
        candidate = engine.observe_trace(good)
    assert candidate is not None and candidate.status == "pending_evaluation"

    poor_traces = [_trace(tool_result="Error: unstable result") for _ in range(3)]
    report = ExperienceEvaluator().evaluate(candidate, poor_traces)

    assert report.passed is False
    assert report.error_rate == 1.0
    assert any("error rate" in reason for reason in report.reasons)


def test_policy_rollback_disables_verified_experience(tmp_path):
    service = EvolutionService(tmp_path)
    for _ in range(3):
        experience = service.complete_trace(_trace())
    assert experience is not None

    rolled_back = service.rollback(experience.experience_id)

    assert rolled_back is not None and rolled_back.status == "rejected"
    assert service.relevant("LAMMPS simulation") == []
    assert service.policy_versions()[0]["action"] == "rollback"
