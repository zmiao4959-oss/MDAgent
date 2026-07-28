"""Acceptance: MiniClaw invokes MDSynth without importing it into the main process."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miniclaw.config import workspace_scope
from miniclaw.skills.loader import SkillLoader
from miniclaw.tools.lammps_script_tool import generate_lammps_script_tool


def main() -> None:
    with TemporaryDirectory() as directory:
        workspace = Path(directory) / "project"
        workspace.mkdir()
        with workspace_scope(workspace):
            result = json.loads(generate_lammps_script_tool(
                request="铜在300K下NPT平衡200ps",
                output_directory="generated/cu-npt-300k",
                use_current_llm=False,
            ))
        assert result["ok"] is True
        assert result["success"] is True
        assert result["preflight_requested"] is False
        assert result["source"]["commit"]
        output = workspace / "generated" / "cu-npt-300k"
        assert (output / "in.main.lammps").is_file()
        assert (output / "validation_report.json").is_file()
        assert (output / "provenance.lock").is_file()
        assert "units metal" in (output / "in.main.lammps").read_text(encoding="utf-8")
        assert SkillLoader().get("lammps-script-gen") is not None
    print("MDSynth integration accepted: isolated compiler Tool and bundled Skill work.")


if __name__ == "__main__":
    main()
