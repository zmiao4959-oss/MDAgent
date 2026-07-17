"""Stage 10 acceptance: isolated baseline/candidate replay and regression rollback."""
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miniclaw.learning.replay import ReplayCase
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def learning_trace():
    trace = EvolutionTrace(chat_id="acceptance", objective="prepare LAMMPS simulation")
    trace.add_tool("read", {}, "ok")
    trace.finish(success=True, final_response="done")
    return trace


async def main():
    with TemporaryDirectory() as directory:
        service = EvolutionService(directory)
        for _ in range(3):
            experience = service.complete_trace(learning_trace())
        workspaces = []

        async def executor(objective, prefix, workspace):
            assert workspace.is_dir()
            workspaces.append(workspace)
            trace = EvolutionTrace(chat_id="replay", objective=objective)
            trace.finish(success=True, final_response="done", prompt_tokens=70 if prefix else 100)
            trace.score = 1.0 if prefix else 0.9
            return trace

        report = await service.run_replay(
            experience.experience_id,
            [ReplayCase("prepare LAMMPS simulation")],
            executor,
        )
        assert report and report["passed"]
        assert len(workspaces) == 2 and all(not path.exists() for path in workspaces)
        assert service.replay_reports()
    print("Stage 10 accepted: isolated A/B replay, safety checks, reports, and rollback work.")


if __name__ == "__main__":
    asyncio.run(main())
