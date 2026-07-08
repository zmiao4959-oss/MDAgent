from pathlib import Path

import miniclaw.skills.loader as loader_module
import miniclaw.tools.paths as paths_module
from miniclaw.skills.loader import SkillLoader


def test_skill_loader_parses_multiline_description(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
<name>demo</name>
<description>
first line
second line
</description>
---
""",
        encoding="utf-8",
    )

    loader = SkillLoader(tmp_path / "skills")

    skill = loader.get("demo")
    assert skill is not None
    assert skill.description == "first line second line"


def test_skill_loader_scans_default_skill_roots(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local_skills = workspace / "skills"
    local_skills.mkdir()
    legacy_skills = tmp_path / "skills"
    legacy_skills.mkdir()

    (local_skills / "local").mkdir()
    (local_skills / "local" / "SKILL.md").write_text(
        "<name>local</name><description>local skill</description>",
        encoding="utf-8",
    )
    (legacy_skills / "legacy").mkdir()
    (legacy_skills / "legacy" / "SKILL.md").write_text(
        "<name>legacy</name><description>legacy skill</description>",
        encoding="utf-8",
    )

    monkeypatch.setattr(loader_module, "WORKSPACE_DIR", workspace)
    monkeypatch.setattr(paths_module, "WORKSPACE_DIR", workspace)

    loader = SkillLoader()

    assert loader.get("local") is not None
    assert loader.get("legacy") is not None
