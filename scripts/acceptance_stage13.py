"""Stage 13 acceptance: verified experiences become safe, reviewable skill drafts."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def learn(service, objective, tool):
    for _ in range(3):
        trace = EvolutionTrace(chat_id="acceptance", objective=objective)
        trace.add_tool(tool, {}, "ok")
        trace.finish(success=True, final_response="done")
        experience = service.complete_trace(trace)
    return experience


def main():
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        first = learn(service, "read deployment manual", "read")
        learn(service, "read deployment manual", "execute")
        draft = service.synthesize_skill(first.task_pattern)
        assert Path(draft["path"]).is_file()
        assert service.test_skill_draft(draft["draft_id"])["passed"] is True

        try:
            service.review_skill_draft(
                draft["draft_id"], approve=True, confirmation="yes"
            )
            raise AssertionError("approval accepted an inexact confirmation")
        except PermissionError:
            pass

        approved = service.review_skill_draft(
            draft["draft_id"], approve=True, confirmation=draft["name"]
        )
        assert (Path(approved["path"]) / "SKILL.md").is_file()
    print("Stage 13 accepted: skill drafts are validated, tested, and explicitly approved.")


if __name__ == "__main__":
    main()
