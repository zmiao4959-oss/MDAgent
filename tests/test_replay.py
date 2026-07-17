import asyncio

import pytest

from miniclaw.learning.replay import ReplayCase
from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def _learned_trace() -> EvolutionTrace:
    trace = EvolutionTrace(chat_id="c", objective="prepare a LAMMPS simulation")
    trace.add_tool("read", {}, "ok")
    trace.finish(success=True, final_response="done")
    return trace


def _promote(service):
    for _ in range(3):
        experience = service.complete_trace(_learned_trace())
    return experience


def test_isolated_replay_compares_baseline_and_candidate(tmp_path):
    service = EvolutionService(tmp_path)
    experience = _promote(service)
    workspaces = []

    async def executor(objective, prefix, workspace):
        assert workspace.is_dir()
        workspaces.append(workspace)
        trace = EvolutionTrace(chat_id="replay", objective=objective)
        trace.finish(
            success=True,
            final_response="done",
            prompt_tokens=70 if prefix else 100,
            completion_tokens=10,
        )
        trace.score = 1.0 if prefix else 0.9
        return trace

    report = asyncio.run(service.run_replay(
        experience.experience_id,
        [ReplayCase("prepare a LAMMPS simulation")],
        executor,
    ))

    assert report is not None and report["passed"] is True
    assert len(workspaces) == 2 and workspaces[0] != workspaces[1]
    assert all(not workspace.exists() for workspace in workspaces)
    assert service.replay_reports()[0]["replay_id"] == report["replay_id"]


def test_replay_regression_rolls_back_policy(tmp_path):
    service = EvolutionService(tmp_path)
    experience = _promote(service)

    async def executor(objective, prefix, workspace):
        trace = EvolutionTrace(chat_id="replay", objective=objective)
        trace.finish(
            success=not bool(prefix),
            final_response="done" if not prefix else "",
            error="candidate failed" if prefix else "",
        )
        return trace

    report = asyncio.run(service.run_replay(
        experience.experience_id,
        [ReplayCase("prepare a LAMMPS simulation")],
        executor,
    ))

    assert report is not None and report["passed"] is False
    assert service.experience_store.all()[0].status == "rejected"


def test_replay_blocks_communication_tools(tmp_path):
    service = EvolutionService(tmp_path)
    experience = _promote(service)

    async def executor(objective, prefix, workspace):
        trace = EvolutionTrace(chat_id="replay", objective=objective)
        trace.add_tool("send_message", {}, "sent")
        trace.finish(success=True, final_response="done")
        return trace

    with pytest.raises(PermissionError):
        asyncio.run(service.run_replay(
            experience.experience_id,
            [ReplayCase("prepare a LAMMPS simulation")],
            executor,
        ))
