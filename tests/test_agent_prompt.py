from pathlib import Path

import miniclaw.agent as agent_module
from miniclaw.agent import Agent
from miniclaw.memory.session import SessionManager
from miniclaw.skills.loader import SkillLoader


class _FakeLLM:
    async def chat(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("not used")


def test_system_prompt_uses_compact_skill_summary(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    skill_dir = workspace / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
<name>demo</name>
<description>
This is a multiline description
for testing the compact prompt summary.
</description>
---
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(agent_module, "WORKSPACE_DIR", workspace)

    agent = Agent(_FakeLLM(), SessionManager(tmp_path / "sessions"))
    agent._skill_loader = SkillLoader(workspace / "skills")

    prompt = agent._build_system_prompt()

    assert "Function schemas are provided separately." in prompt
    assert "compact prompt summary" in prompt
    assert "read_skill name='demo'" in prompt
    assert "Skill files are named SKILL.md" in prompt
    assert "<available_skills>" not in prompt
