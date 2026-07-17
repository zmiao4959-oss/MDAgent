"""Privacy-conscious semantic workflows derived from completed Agent traces."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .trace import EvolutionTrace, ToolTrace


@dataclass
class SemanticStep:
    tool: str
    action: str
    intent: str
    resource_type: str = "generic-resource"
    command_family: str = ""
    constraints: List[str] = field(default_factory=list)
    success_signals: List[str] = field(default_factory=list)
    failure_signals: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def signature(self) -> str:
        return ":".join((self.action, self.resource_type, self.command_family or "none"))


@dataclass
class StructuredStrategy:
    task_pattern: str
    conditions: List[str]
    steps: List[SemanticStep]
    validation: List[str]
    fallback: List[str]
    safety: List[str]
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["semantic_signature"] = self.semantic_signature
        return payload

    @property
    def semantic_signature(self) -> str:
        return " -> ".join(step.signature for step in self.steps)


def build_strategy(trace: EvolutionTrace, task_pattern: str) -> StructuredStrategy:
    steps = [semantic_step(item) for item in trace.tool_calls]
    resource_types = sorted({step.resource_type for step in steps if step.resource_type != "generic-resource"})
    conditions = [f"task pattern matches [{task_pattern or 'general'}]"]
    if resource_types:
        conditions.append(f"required resource types are available: {', '.join(resource_types)}")
    validation = ["confirm every required step reports success"]
    if any(step.action in {"write", "transform"} for step in steps):
        validation.append("confirm the intended artifact exists and is readable")
    if any(step.action == "execute" for step in steps):
        validation.append("confirm execution completed without an error signal")
    return StructuredStrategy(
        task_pattern=task_pattern,
        conditions=conditions,
        steps=steps,
        validation=validation,
        fallback=[
            "stop at the first failed step and inspect its error category",
            "change the approach instead of repeating an identical failed action",
        ],
        safety=[
            "keep file operations inside the active workspace",
            "preserve user approval and tool permission requirements",
            "never reuse secrets, absolute paths, or raw project content from another task",
        ],
    )


def semantic_step(call: ToolTrace) -> SemanticStep:
    tool = _safe_token(call.name, "tool")
    action = _action_for(tool)
    resource_type = _resource_type(call.arguments)
    command_family = _command_family(call.arguments) if action == "execute" else ""
    target = command_family or resource_type
    intent = f"{action.capitalize()} the {target}."
    constraints = ["stay-within-workspace"]
    if action in {"read", "inspect", "search"}:
        constraints.append("read-only")
    if command_family:
        constraints.append(f"command-family:{command_family}")
    success_signals = ["tool-reported-success"] if call.success else []
    failure_signals = [] if call.success else [_error_category(call.error or call.result_preview)]
    return SemanticStep(
        tool=tool,
        action=action,
        intent=intent,
        resource_type=resource_type,
        command_family=command_family,
        constraints=constraints,
        success_signals=success_signals,
        failure_signals=failure_signals,
    )


def render_strategy(strategy: Dict[str, Any]) -> List[str]:
    """Render bounded structured guidance for prompt injection."""
    lines = ["Conditions:"]
    lines.extend(f"  - {item}" for item in strategy.get("conditions", [])[:4])
    lines.append("Workflow:")
    for index, step in enumerate(strategy.get("steps", [])[:12], 1):
        intent = str(step.get("intent", "Perform the validated step."))
        constraints = ", ".join(str(item) for item in step.get("constraints", [])[:3])
        lines.append(f"  {index}. {intent}" + (f" Constraints: {constraints}." if constraints else ""))
    lines.append("Validation:")
    lines.extend(f"  - {item}" for item in strategy.get("validation", [])[:4])
    lines.append("Fallback:")
    lines.extend(f"  - {item}" for item in strategy.get("fallback", [])[:3])
    return lines


def _action_for(tool: str) -> str:
    lowered = tool.lower()
    if any(token in lowered for token in ("read", "open", "view", "get_code")):
        return "read"
    if any(token in lowered for token in ("write", "patch", "edit", "create")):
        return "write"
    if any(token in lowered for token in ("execute", "shell", "command", "run")):
        return "execute"
    if any(token in lowered for token in ("search", "find", "query")):
        return "search"
    if any(token in lowered for token in ("inspect", "check", "validate", "test")):
        return "verify"
    return "invoke"


def _resource_type(arguments: Dict[str, Any]) -> str:
    for key, value in arguments.items():
        if not isinstance(value, str) or not any(token in key.lower() for token in ("path", "file", "input", "output")):
            continue
        suffix = Path(value.replace("\\", "/")).suffix.lower().lstrip(".")
        if suffix:
            return {
                "yaml": "yaml-config", "yml": "yaml-config", "json": "json-data",
                "py": "python-source", "md": "markdown-document", "csv": "tabular-data",
                "in": "lammps-input", "lmp": "lammps-data", "data": "simulation-data",
            }.get(suffix, f"{_safe_token(suffix, 'file')}-file")
    return "generic-resource"


def _command_family(arguments: Dict[str, Any]) -> str:
    for key in ("command", "cmd", "script"):
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        first = re.split(r"\s+", value.strip(), maxsplit=1)[0].strip('"\'')
        family = Path(first.replace("\\", "/")).name.lower()
        family = re.sub(r"\.(exe|cmd|bat|ps1|sh)$", "", family)
        aliases = {
            "python3": "python", "py": "python", "lmp_serial": "lammps",
            "lmp_mpi": "lammps", "mpiexec": "mpi", "mpirun": "mpi",
        }
        return _safe_token(aliases.get(family, family), "command")
    return "unspecified-command"


def _error_category(text: str) -> str:
    lowered = text.lower()
    for token, category in (
        ("timeout", "timeout"), ("permission", "permission-denied"),
        ("not found", "missing-resource"), ("syntax", "syntax-error"),
        ("exit code", "nonzero-exit"),
    ):
        if token in lowered:
            return category
    return "tool-error"


def _safe_token(value: str, fallback: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip().lower()).strip("-._")
    return token[:64] or fallback
