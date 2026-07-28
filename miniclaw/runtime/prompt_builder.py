"""Profile-aware prompt and capability-schema construction."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Tuple, TYPE_CHECKING

from ..settings import active_workspace_dir, WORKSPACE_DIR, MEMORY_FILE
from ..skills.loader import SkillLoader
from ..tools.registry import ToolRegistry, tool_registry

if TYPE_CHECKING:
    from ..agents.profile import AgentProfile

_TOOL_DESC_LIMIT = 120
_SKILL_DESC_LIMIT = 160
_MAX_SKILLS_IN_PROMPT = 12


class PromptBuilder:
    """Build cached prompt layers and filtered Tool schemas for one profile."""

    def __init__(
        self,
        profile: Optional["AgentProfile"] = None,
        *,
        skill_loader: Optional[SkillLoader] = None,
        registry: ToolRegistry = tool_registry,
        workspace_dir=None,
    ):
        self.profile = profile
        self.skill_loader = skill_loader or SkillLoader()
        self.registry = registry
        self.workspace_dir = workspace_dir or WORKSPACE_DIR
        self._stable_cache: Optional[str] = None
        self._stable_cache_key: Optional[Tuple[Any, ...]] = None
        self._context_cache: Optional[str] = None
        self._context_cache_key: Optional[Tuple[Any, ...]] = None

    def visible_tool_names(self) -> List[str]:
        if self.profile is None:
            return self.registry.tool_names()
        return self.registry.tool_names(set(self.profile.tools))

    def visible_skills(self) -> List[Any]:
        skills = self.skill_loader.list_all()
        if self.profile is None:
            return skills
        return [skill for skill in skills if self.profile.allows_skill(skill.name)]

    def stable_fingerprint(self) -> Tuple[Any, ...]:
        parts: List[Any] = []
        for name in ("SOUL.md", "IDENTITY.md"):
            path = self.workspace_dir / name
            if path.exists():
                stat = path.stat()
                parts.append((name, stat.st_mtime_ns, stat.st_size))
        skills_dir = self.workspace_dir / "skills"
        if skills_dir.exists():
            skill_mtimes = tuple(
                sorted(
                    (p.name, p.stat().st_mtime_ns)
                    for p in skills_dir.iterdir()
                    if p.is_dir() and (p / "SKILL.md").exists()
                )
            )
            parts.append(("skills", skill_mtimes))
        parts.append(("tools", tuple(self.visible_tool_names())))
        if self.profile is not None:
            parts.append(("profile", self.profile.cache_key))
        return tuple(parts)

    def context_fingerprint(self) -> Tuple[Any, ...]:
        parts: List[Any] = []
        for name in ("AGENTS.md", "USER.md", MEMORY_FILE):
            path = self.workspace_dir / name
            if path.exists():
                stat = path.stat()
                parts.append((name, stat.st_mtime_ns, stat.st_size))
        return tuple(parts)

    def build_stable_prompt(self) -> str:
        self.skill_loader._refresh()
        parts: List[str] = []
        for filename in ("SOUL.md", "IDENTITY.md"):
            path = self.workspace_dir / filename
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
        if self.profile is not None and self.profile.system_prompt:
            parts.append(self.profile.system_prompt)
        parts.append(self.build_tool_summary())
        skills = self.visible_skills()
        if skills:
            parts.append(self.build_skill_summary(skills))
        return "\n\n".join(parts)

    def build_tool_summary(self) -> str:
        lines = [
            "## Tooling",
            "Function schemas are provided separately. Use those schemas for exact arguments and field names.",
        ]
        for tool_name in self.visible_tool_names():
            tool = self.registry.get(tool_name)
            if tool is None:
                continue
            desc = " ".join((tool.description or "").split())
            if len(desc) > _TOOL_DESC_LIMIT:
                desc = desc[: _TOOL_DESC_LIMIT - 3].rstrip() + "..."
            suffix = ""
            if tool.risk_level != "low":
                suffix += f" [{tool.risk_level} risk]"
            if tool.require_approval:
                suffix += " [requires approval]"
            lines.append(f"- `{tool_name}`: {desc or 'No description.'}{suffix}")
        return "\n".join(lines)

    @staticmethod
    def build_skill_summary(skills: List[Any]) -> str:
        lines = [
            "## Available Skills",
            (
                "If a skill clearly matches the task, call `read_skill` with the exact "
                "skill name before acting. Skill files are named SKILL.md; do not guess "
                "README.md or other filenames."
            ),
        ]
        ordered = sorted(skills, key=lambda skill: skill.name.lower())
        shown = ordered[:_MAX_SKILLS_IN_PROMPT]
        for skill in shown:
            desc = " ".join((skill.description or "").split())
            if len(desc) > _SKILL_DESC_LIMIT:
                desc = desc[: _SKILL_DESC_LIMIT - 3].rstrip() + "..."
            line = f"- `{skill.name}`"
            if desc:
                line += f": {desc}"
            lines.append(f"{line} (load: read_skill name={skill.name!r})")
        remaining = len(ordered) - len(shown)
        if remaining > 0:
            lines.append(f"- ... and {remaining} more skills available in the workspace.")
        return "\n".join(lines)

    def build_context_prompt(self) -> str:
        parts: List[str] = []
        for filename in ("AGENTS.md", "USER.md"):
            path = self.workspace_dir / filename
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
        memory = self.workspace_dir / MEMORY_FILE
        if memory.exists():
            text = memory.read_text(encoding="utf-8")
            if len(text) > 8000:
                text = text[:8000] + "\n\n... [记忆文件过长，已截断]"
            parts.append(f"## Long-term Memory\n{text}")
        return "\n\n".join(parts)

    def build_system_prompt(self) -> str:
        stable_key = self.stable_fingerprint()
        context_key = self.context_fingerprint()
        if self._stable_cache is None or self._stable_cache_key != stable_key:
            self._stable_cache = self.build_stable_prompt()
            self._stable_cache_key = stable_key
        if self._context_cache is None or self._context_cache_key != context_key:
            self._context_cache = self.build_context_prompt()
            self._context_cache_key = context_key
        parts = [self._stable_cache]
        if self._context_cache:
            parts.append(self._context_cache)
        parts.append(
            "## Runtime Info\n"
            f"- Current time: {datetime.now().isoformat()}\n"
            f"- Workspace: {active_workspace_dir()}"
        )
        return "\n\n".join(parts)

    def build_tool_definitions(self) -> List[dict]:
        allowed = None if self.profile is None else set(self.profile.tools)
        return self.registry.list_for_llm(allowed_names=allowed)
