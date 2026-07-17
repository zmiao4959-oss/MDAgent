import asyncio
import json
from pathlib import Path

import pytest

from miniclaw.learning.service import EvolutionService
from miniclaw.learning.trace import EvolutionTrace


def _trace(command):
    trace = EvolutionTrace(chat_id="executable", objective="validate simulation workflow")
    trace.add_tool("read", {"path": "model.in"}, "ok")
    trace.add_tool("execute", {"command": command}, "completed")
    trace.finish(success=True, final_response="validated")
    return trace


def _prepare(service):
    for command in ("python validate.py", "lmp -in model.in"):
        for _ in range(3):
            experience = service.complete_trace(_trace(command))
    skill = service.list_skill_drafts()[0]
    approved_skill = service.review_skill_draft(
        skill["draft_id"], approve=True, confirmation=skill["name"]
    )
    return approved_skill["executable_policy_draft"]


def _bindings(policy):
    rows = {}
    for step in policy["steps"]:
        if step["action"] == "read":
            rows[step["step_id"]] = {"path": "model.in"}
        elif step["action"] == "write":
            rows[step["step_id"]] = {"path": "output.txt", "content": "ok"}
        elif step["action"] == "execute":
            rows[step["step_id"]] = {"command": f"{step['command_family']} --version"}
        else:
            rows[step["step_id"]] = {}
    return rows


def _run(coroutine):
    return asyncio.run(coroutine)


def test_skill_approval_generates_testable_executable_policy_draft(tmp_path):
    service = EvolutionService(tmp_path)
    draft = _prepare(service)

    assert Path(draft["path"]).is_file()
    assert Path(draft["path"]).with_name("runner.py").is_file()
    assert service.list_executable_policies()[0]["status"] == "draft"
    tested = _run(service.test_executable_policy(draft["policy_id"]))

    assert tested["passed"] is True
    assert [call["tool"] for call in tested["calls"]] == ["read", "execute", "execute"]


def test_unapproved_policy_cannot_run_and_approval_requires_exact_name(tmp_path):
    service = EvolutionService(tmp_path)
    draft = _prepare(service)

    async def executor(tool, arguments):
        return "ok"

    with pytest.raises(PermissionError):
        _run(service.run_executable_policy(
            draft["policy_id"], {}, executor, workspace=tmp_path
        ))
    with pytest.raises(PermissionError):
        _run(service.review_executable_policy(
            draft["policy_id"], approve=True, confirmation="yes"
        ))


def test_approved_policy_runs_in_order_with_validated_bindings(tmp_path):
    service = EvolutionService(tmp_path)
    draft = _prepare(service)
    approved = _run(service.review_executable_policy(
        draft["policy_id"], approve=True, confirmation=draft["name"]
    ))
    policy = json.loads(
        (Path(approved["path"]) / "policy.json").read_text(encoding="utf-8")
    )
    (tmp_path / "model.in").write_text("model", encoding="utf-8")
    calls = []

    async def executor(tool, arguments):
        calls.append((tool, arguments))
        return "ok"

    result = _run(service.run_executable_policy(
        draft["policy_id"], _bindings(policy), executor, workspace=tmp_path
    ))

    assert result["passed"] is True
    assert [tool for tool, _ in calls] == [step["tool"] for step in policy["steps"]]


def test_approved_policy_blocks_escape_network_and_command_family_changes(tmp_path):
    service = EvolutionService(tmp_path)
    draft = _prepare(service)
    approved = _run(service.review_executable_policy(
        draft["policy_id"], approve=True, confirmation=draft["name"]
    ))
    policy = json.loads(
        (Path(approved["path"]) / "policy.json").read_text(encoding="utf-8")
    )

    async def executor(tool, arguments):
        return "ok"

    bindings = _bindings(policy)
    read_step = next(step for step in policy["steps"] if step["action"] == "read")
    bindings[read_step["step_id"]] = {"path": "../outside.txt"}
    escaped = _run(service.run_executable_policy(
        draft["policy_id"], bindings, executor, workspace=tmp_path
    ))
    assert escaped["passed"] is False
    assert "escapes" in escaped["errors"][0]

    bindings = _bindings(policy)
    execute_step = next(step for step in policy["steps"] if step["action"] == "execute")
    bindings[execute_step["step_id"]] = {"command": "curl https://example.com"}
    network = _run(service.run_executable_policy(
        draft["policy_id"], bindings, executor, workspace=tmp_path
    ))
    assert network["passed"] is False
    assert "forbidden" in network["errors"][0]

    bindings[execute_step["step_id"]] = {"command": "other-tool --version"}
    changed = _run(service.run_executable_policy(
        draft["policy_id"], bindings, executor, workspace=tmp_path
    ))
    assert changed["passed"] is False
    assert "does not match" in changed["errors"][0]


def test_modified_runner_or_unknown_tool_fails_isolated_test(tmp_path):
    service = EvolutionService(tmp_path)
    draft = _prepare(service)
    runner = Path(draft["path"]).with_name("runner.py")
    runner.write_text("print('modified')\n", encoding="utf-8")

    tested = _run(service.test_executable_policy(draft["policy_id"]))

    assert tested["passed"] is False
    assert "runner template was modified" in tested["errors"]


def test_python_inline_code_and_unexpected_arguments_are_blocked(tmp_path):
    service = EvolutionService(tmp_path)
    draft = _prepare(service)
    approved = _run(service.review_executable_policy(
        draft["policy_id"], approve=True, confirmation=draft["name"]
    ))
    policy = json.loads(
        (Path(approved["path"]) / "policy.json").read_text(encoding="utf-8")
    )

    async def executor(tool, arguments):
        return "ok"

    bindings = _bindings(policy)
    python_step = next(
        step for step in policy["steps"] if step.get("command_family") == "python"
    )
    bindings[python_step["step_id"]] = {"command": "python -c print(1)"}
    inline = _run(service.run_executable_policy(
        draft["policy_id"], bindings, executor, workspace=tmp_path
    ))
    assert inline["passed"] is False
    assert "inline Python" in inline["errors"][0]

    bindings = _bindings(policy)
    read_step = next(step for step in policy["steps"] if step["action"] == "read")
    bindings[read_step["step_id"]]["cwd"] = ".."
    extra = _run(service.run_executable_policy(
        draft["policy_id"], bindings, executor, workspace=tmp_path
    ))
    assert extra["passed"] is False
    assert "unexpected arguments" in extra["errors"][0]
