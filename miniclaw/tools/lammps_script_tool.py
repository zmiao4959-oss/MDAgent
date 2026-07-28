"""Safe MiniClaw bridge to the independently versioned MDSynth compiler."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from ..settings import active_workspace_dir, config
from .paths import resolve_workspace_path
from .registry import tool_registry


MDSYNTH_SOURCE_ENV = "MDSYNTH_PROJECT_DIR"
DEFAULT_MDSYNTH_SOURCE = (
    Path.home() / "Desktop" / "MDAgent" / "组会" / "周五" /
    "MD编译器开发" / "lammps-script-gen"
)
DEFAULT_OUTPUT_DIRECTORY = "generated/lammps-script"
MAX_REQUEST_CHARS = 12_000


def _environment_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _resolve_mdsynth_source() -> tuple[Path | None, str | None]:
    raw = os.environ.get(MDSYNTH_SOURCE_ENV, "").strip()
    source = Path(raw).expanduser() if raw else DEFAULT_MDSYNTH_SOURCE
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        return None, (
            f"MDSynth source not found: {source}. Set {MDSYNTH_SOURCE_ENV}. "
            f"Details: {exc}"
        )
    required = (resolved / "pyproject.toml", resolved / "mdsynth" / "pipeline.py")
    if not all(path.is_file() for path in required):
        return None, f"Invalid MDSynth source directory: {resolved}"
    return resolved, None


def _resolve_output_directory(path: str, overwrite: bool) -> tuple[Path | None, str | None]:
    resolved, error = resolve_workspace_path(path, allow_create=True)
    if error or resolved is None:
        return None, error
    workspace = active_workspace_dir().resolve()
    if resolved == workspace:
        return None, "Error: output_directory must be a subdirectory of the active workspace"
    try:
        relative = resolved.relative_to(workspace)
    except ValueError:
        return None, "Error: output_directory must stay inside the active project workspace"
    if any(part.lower() in {".git", ".miniclaw"} for part in relative.parts):
        return None, "Error: output_directory cannot target internal project state"
    if resolved.exists() and any(resolved.iterdir()) and not overwrite:
        return None, (
            f"Error: output directory is not empty: {relative.as_posix()}. "
            "Choose a new directory or set overwrite=true with approval."
        )
    return resolved, None


def _subprocess_environment(source: Path, include_api_key: bool) -> dict[str, str]:
    allowed = (
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
        "USERPROFILE", "HOME", "VIRTUAL_ENV", "PYTHONHOME", "LAMMPS_POTENTIALS",
    )
    environment = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(source), existing_pythonpath) if value
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    if include_api_key:
        environment["MDSYNTH_BRIDGE_API_KEY"] = config.llm.resolved_api_key
    executable = os.environ.get("MDSYNTH_LAMMPS_EXECUTABLE", "")
    if executable:
        environment["MDSYNTH_LAMMPS_EXECUTABLE"] = executable
    return environment


@tool_registry.register(
    name="generate_lammps_script",
    description=(
        "Compile a natural-language molecular-dynamics goal into a validated LAMMPS "
        "script and evidence package using MDSynth."
    ),
    schema={
        "type": "function",
        "function": {
            "name": "generate_lammps_script",
            "description": (
                "Generate an auditable LAMMPS input package inside the active project. "
                "Supports deterministic templates for relaxation, NVT, NPT, tension, "
                "and thermal expansion. Does not run the final simulation."
            ),
            "parameters": {
                "type": "object",
                "required": ["request"],
                "properties": {
                    "request": {
                        "type": "string",
                        "description": "Complete MD research goal in Chinese or English.",
                    },
                    "output_directory": {
                        "type": "string",
                        "description": (
                            "New directory inside the active project workspace; default "
                            f"{DEFAULT_OUTPUT_DIRECTORY}."
                        ),
                    },
                    "preflight": {
                        "type": "boolean",
                        "description": "Run bounded LAMMPS preflight checks; default false.",
                    },
                    "use_current_llm": {
                        "type": "boolean",
                        "description": "Use MiniClaw's current LLM for intent extraction; default true.",
                    },
                    "allow_direct_generation": {
                        "type": "boolean",
                        "description": (
                            "Allow non-template LLM script generation. Keep false unless the user "
                            "explicitly accepts the weaker validation path."
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Allow replacing known files in a non-empty output directory.",
                    },
                    "confirmation": {
                        "type": "string",
                        "description": (
                            "Exact ALLOW MDSYNTH ADVANCED confirmation required for preflight, "
                            "direct generation, or overwrite."
                        ),
                    },
                },
            },
        },
    },
    require_approval=True,
    risk_level="high",
    tags=["lammps", "simulation", "compiler", "filesystem"],
    timeout_sec=360,
)
def generate_lammps_script_tool(
    request: str,
    output_directory: str = DEFAULT_OUTPUT_DIRECTORY,
    preflight: bool = False,
    use_current_llm: bool = True,
    allow_direct_generation: bool = False,
    overwrite: bool = False,
    confirmation: str = "",
    **_kwargs,
) -> str:
    objective = str(request or "").strip()
    if not objective:
        return "Error: request is required"
    if len(objective) > MAX_REQUEST_CHARS:
        return f"Error: request exceeds {MAX_REQUEST_CHARS} characters"
    if use_current_llm and not config.llm.resolved_api_key:
        return "Error: the current MiniClaw LLM has no configured API key"
    if (preflight or allow_direct_generation or overwrite) and confirmation != "ALLOW MDSYNTH ADVANCED":
        return (
            "Error: preflight, direct generation, and overwrite require exact confirmation: "
            "ALLOW MDSYNTH ADVANCED"
        )

    source, source_error = _resolve_mdsynth_source()
    if source_error or source is None:
        return f"Error: {source_error}"
    output, output_error = _resolve_output_directory(output_directory, bool(overwrite))
    if output_error or output is None:
        return output_error or "Error: invalid output directory"

    runner = Path(__file__).resolve().parent.parent / "integrations" / "mdsynth_runner.py"
    payload: dict[str, Any] = {
        "request": objective,
        "source_directory": str(source),
        "output_directory": str(output),
        "preflight": bool(preflight),
        "use_current_llm": bool(use_current_llm),
        "allow_direct_generation": bool(allow_direct_generation),
        "model": config.llm.model,
        "base_url": config.llm.base_url,
        "rag_api_url": (
            os.environ.get("MDSYNTH_RAG_API_URL")
            or os.environ.get("LAMMPS_RAG_API_URL")
            or "http://localhost:3000"
        ),
        "rag_required": _environment_flag("MDSYNTH_RAG_REQUIRED", True),
    }
    with tempfile.TemporaryDirectory(prefix="miniclaw_mdsynth_") as temporary:
        temp_dir = Path(temporary)
        request_file = temp_dir / "request.json"
        result_file = temp_dir / "result.json"
        request_file.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--request-file", str(request_file),
                "--result-file", str(result_file),
            ],
            cwd=temp_dir,
            env=_subprocess_environment(source, bool(use_current_llm)),
            capture_output=True,
            text=True,
            timeout=330,
            check=False,
        )
        if not result_file.is_file():
            error = (result.stderr or result.stdout or "runner returned no result")[-2_000:]
            return f"Error: MDSynth bridge failed ({result.returncode}): {error}"
        response = json.loads(result_file.read_text(encoding="utf-8"))
    if not response.get("ok"):
        return "Error: MDSynth generation failed: " + str(response.get("error", "unknown error"))
    return json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True)
