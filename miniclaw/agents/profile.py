"""Immutable configuration describing one Agent role."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple


def _names(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        value = value.get("allow", ())
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("skills/tools must be a list or an {allow: [...]} mapping")
    names = tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return names


@dataclass(frozen=True)
class AgentRuntimeOptions:
    """Small, optional overrides; all omitted values inherit global config."""

    max_tool_rounds: Optional[int] = None
    timeout_sec: Optional[float] = None
    model: Optional[str] = None
    thinking: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Any) -> "AgentRuntimeOptions":
        data = raw if isinstance(raw, Mapping) else {}
        rounds = data.get("max_tool_rounds")
        timeout = data.get("timeout_sec")
        return cls(
            max_tool_rounds=max(1, int(rounds)) if rounds is not None else None,
            timeout_sec=max(1.0, float(timeout)) if timeout is not None else None,
            model=str(data["model"]).strip() if data.get("model") else None,
            thinking=str(data["thinking"]).strip() if data.get("thinking") else None,
        )


@dataclass(frozen=True)
class AgentProfile:
    """Role-specific overlay applied to the shared Agent runtime."""

    name: str
    description: str
    system_prompt: str = ""
    skills: Tuple[str, ...] = field(default_factory=tuple)
    tools: Tuple[str, ...] = field(default_factory=tuple)
    runtime: AgentRuntimeOptions = field(default_factory=AgentRuntimeOptions)
    source: Optional[Path] = None

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        source: Optional[Path] = None,
    ) -> "AgentProfile":
        name = str(raw.get("name", "")).strip()
        description = str(raw.get("description", "")).strip()
        if not name:
            raise ValueError("agent profile requires a non-empty name")
        if not description:
            raise ValueError(f"agent profile '{name}' requires a description")

        prompt_value = raw.get("prompt", "")
        prompt = ""
        if prompt_value and source is not None:
            prompt_path = (source.parent / str(prompt_value)).resolve()
            if not prompt_path.is_file():
                raise ValueError(f"agent profile '{name}' prompt not found: {prompt_path}")
            prompt = prompt_path.read_text(encoding="utf-8").strip()
        elif prompt_value:
            prompt = str(prompt_value).strip()

        return cls(
            name=name,
            description=description,
            system_prompt=prompt,
            skills=_names(raw.get("skills")),
            tools=_names(raw.get("tools")),
            runtime=AgentRuntimeOptions.from_mapping(raw.get("runtime")),
            source=source,
        )

    def allows_tool(self, name: str) -> bool:
        return "*" in self.tools or name in self.tools

    def allows_skill(self, name: str) -> bool:
        return "*" in self.skills or name in self.skills

    @property
    def cache_key(self) -> tuple:
        source_stamp = None
        if self.source and self.source.exists():
            stat = self.source.stat()
            source_stamp = (stat.st_mtime_ns, stat.st_size)
        return (
            self.name,
            self.system_prompt,
            self.skills,
            self.tools,
            self.runtime,
            source_stamp,
        )
