"""
tools/skill_tool.py - read workspace skills by canonical skill name.
"""
from __future__ import annotations

from ..skills.loader import SkillLoader
from ..skills.project import mounted_from_tool_context
from .registry import tool_registry


@tool_registry.register(
    name="read_skill",
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
def read_skill_tool(name: str, _context=None, **kwargs):
    if not name or not str(name).strip():
        return "Error: skill name is required"

    allowed_skills = (_context or {}).get("allowed_skills")
    if allowed_skills is not None:
        allowed = set(allowed_skills)
        if "*" not in allowed and str(name).strip() not in allowed:
            return f"Error: Skill '{name}' is not allowed for this agent profile"

    loader = SkillLoader(mounted_skills=mounted_from_tool_context(_context or {}))
    skill = loader.get(str(name).strip())
    if skill is None:
        available = ", ".join(sorted(s.name for s in loader.list_all()))
        return f"Error: skill not found: {name}. Available skills: {available}"

    try:
        content = skill.location.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading skill {name}: {e}"

    project_note = (
        "\n[Project-mounted version: keep this package unchanged; use python -B for its Python scripts and put outputs in the project workspace.]\n"
        if skill.name in loader.mounted_skills else ""
    )
    return f"Skill: {skill.name}\nFile: {skill.location}\n{project_note}\n{content}"
