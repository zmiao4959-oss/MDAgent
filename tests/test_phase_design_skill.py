"""Exercise the bundled Skill through existing MiniClaw loading and tool channels."""
import asyncio
import json
from pathlib import Path
import sys

import pytest

from miniclaw.agents import AgentCatalog
from miniclaw.runtime.prompt_builder import PromptBuilder
from miniclaw.settings import workspace_scope
from miniclaw.skills.loader import SkillLoader
from miniclaw.tools import exec_tool, file_tools, skill_tool  # register existing tools
from miniclaw.tools.registry import tool_registry


PROJECT = Path(__file__).resolve().parents[1]
PROFILES = PROJECT / "miniclaw/agents/profiles"
NAME = "phase-transition-design"


def researcher():
    return AgentCatalog(roots=[PROFILES]).require("researcher")


def context(profile):
    return {"allowed_tools": list(profile.tools), "allowed_skills": list(profile.skills)}


def call_tool(name, arguments, profile):
    return asyncio.run(tool_registry.execute(name, arguments, context(profile)))


def skill_directory(profile):
    result = call_tool("read_skill", {"name": NAME}, profile)
    assert result.startswith("Skill: " + NAME), result
    filename = next(line.removeprefix("File: ") for line in result.splitlines() if line.startswith("File: "))
    path = Path(filename)
    assert path.is_absolute() and path.is_file()
    return path.parent


def test_restored_researcher_and_skill_are_discoverable():
    profile = researcher()
    loader = SkillLoader()
    selected = loader.get(NAME)
    assert selected is not None
    assert set(profile.tools) == {"read", "read_skill", "execute", "web_search", "web_fetch"}
    assert set(profile.skills) == {NAME}
    assert {s.name for s in PromptBuilder(profile=profile, skill_loader=loader).visible_skills()} == {NAME}
    coordinator = AgentCatalog(roots=[PROFILES]).require("coordinator")
    assert "execute" not in coordinator.tools


def test_skill_references_are_readable_from_another_project(tmp_path):
    profile = researcher()
    with workspace_scope(tmp_path):
        directory = skill_directory(profile)
        result = call_tool("read", {"path": str(directory / "references/agent-entry.md"), "limit": 12}, profile)
    assert not result.startswith("Error:"), result
    assert "研究" in result


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ("state --duration-ps 100 --dt-fs 0.5 --tau-t-fs 100 --tau-p-fs 1000", {"status": "ok", "steps": 200000, "tau_t_over_dt": 200, "tau_p_over_dt": 2000}),
        ("tension --strain 0.3 --steps 100000 --dt-fs 1 --axes xy --length x=35 --length y=36", {"status": "ok", "duration_ps": 100, "increment_A_per_step": {"x": 0.000105, "y": 0.000108}}),
        ("tension --strain 0.1 --rate 1e8 --dt-fs 1 --axes z", {"status": "ok", "steps": 1000000, "reference_status": "RUNTIME", "increment_A_per_step": {}}),
        ("tension --strain 0.1 --rate 1e8 --dt-fs 1 --axes z --strain-definition true", {"status": "error"}),
        ("tension --strain 0.1 --rate 1e8 --steps 1000 --dt-fs 1 --axes z", {"status": "error"}),
    ],
)
def test_existing_execute_runs_skill_helper_without_input_files(tmp_path, arguments, expected):
    profile = researcher()
    with workspace_scope(tmp_path):
        directory = skill_directory(profile)
        command = f'"{sys.executable}" "{directory / "scripts/calculate_plan.py"}" {arguments}'
        result = call_tool("execute", {"command": command, "working_dir": str(tmp_path), "timeout": 10}, profile)
    assert "{" in result, result
    data, _ = json.JSONDecoder().raw_decode(result[result.index("{"):])
    for field, value in expected.items():
        assert data[field] == value
    assert not list(tmp_path.iterdir()), "The parameter helper must not create inputs or assets"


def test_researcher_does_not_gain_project_write_tools(tmp_path):
    profile = researcher()
    with workspace_scope(tmp_path):
        result = call_tool("write", {"path": "should-not-exist.txt", "content": "no"}, profile)
    assert "not allowed" in result
    assert not (tmp_path / "should-not-exist.txt").exists()


def test_researcher_cannot_load_unrelated_skills(tmp_path):
    with workspace_scope(tmp_path):
        result = call_tool("read_skill", {"name": "gpumd-script"}, researcher())
    assert "not allowed" in result


@pytest.mark.parametrize("name", ["browser_snapshot", "grep", "list", "search_gpumd_docs", "system_info"])
def test_researcher_removed_tools_are_not_callable(tmp_path, name):
    with workspace_scope(tmp_path):
        result = call_tool(name, {}, researcher())
    assert "not allowed" in result
