from pathlib import Path

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def _trace(command):
    trace = EvolutionTrace(chat_id="skill-merge", objective="validate simulation workflow")
    trace.add_tool("read", {"path": "model.in"}, "ok")
    trace.add_tool("execute", {"command": command}, "completed")
    trace.finish(success=True, final_response="validated")
    return trace


def test_two_verified_structured_experiences_create_one_automatic_draft(tmp_path):
    service = EvolutionService(tmp_path)
    for command in ("python validate.py", "lmp -in model.in"):
        for _ in range(3):
            experience = service.complete_trace(_trace(command))
        assert experience is not None and experience.status == "verified"

    drafts = service.list_skill_drafts()

    assert len(drafts) == 1
    draft = drafts[0]
    text = Path(draft["path"]).read_text(encoding="utf-8")
    assert len(draft["experience_ids"]) == 2
    assert "## Conditions" in text
    assert "## Workflow" in text
    assert "## Validation" in text
    assert "## Failure recovery" in text
    assert "## Safety" in text
    assert "command-family:python" in text
    assert "command-family:lmp" in text


def test_skill_synthesis_is_idempotent_for_same_evidence_set(tmp_path):
    service = EvolutionService(tmp_path)
    for command in ("python validate.py", "lmp -in model.in"):
        for _ in range(3):
            experience = service.complete_trace(_trace(command))
    first = service.synthesize_skill(experience.task_pattern)
    second = service.synthesize_skill(experience.task_pattern)

    assert first["draft_id"] == second["draft_id"]
    assert len(service.list_skill_drafts()) == 1


def test_structured_skill_passes_isolated_validation_before_approval(tmp_path):
    service = EvolutionService(tmp_path)
    for command in ("python validate.py", "lmp -in model.in"):
        for _ in range(3):
            experience = service.complete_trace(_trace(command))
    draft = service.list_skill_drafts()[0]

    tested = service.test_skill_draft(draft["draft_id"])
    approved = service.review_skill_draft(
        draft["draft_id"], approve=True, confirmation=draft["name"]
    )

    assert tested["passed"] is True
    installed = Path(approved["path"])
    assert (installed / "SKILL.md").is_file()
    assert not (installed / "README.md").exists()
