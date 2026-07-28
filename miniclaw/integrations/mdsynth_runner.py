"""Process-isolated runner for the external MDSynth compiler.

This module is intentionally dependency-free until ``main`` adds the selected
MDSynth repository to ``sys.path``.  It is launched by the MiniClaw tool bridge,
not imported as part of normal startup.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import traceback
from typing import Any


FORBIDDEN_DIRECT_COMMANDS = frozenset({"shell", "python", "jump", "include"})
PATH_WRITING_COMMANDS = frozenset({
    "dump", "log", "restart", "write_data", "write_dump", "write_restart",
})


def _direct_script_safety_issues(script: str) -> list[str]:
    """Reject host execution and paths escaping the disposable preflight cwd."""
    issues: list[str] = []
    for number, raw_line in enumerate(script.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        command = tokens[0].lower()
        if command in FORBIDDEN_DIRECT_COMMANDS:
            issues.append(f"line {number}: forbidden LAMMPS command '{command}'")
            continue
        if command not in PATH_WRITING_COMMANDS:
            continue
        for token in tokens[1:]:
            cleaned = token.strip("\"'")
            if Path(cleaned).is_absolute() or ".." in Path(cleaned).parts:
                issues.append(f"line {number}: output path must stay in the preflight sandbox")
                break
    return issues


def _patch_direct_preflight(enabled: bool) -> None:
    """Make direct generation static-only unless preflight was explicitly requested."""
    from mdsynth.llm_direct.generator import LLMDirectGenerator

    original = LLMDirectGenerator._check_script

    def guarded_check(self, script: str, work_dir=None):
        issues = _direct_script_safety_issues(script)
        if issues:
            return False, "; ".join(issues), ""
        if not enabled:
            return True, "", ""
        return original(self, script, work_dir)

    LLMDirectGenerator._check_script = guarded_check


def _source_state(source: Path) -> dict[str, Any]:
    import subprocess

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    return {
        "path": str(source),
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }


def run(payload: dict[str, Any]) -> dict[str, Any]:
    source = Path(payload["source_directory"]).resolve(strict=True)
    output_dir = Path(payload["output_directory"]).resolve()
    sys.path.insert(0, str(source))

    from mdsynth import MDSynthPipeline
    from mdsynth.config import MDSynthConfig
    from mdsynth.llm.base import MockLLMBackend
    from mdsynth.llm.openai_backend import OpenAIBackend

    preflight = bool(payload.get("preflight", False))
    use_current_llm = bool(payload.get("use_current_llm", True))
    allow_direct = bool(payload.get("allow_direct_generation", False))
    _patch_direct_preflight(preflight)

    if use_current_llm:
        backend = OpenAIBackend(
            model=str(payload.get("model", "")),
            api_key=os.environ.get("MDSYNTH_BRIDGE_API_KEY", ""),
            base_url=str(payload.get("base_url", "")) or None,
            use_strict_schema=False,
        )
    else:
        backend = MockLLMBackend()

    config = MDSynthConfig(
        output_directory=output_dir,
        sandbox_enabled=preflight,
        lammps_executable=os.environ.get("MDSYNTH_LAMMPS_EXECUTABLE", ""),
        lammps_potentials_dir=os.environ.get("LAMMPS_POTENTIALS", ""),
        rag_api_url=str(payload.get("rag_api_url", "")),
        rag_required=bool(payload.get("rag_required", False)),
    )
    pipeline = MDSynthPipeline(llm_backend=backend, config=config)
    if not allow_direct:
        def direct_generation_blocked(*_args, **_kwargs):
            raise RuntimeError(
                "request is outside the deterministic MDSynth templates; "
                "set allow_direct_generation=true only with explicit user approval"
            )

        pipeline._run_direct_generation = direct_generation_blocked

    output = pipeline.run(str(payload["request"]))
    pipeline.save(output, str(output_dir))
    files = sorted(
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file()
    )
    return {
        "ok": True,
        "success": bool(output.success),
        "output_directory": str(output_dir),
        "script": str(output_dir / "in.main.lammps"),
        "files": files,
        "preflight_requested": preflight,
        "direct_generation_allowed": allow_direct,
        "source": _source_state(source),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--result-file", required=True)
    arguments = parser.parse_args()
    result_path = Path(arguments.result_file)
    try:
        payload = json.loads(Path(arguments.request_file).read_text(encoding="utf-8"))
        result = run(payload)
        exit_code = 0
    except Exception as exc:
        result = {
            "ok": False,
            "error_type": exc.__class__.__name__,
            "error": str(exc)[:2_000],
            "traceback": traceback.format_exc(limit=8)[-6_000:],
        }
        exit_code = 1
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
