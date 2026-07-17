"""Stage 17 acceptance: executable policies require isolation, approval, and safe bindings."""
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def trace(command):
    item = EvolutionTrace(chat_id="stage17", objective="validate simulation workflow")
    item.add_tool("read", {"path": "model.in"}, "ok")
    item.add_tool("execute", {"command": command}, "completed")
    item.finish(success=True, final_response="validated")
    return item


async def main_async():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        service = EvolutionService(root)
        for command in ("python validate.py", "lmp -in model.in"):
            for _ in range(3):
                service.complete_trace(trace(command))
        skill = service.list_skill_drafts()[0]
        skill_result = service.review_skill_draft(
            skill["draft_id"], approve=True, confirmation=skill["name"]
        )
        draft = skill_result["executable_policy_draft"]
        assert (await service.test_executable_policy(draft["policy_id"]))["passed"] is True
        approved = await service.review_executable_policy(
            draft["policy_id"], approve=True, confirmation=draft["name"]
        )
        policy = json.loads((Path(approved["path"]) / "policy.json").read_text(encoding="utf-8"))
        bindings = {}
        for step in policy["steps"]:
            if step["action"] == "read":
                bindings[step["step_id"]] = {"path": "model.in"}
            elif step["action"] == "execute":
                bindings[step["step_id"]] = {"command": f"{step['command_family']} --version"}

        async def executor(tool, arguments):
            return "ok"

        result = await service.run_executable_policy(
            draft["policy_id"], bindings, executor, workspace=root
        )
        assert result["passed"] is True
        first = next(step for step in policy["steps"] if step["action"] == "read")
        bindings[first["step_id"]] = {"path": "../escape"}
        blocked = await service.run_executable_policy(
            draft["policy_id"], bindings, executor, workspace=root
        )
        assert blocked["passed"] is False
    print("Stage 17 accepted: executable policies are isolated, approved, runnable, and bounded.")


if __name__ == "__main__":
    asyncio.run(main_async())
