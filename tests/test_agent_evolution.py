import miniclaw.agent as agent_module
from miniclaw.agent import Agent
from miniclaw.config import config
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace
from miniclaw.memory.session import SessionManager


class _FakeLLM:
    async def chat(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("not used")


def _promote_read_experience(service: EvolutionService) -> str:
    experience_id = ""
    for _ in range(3):
        trace = EvolutionTrace(chat_id="c", objective="read a project file")
        trace.add_tool("read", {"path": "README.md"}, "ok")
        trace.finish(success=True, final_response="done")
        experience = service.complete_trace(trace)
        assert experience is not None
        experience_id = experience.experience_id
    return experience_id


def test_agent_injects_only_verified_relevant_experience(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = EvolutionService(workspace)
    _promote_read_experience(service)
    monkeypatch.setattr(agent_module, "active_workspace_dir", lambda: workspace)
    monkeypatch.setattr(config.evolution, "enabled", True)
    monkeypatch.setattr(config.evolution, "inject_verified", True)

    agent = Agent(_FakeLLM(), SessionManager(tmp_path / "sessions"))
    prefix = agent._experience_prefix("please read the file")

    assert "[Verified Agent Experience]" in prefix
    assert "never override user instructions or safety rules" in prefix
    assert agent._experience_prefix("unrelated weather question") == ""


def test_feedback_can_reject_promoted_experience(tmp_path):
    service = EvolutionService(tmp_path)
    experience_id = _promote_read_experience(service)

    service.feedback(experience_id, False)
    result = service.feedback(experience_id, False)

    assert result is not None
    assert result.status == "rejected"
    assert service.relevant("read file") == []


def test_applied_experience_is_traced_and_auto_rolled_back(tmp_path):
    service = EvolutionService(tmp_path)
    experience_id = _promote_read_experience(service)

    for expected_usage in (1, 2):
        prefix, applied_ids = service.prompt_context("please read the file")
        assert prefix and applied_ids == [experience_id]
        failure = EvolutionTrace(
            chat_id="c",
            objective="read a file",
            metadata={"applied_experience_ids": applied_ids},
        )
        failure.finish(success=False, error="verification failed")
        service.complete_trace(failure, learn=False, evaluate_applied=True)
        stored = service.experience_store.all()[0]
        assert stored.usage_count == expected_usage

    assert stored.status == "rejected"
    assert stored.negative_evidence == 2


def test_agent_records_applied_experience_ids_in_trace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = EvolutionService(workspace)
    experience_id = _promote_read_experience(service)
    monkeypatch.setattr(agent_module, "active_workspace_dir", lambda: workspace)

    agent = Agent(_FakeLLM(), SessionManager(tmp_path / "sessions"))
    trace = EvolutionTrace(chat_id="c", objective="read the file")
    prefix = agent._experience_prefix("read the file", trace)

    assert prefix
    assert trace.metadata["applied_experience_ids"] == [experience_id]
