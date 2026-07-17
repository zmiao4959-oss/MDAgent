from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def _learning_trace() -> EvolutionTrace:
    trace = EvolutionTrace(chat_id="c", objective="prepare a LAMMPS simulation")
    trace.add_tool("execute", {"command": "lammps"}, "completed")
    trace.finish(success=True, final_response="done")
    return trace


def _outcome(applied_ids, success=True) -> EvolutionTrace:
    trace = EvolutionTrace(
        chat_id="c",
        objective="prepare a LAMMPS simulation",
        metadata={"applied_experience_ids": applied_ids},
    )
    trace.finish(
        success=success,
        final_response="done" if success else "",
        error="failed" if not success else "",
    )
    return trace


def _service(tmp_path, traffic=100):
    return EvolutionService(
        tmp_path,
        canary_enabled=True,
        canary_traffic_percent=traffic,
        canary_min_trials=3,
        canary_max_failures=2,
        canary_min_success_rate=0.66,
    )


def test_canary_promotes_after_successful_trials(tmp_path):
    service = _service(tmp_path)
    for _ in range(3):
        experience = service.complete_trace(_learning_trace())
    assert experience is not None and experience.status == "canary"

    for _ in range(3):
        _, applied = service.prompt_context("prepare LAMMPS simulation")
        assert applied == [experience.experience_id]
        service.complete_trace(_outcome(applied), learn=False)

    current = service.experience_store.all()[0]
    assert current.status == "verified"
    assert service.canary_stats(experience.experience_id)[0]["successes"] == 3
    assert service.policy_versions()[0]["action"] == "canary_promote"


def test_canary_rolls_back_after_failure_limit(tmp_path):
    service = _service(tmp_path)
    for _ in range(3):
        experience = service.complete_trace(_learning_trace())
    for _ in range(2):
        _, applied = service.prompt_context("prepare LAMMPS simulation")
        service.complete_trace(_outcome(applied, success=False), learn=False)

    current = service.experience_store.all()[0]
    assert current.status == "rejected"
    assert service.policy_versions()[0]["action"] == "canary_rollback"


def test_zero_percent_canary_never_enters_prompt(tmp_path):
    service = _service(tmp_path, traffic=0)
    for _ in range(3):
        experience = service.complete_trace(_learning_trace())
    assert experience is not None and experience.status == "canary"
    assert service.prompt_context("prepare LAMMPS simulation") == ("", [])
