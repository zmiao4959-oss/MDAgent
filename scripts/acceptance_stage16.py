"""Stage 16 acceptance: stable structured experiences synthesize one safe Skill draft."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def trace(command):
    item = EvolutionTrace(chat_id="stage16", objective="validate simulation workflow")
    item.add_tool("read", {"path": "model.in"}, "ok")
    item.add_tool("execute", {"command": command}, "completed")
    item.finish(success=True, final_response="validated")
    return item


def main():
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        for command in ("python validate.py", "lmp -in model.in"):
            for _ in range(3):
                service.complete_trace(trace(command))
        drafts = service.list_skill_drafts()
        assert len(drafts) == 1
        draft = drafts[0]
        text = Path(draft["path"]).read_text(encoding="utf-8")
        assert all(section in text for section in (
            "## Conditions", "## Workflow", "## Validation",
            "## Failure recovery", "## Safety",
        ))
        assert service.test_skill_draft(draft["draft_id"])["passed"] is True
        installed = service.review_skill_draft(
            draft["draft_id"], approve=True, confirmation=draft["name"]
        )
        assert (Path(installed["path"]) / "SKILL.md").is_file()
    print("Stage 16 accepted: structured experiences synthesize and approve one valid Skill.")


if __name__ == "__main__":
    main()
