"""
tools/skill_tool.py - read workspace skills by canonical skill name.
"""
from __future__ import annotations

from ..skills.loader import SkillLoader
from .registry import tool_registry


@tool_registry.register(
    name="read_skill",
    description="Read a skill's SKILL.md by exact skill name.",
    schema={
        "type": "function",
        "function": {
            "name": "read_skill",
            "description": (
                "Load the full SKILL.md for a discovered skill by exact name. "
                "Use this instead of guessing paths like README.md."
            ),
            "parameters": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact skill name, e.g. tensile-test or atomsk",
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="low",
    tags=["skills"],
)
def read_skill_tool(name: str, **kwargs):
    if not name or not str(name).strip():
        return "Error: skill name is required"

    loader = SkillLoader()
    skill = loader.get(str(name).strip())
    if skill is None:
        available = ", ".join(sorted(s.name for s in loader.list_all()))
        return f"Error: skill not found: {name}. Available skills: {available}"

    try:
        content = skill.location.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading skill {name}: {e}"

    return f"Skill: {skill.name}\nFile: {skill.location}\n\n{content}"
