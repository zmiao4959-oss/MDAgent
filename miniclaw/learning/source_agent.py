"""Constrained LLM adapter for generating one test-first source candidate."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path, PurePosixPath
from typing import Dict, List

from ..llm.base import LLMMessage
from .source_experiment import PatchActionResult, SourcePatchPolicy
from .source_proposal import SourceProposal


_SAFE_SOURCE_SUFFIXES = frozenset({".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml"})
_UNSAFE_TEST_CONTENT = re.compile(
    r"\b(?:subprocess|socket|requests|urllib|shutil)\b|"
    r"\bos\.system\b|\b(?:eval|exec)\s*\(|\bopen\s*\(",
    re.IGNORECASE,
)


class LLMSourcePatchAgent:
    """Ask an LLM for JSON edits, then apply only deterministic safe operations."""

    def __init__(
        self,
        llm,
        *,
        max_context_files: int = 6,
        max_context_chars: int = 30000,
        timeout_sec: int = 120,
        max_edits: int = 4,
    ):
        self.llm = llm
        self.max_context_files = max(1, min(20, int(max_context_files)))
        self.max_context_chars = max(1000, min(100000, int(max_context_chars)))
        self.timeout_sec = max(5, min(600, int(timeout_sec)))
        self.max_edits = max(1, min(10, int(max_edits)))
        self._allowed_source_files: List[str] = []

    async def write_reproduction_test(
        self, worktree: Path, proposal: SourceProposal
    ) -> PatchActionResult:
        context = self._source_context(worktree, proposal)
        payload = await self._request_json(
            "reproduction test",
            _test_prompt(proposal, context),
        )
        edits = self._edits(payload)
        if not edits:
            raise ValueError("LLM did not provide a reproduction test edit")
        test_paths: List[str] = []
        for edit in edits:
            if edit.get("operation") != "create":
                raise PermissionError("reproduction plan may only create generated tests")
            path = _safe_relative_path(str(edit.get("path", "")))
            if (
                not path.startswith(SourcePatchPolicy.GENERATED_TEST_PREFIX)
                or not path.endswith(".py")
            ):
                raise PermissionError(
                    "reproduction test must use tests/test_evolution_generated_*.py"
                )
            target = worktree / path
            if target.exists():
                raise FileExistsError("generated reproduction test already exists")
            content = str(edit.get("content", ""))
            if not content.strip() or _UNSAFE_TEST_CONTENT.search(content):
                raise PermissionError("generated reproduction test contains unsafe content")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            test_paths.append(path)
        return PatchActionResult(
            summary=_safe_summary(payload),
            test_commands=[
                ["python", "-m", "pytest", "-q", path] for path in test_paths
            ],
        )

    async def implement_patch(
        self, worktree: Path, proposal: SourceProposal
    ) -> PatchActionResult:
        if not self._allowed_source_files:
            self._source_context(worktree, proposal)
        context = self._source_context(worktree, proposal, include_generated_tests=True)
        payload = await self._request_json(
            "implementation patch",
            _patch_prompt(proposal, context, self._allowed_source_files),
        )
        edits = self._edits(payload)
        if not edits:
            raise ValueError("LLM did not provide an implementation edit")
        for edit in edits:
            if edit.get("operation") != "replace":
                raise PermissionError("implementation plan may only use exact replacements")
            path = _safe_relative_path(str(edit.get("path", "")))
            if path not in self._allowed_source_files:
                raise PermissionError(f"implementation edit is outside suspected files: {path}")
            target = worktree / path
            old = str(edit.get("old", ""))
            new = str(edit.get("new", ""))
            if not old or old == new:
                raise ValueError("replacement must change a non-empty exact source fragment")
            current = target.read_text(encoding="utf-8")
            if current.count(old) != 1:
                raise ValueError("replacement source fragment must occur exactly once")
            target.write_text(current.replace(old, new, 1), encoding="utf-8")
        return PatchActionResult(summary=_safe_summary(payload))

    def _source_context(
        self,
        worktree: Path,
        proposal: SourceProposal,
        *,
        include_generated_tests: bool = False,
    ) -> str:
        candidates: List[str] = []
        for raw in proposal.suspected_files:
            path = _safe_relative_path(raw)
            target = worktree / path
            if (
                target.is_file()
                and target.suffix.lower() in _SAFE_SOURCE_SUFFIXES
                and not path.startswith("tests/")
                and not any(
                    path == prefix or path.startswith(prefix)
                    for prefix in SourcePatchPolicy.PROTECTED_PREFIXES
                )
            ):
                candidates.append(path)
        candidates = list(dict.fromkeys(candidates))[: self.max_context_files]
        if not candidates:
            raise ValueError("source proposal has no safe suspected source files")
        self._allowed_source_files = list(candidates)
        if include_generated_tests:
            candidates.extend(
                path.relative_to(worktree).as_posix()
                for path in sorted(
                    (worktree / "tests").glob("test_evolution_generated_*.py")
                )
                if path.is_file()
            )
        remaining = self.max_context_chars
        sections = []
        for path in candidates:
            content = (worktree / path).read_text(encoding="utf-8", errors="replace")
            content = _redact_source(content)[:remaining]
            if not content:
                continue
            sections.append(f"--- {path} ---\n{content}")
            remaining -= len(content)
            if remaining <= 0:
                break
        return "\n\n".join(sections)

    async def _request_json(self, phase: str, prompt: str) -> Dict:
        response = await asyncio.wait_for(
            self.llm.chat(
                [
                    LLMMessage(
                        role="system",
                        content=(
                            "You are a constrained source repair planner. Return one JSON "
                            "object only. Never request shell commands, network access, "
                            "dependency installation, or changes outside the listed files."
                        ),
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                tools=None,
                temperature=0.1,
                max_tokens=4096,
            ),
            timeout=self.timeout_sec,
        )
        content = str(response.content or "")
        try:
            value = json.loads(_strip_json_fence(content))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"LLM returned invalid JSON for {phase}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"LLM response for {phase} must be a JSON object")
        return value

    def _edits(self, payload: Dict) -> List[Dict]:
        edits = payload.get("edits")
        if not isinstance(edits, list) or not all(isinstance(item, dict) for item in edits):
            raise ValueError("LLM response must contain an edits array")
        if len(edits) > self.max_edits:
            raise ValueError(f"LLM proposed more than {self.max_edits} edits")
        return edits


def _safe_relative_path(raw: str) -> str:
    value = raw.strip().replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or value.startswith(".git/")
    ):
        raise PermissionError("LLM edit path must be a normalized repository-relative path")
    return value


def _strip_json_fence(content: str) -> str:
    value = content.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else value


def _safe_summary(payload: Dict) -> str:
    return re.sub(r"\s+", " ", str(payload.get("summary", "LLM source edit"))).strip()[:300]


def _redact_source(content: str) -> str:
    value = re.sub(
        r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*['\"]?[^\s,'\"]+",
        r"\1=[REDACTED]",
        content,
    )
    return re.sub(r"(?i)bearer\s+[a-z0-9._-]+", "Bearer [REDACTED]", value)


def _proposal_text(proposal: SourceProposal) -> str:
    return json.dumps(
        {
            "title": proposal.title,
            "error_category": proposal.error_category,
            "sanitized_examples": proposal.sanitized_examples,
            "hypothesis": proposal.hypothesis,
            "expected_benefit": proposal.expected_benefit,
            "risk": proposal.risk,
            "suspected_files": proposal.suspected_files,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _test_prompt(proposal: SourceProposal, context: str) -> str:
    return f"""Create the smallest deterministic pytest regression test for this proposal.
The test must fail against the current implementation and must not access network, shell,
environment secrets, or files outside normal imports.

Proposal:
{_proposal_text(proposal)}

Allowed source context (read-only):
{context}

Return exactly:
{{"summary":"...","edits":[{{"operation":"create","path":"tests/test_evolution_generated_<name>.py","content":"..."}}]}}
"""


def _patch_prompt(proposal: SourceProposal, context: str, allowed_files: List[str]) -> str:
    return f"""Fix the implementation so the generated regression test passes.
Use only exact text replacement in these files: {json.dumps(allowed_files)}.
Keep the change minimal and do not change tests, dependencies, security controls, or APIs.

Proposal:
{_proposal_text(proposal)}

Current source and generated test:
{context}

Return exactly:
{{"summary":"...","edits":[{{"operation":"replace","path":"one allowed file","old":"exact existing text","new":"replacement text"}}]}}
"""
