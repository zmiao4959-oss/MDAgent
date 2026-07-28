import json
from pathlib import Path
from types import SimpleNamespace

import miniclaw.skills.loader as loader_module
import miniclaw.tools.lammps_script_tool as tool_module
import miniclaw.tools.paths as paths_module
from miniclaw.config import workspace_scope
from miniclaw.integrations.mdsynth_runner import _direct_script_safety_issues
from miniclaw.skills.loader import SkillLoader
from miniclaw.tools import ensure_tools_loaded, tool_registry


def test_lammps_script_tool_is_high_risk_and_registered():
    ensure_tools_loaded()
    tool = tool_registry.get("generate_lammps_script")
    assert tool is not None
    assert tool.require_approval is True
    assert tool.risk_level == "high"
    assert {"lammps", "compiler"} <= set(tool.tags)


def test_lammps_output_must_stay_in_active_project(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    with workspace_scope(workspace):
        resolved, error = tool_module._resolve_output_directory("generated/cu", False)
        assert error is None
        assert resolved == (workspace / "generated" / "cu").resolve()

        resolved, error = tool_module._resolve_output_directory(str(tmp_path / "outside"), False)
        assert resolved is None
        assert "workspace" in error

        resolved, error = tool_module._resolve_output_directory(".miniclaw/private", False)
        assert resolved is None
        assert "internal project state" in error


def test_advanced_mdsynth_modes_require_exact_confirmation():
    result = tool_module.generate_lammps_script_tool(
        request="铜在300K下NPT平衡",
        use_current_llm=False,
        preflight=True,
        confirmation="yes",
    )
    assert "ALLOW MDSYNTH ADVANCED" in result


def test_lammps_bridge_passes_a_bounded_payload_to_subprocess(tmp_path, monkeypatch):
    source = tmp_path / "mdsynth-source"
    (source / "mdsynth").mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\nname='mdsynth'\n", encoding="utf-8")
    (source / "mdsynth" / "pipeline.py").write_text("", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured = {}

    monkeypatch.setattr(tool_module, "_resolve_mdsynth_source", lambda: (source, None))
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-cross-process")

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["environment"] = kwargs["env"]
        request_path = Path(arguments[arguments.index("--request-file") + 1])
        result_path = Path(arguments[arguments.index("--result-file") + 1])
        captured["payload"] = json.loads(request_path.read_text(encoding="utf-8"))
        result_path.write_text(
            json.dumps({
                "ok": True,
                "success": True,
                "output_directory": captured["payload"]["output_directory"],
                "script": "in.main.lammps",
                "files": ["in.main.lammps"],
                "source": {"commit": "abc", "dirty": False},
            }),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tool_module.subprocess, "run", fake_run)
    with workspace_scope(workspace):
        response = json.loads(tool_module.generate_lammps_script_tool(
            request="铜在300K下NVT平衡",
            output_directory="generated/cu-nvt",
            use_current_llm=False,
        ))

    assert response["ok"] is True
    assert captured["payload"]["request"] == "铜在300K下NVT平衡"
    assert Path(captured["payload"]["output_directory"]).is_relative_to(workspace)
    assert captured["payload"]["rag_api_url"] == "http://localhost:3000"
    assert captured["payload"]["rag_required"] is True
    assert "UNRELATED_SECRET" not in captured["environment"]
    assert "MDSYNTH_BRIDGE_API_KEY" not in captured["environment"]


def test_direct_generation_rejects_host_commands_and_escaping_output_paths():
    assert _direct_script_safety_issues("shell rm -rf data") == [
        "line 1: forbidden LAMMPS command 'shell'"
    ]
    assert _direct_script_safety_issues("include other.in") == [
        "line 1: forbidden LAMMPS command 'include'"
    ]
    issues = _direct_script_safety_issues("write_data ../outside.data")
    assert issues == ["line 1: output path must stay in the preflight sandbox"]
    assert _direct_script_safety_issues("dump d all custom 100 dump.lammpstrj id type x y z") == []


def test_builtin_lammps_skill_is_discovered(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(loader_module, "WORKSPACE_DIR", workspace)
    monkeypatch.setattr(paths_module, "WORKSPACE_DIR", workspace)

    skill = SkillLoader().get("lammps-script-gen")
    assert skill is not None
    assert "LAMMPS" in skill.description
    assert "generate_lammps_script" in skill.location.read_text(encoding="utf-8")
