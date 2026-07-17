"""Compile structured experiences into reviewable, constrained tool workflows."""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import shlex
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .experience import Experience


PolicyExecutor = Callable[[str, Dict[str, Any]], Any | Awaitable[Any]]


@dataclass
class ExecutablePolicyDraft:
    name: str
    task_pattern: str
    experience_ids: List[str]
    path: str
    policy_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "draft"
    created_at: float = field(default_factory=time.time)
    tested_at: float = 0.0
    approved_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExecutablePolicyManager:
    """Manage policy drafts; generated content is declarative, never arbitrary code."""

    ALLOWED_TOOLS = frozenset({"read", "write", "execute"})
    ALLOWED_ACTIONS = frozenset({"read", "write", "execute", "verify"})
    FORBIDDEN_COMMAND_FAMILIES = frozenset({
        "rm", "del", "erase", "format", "shutdown", "reboot", "curl", "wget",
        "powershell", "pwsh", "cmd", "bash", "sh",
    })
    FORBIDDEN_COMMAND = re.compile(
        r"(?:^|\s)(?:rm\s+-rf|del\s+/[sq]|format(?:\.com)?|shutdown|reboot)(?:\s|$)|"
        r"\b(?:curl|wget|invoke-webrequest|invoke-restmethod)\b|"
        r"(?:>|>>)\s*(?:/etc/|[a-z]:\\windows)",
        re.IGNORECASE,
    )
    RUNNER_TEMPLATE = '''"""Fixed MiniClaw executable-policy wrapper; contains no generated code."""
import json
from pathlib import Path


def load_policy():
    return json.loads(Path(__file__).with_name("policy.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    policy = load_policy()
    print(f"Policy {policy['name']} must run through MiniClaw's restricted policy executor.")
'''

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".miniclaw" / "evolution" / "executable-policies"
        self.drafts_root = self.root / "drafts"
        self.approved_root = self.root / "approved"

    def compile(
        self,
        experiences: List[Experience],
        *,
        task_pattern: str,
        min_experiences: int = 2,
    ) -> ExecutablePolicyDraft:
        selected = sorted(
            [
                item for item in experiences
                if item.status == "verified" and item.task_pattern == task_pattern and item.strategy
            ],
            key=lambda item: item.experience_id,
        )
        if len(selected) < min_experiences:
            raise ValueError(
                f"requires at least {min_experiences} verified structured experiences"
            )
        experience_ids = [item.experience_id for item in selected]
        existing = self._matching_draft(task_pattern, experience_ids)
        if existing is not None:
            return existing
        name = _policy_name(task_pattern)
        policy_id = uuid.uuid4().hex
        directory = self.drafts_root / policy_id
        path = directory / "policy.json"
        policy = {
            "schema_version": 1,
            "name": name,
            "task_pattern": task_pattern,
            "conditions": _unique(selected, "conditions"),
            "steps": _policy_steps(selected),
            "validation": _unique(selected, "validation"),
            "fallback": _unique(selected, "fallback"),
            "safety": [
                "run only after explicit policy approval",
                "keep paths inside the selected workspace",
                "reject unknown tools, network tools, and dangerous commands",
                "stop after the first failed step",
            ],
            "experience_ids": experience_ids,
        }
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (directory / "runner.py").write_text(self.RUNNER_TEMPLATE, encoding="utf-8")
        draft = ExecutablePolicyDraft(
            policy_id=policy_id,
            name=name,
            task_pattern=task_pattern,
            experience_ids=experience_ids,
            path=str(path),
        )
        self._save(draft)
        return draft

    def validate(self, policy_id: str) -> Dict[str, Any]:
        draft = self.get(policy_id)
        if draft is None:
            raise FileNotFoundError("executable policy draft not found")
        policy = json.loads(Path(draft.path).read_text(encoding="utf-8"))
        errors: List[str] = []
        if policy.get("schema_version") != 1:
            errors.append("unsupported policy schema")
        if policy.get("name") != draft.name:
            errors.append("policy name does not match metadata")
        steps = policy.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append("policy requires at least one step")
            steps = []
        for index, step in enumerate(steps, 1):
            tool = str(step.get("tool", ""))
            action = str(step.get("action", ""))
            if tool not in self.ALLOWED_TOOLS:
                errors.append(f"step {index} uses forbidden or unknown tool: {tool}")
            if action not in self.ALLOWED_ACTIONS:
                errors.append(f"step {index} uses unsupported action: {action}")
            if action == "execute" and not step.get("command_family"):
                errors.append(f"step {index} has no command family restriction")
            if step.get("command_family") in self.FORBIDDEN_COMMAND_FAMILIES:
                errors.append(f"step {index} uses a forbidden command family")
        runner = Path(draft.path).with_name("runner.py")
        if not runner.is_file() or runner.read_text(encoding="utf-8") != self.RUNNER_TEMPLATE:
            errors.append("runner template was modified")
        return {"valid": not errors, "errors": errors, "policy": policy, "draft": draft.to_dict()}

    async def isolated_test(self, policy_id: str) -> Dict[str, Any]:
        validation = self.validate(policy_id)
        if not validation["valid"]:
            return {"passed": False, "errors": validation["errors"], "calls": []}
        calls = []

        async def fake_executor(tool: str, arguments: Dict[str, Any]):
            calls.append({"tool": tool, "arguments": arguments})
            return "ok"

        with tempfile.TemporaryDirectory(prefix="miniclaw-policy-test-") as directory:
            workspace = Path(directory)
            bindings = self._test_bindings(validation["policy"], workspace)
            result = await self._run_policy(
                validation["policy"], bindings, fake_executor, workspace, timeout_sec=5
            )
        draft = self.get(policy_id)
        assert draft is not None
        draft.tested_at = time.time() if result["passed"] else 0.0
        draft.status = "tested" if result["passed"] else "draft"
        self._save(draft)
        return {"passed": result["passed"], "errors": result["errors"], "calls": calls}

    async def approve(self, policy_id: str, confirmation: str) -> Dict[str, Any]:
        draft = self.get(policy_id)
        if draft is None:
            raise FileNotFoundError("executable policy draft not found")
        if confirmation != draft.name:
            raise PermissionError("confirmation must exactly match the executable policy name")
        tested = await self.isolated_test(policy_id)
        if not tested["passed"]:
            raise ValueError("executable policy did not pass isolated validation")
        destination = self.approved_root / draft.name
        if destination.exists():
            raise FileExistsError("an approved executable policy already uses this name")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(Path(draft.path).parent, destination)
        draft.status = "approved"
        draft.approved_at = time.time()
        self._save(draft)
        approved = ExecutablePolicyDraft(**draft.to_dict())
        approved.path = str(destination / "policy.json")
        self._save(approved)
        return {"installed": True, "path": str(destination), "policy": approved.to_dict()}

    def reject(self, policy_id: str) -> Dict[str, Any]:
        draft = self.get(policy_id)
        if draft is None:
            raise FileNotFoundError("executable policy draft not found")
        draft.status = "rejected"
        self._save(draft)
        return draft.to_dict()

    async def run(
        self,
        policy_id: str,
        bindings: Dict[str, Dict[str, Any]],
        executor: PolicyExecutor,
        *,
        workspace: Path | str,
        timeout_sec: float = 30,
    ) -> Dict[str, Any]:
        policy = self._approved_policy(policy_id)
        if policy is None:
            raise PermissionError("executable policy is not approved")
        return await self._run_policy(
            policy, bindings, executor, Path(workspace).resolve(), timeout_sec
        )

    async def _run_policy(
        self,
        policy: Dict[str, Any],
        bindings: Dict[str, Dict[str, Any]],
        executor: PolicyExecutor,
        workspace: Path,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        results = []
        errors = []
        for step in policy.get("steps", []):
            step_id = str(step["step_id"])
            try:
                arguments = self._validate_binding(step, bindings.get(step_id, {}), workspace)
                result = executor(str(step["tool"]), arguments)
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(result, timeout=timeout_sec)
                text = str(result)
                if text.lstrip().lower().startswith(("error", "[error")):
                    raise RuntimeError("tool reported an error")
                results.append({"step_id": step_id, "tool": step["tool"], "result": text[:500]})
            except Exception as exc:
                errors.append(f"{step_id}: {exc}")
                break
        return {"passed": not errors, "results": results, "errors": errors}

    def _validate_binding(
        self, step: Dict[str, Any], arguments: Dict[str, Any], workspace: Path
    ) -> Dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("step binding must be an object")
        required = set(step.get("required_arguments", []))
        missing = sorted(required - set(arguments))
        if missing:
            raise ValueError(f"missing required arguments: {', '.join(missing)}")
        unexpected = sorted(set(arguments) - required)
        if unexpected:
            raise ValueError(f"unexpected arguments: {', '.join(unexpected)}")
        clean = dict(arguments)
        for key, value in clean.items():
            if not isinstance(value, str):
                continue
            if any(token in key.lower() for token in ("path", "file", "input", "output")):
                target = Path(value)
                target = target.resolve() if target.is_absolute() else (workspace / target).resolve()
                if target != workspace and workspace not in target.parents:
                    raise PermissionError("path escapes the approved workspace")
                clean[key] = str(target)
        if step.get("action") == "execute":
            command = str(clean.get("command", ""))
            if self.FORBIDDEN_COMMAND.search(command):
                raise PermissionError("dangerous or network command is forbidden")
            family = _command_family(command)
            if family != step.get("command_family"):
                raise PermissionError(
                    f"command family {family} does not match approved family {step.get('command_family')}"
                )
            self._validate_command(command, family, workspace)
        return clean

    def _validate_command(self, command: str, family: str, workspace: Path) -> None:
        if family in self.FORBIDDEN_COMMAND_FAMILIES:
            raise PermissionError("command family is forbidden")
        if re.search(r"[;&|`$<>\r\n]", command):
            raise PermissionError("shell operators and redirection are forbidden")
        try:
            tokens = [token.strip('"\'') for token in shlex.split(command, posix=False)]
        except ValueError as exc:
            raise ValueError("command quoting is invalid") from exc
        lowered = [token.lower() for token in tokens[1:]]
        if family == "python":
            if any(token in {"-c", "-m"} for token in lowered):
                raise PermissionError("inline Python and module execution are forbidden")
            script = next((token for token in tokens[1:] if token.lower().endswith(".py")), "")
            if script:
                self._ensure_workspace_path(script, workspace)
            elif lowered != ["--version"]:
                raise PermissionError("Python policy requires a workspace script or --version")
        if family in {"lmp", "lammps", "lmp_serial", "lmp_mpi"}:
            for flag in ("-in", "-i"):
                if flag in lowered:
                    index = lowered.index(flag) + 2
                    if index >= len(tokens):
                        raise ValueError("LAMMPS input flag requires a file")
                    self._ensure_workspace_path(tokens[index], workspace)

    @staticmethod
    def _ensure_workspace_path(value: str, workspace: Path) -> Path:
        target = Path(value)
        target = target.resolve() if target.is_absolute() else (workspace / target).resolve()
        if target != workspace and workspace not in target.parents:
            raise PermissionError("command references a path outside the approved workspace")
        return target

    def _test_bindings(self, policy: Dict[str, Any], workspace: Path) -> Dict[str, Dict[str, Any]]:
        bindings = {}
        for step in policy.get("steps", []):
            action = step["action"]
            if action == "read":
                sample = workspace / "sample.txt"
                sample.write_text("sample", encoding="utf-8")
                bindings[step["step_id"]] = {"path": "sample.txt"}
            elif action == "write":
                bindings[step["step_id"]] = {"path": "output.txt", "content": "sample"}
            elif action == "execute":
                family = step["command_family"]
                bindings[step["step_id"]] = {"command": f"{family} --version"}
            else:
                bindings[step["step_id"]] = {}
        return bindings

    def list_drafts(self) -> List[Dict[str, Any]]:
        return self._list_metadata(self.drafts_root)

    def list_approved(self) -> List[Dict[str, Any]]:
        return self._list_metadata(self.approved_root)

    def get(self, policy_id: str) -> Optional[ExecutablePolicyDraft]:
        metadata = self.drafts_root / Path(policy_id).name / "metadata.json"
        if not metadata.is_file():
            return None
        return ExecutablePolicyDraft(**json.loads(metadata.read_text(encoding="utf-8")))

    def _approved_policy(self, policy_id: str) -> Optional[Dict[str, Any]]:
        for metadata in self.approved_root.glob("*/metadata.json"):
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            if payload.get("policy_id") == policy_id and payload.get("status") == "approved":
                return json.loads(metadata.with_name("policy.json").read_text(encoding="utf-8"))
        return None

    def _matching_draft(
        self, task_pattern: str, experience_ids: List[str]
    ) -> Optional[ExecutablePolicyDraft]:
        for payload in self.list_drafts():
            if payload.get("status") == "rejected":
                continue
            if payload.get("task_pattern") == task_pattern and sorted(payload.get("experience_ids", [])) == sorted(experience_ids):
                return ExecutablePolicyDraft(**payload)
        return None

    @staticmethod
    def _list_metadata(root: Path) -> List[Dict[str, Any]]:
        rows = []
        if root.is_dir():
            for metadata in root.glob("*/metadata.json"):
                try:
                    rows.append(json.loads(metadata.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return sorted(rows, key=lambda item: item.get("created_at", 0), reverse=True)

    @staticmethod
    def _save(draft: ExecutablePolicyDraft) -> None:
        metadata = Path(draft.path).parent / "metadata.json"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(
            json.dumps(draft.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _policy_steps(experiences: List[Experience]) -> List[Dict[str, Any]]:
    rows = []
    signatures = set()
    for experience in experiences:
        for step in experience.strategy.get("steps", []):
            action = str(step.get("action", "invoke"))
            tool = str(step.get("tool", ""))
            family = str(step.get("command_family", ""))
            signature = (tool, action, str(step.get("resource_type", "")), family)
            if signature in signatures:
                continue
            signatures.add(signature)
            required = {
                "read": ["path"], "write": ["path", "content"],
                "execute": ["command"], "verify": [],
            }.get(action, [])
            rows.append({
                "step_id": f"step-{len(rows) + 1}",
                "tool": tool,
                "action": action,
                "intent": str(step.get("intent", "Perform the approved step.")),
                "resource_type": str(step.get("resource_type", "generic-resource")),
                "command_family": family,
                "constraints": list(step.get("constraints", [])),
                "required_arguments": required,
            })
    return rows


def _unique(experiences: List[Experience], field_name: str) -> List[str]:
    values = []
    for experience in experiences:
        for item in experience.strategy.get(field_name, []):
            value = str(item).strip()
            if value and value not in values:
                values.append(value)
    return values


def _policy_name(task_pattern: str) -> str:
    words = re.findall(r"[a-z0-9]+", task_pattern.lower())
    base = "-".join(words[:6]) or "validated-workflow"
    return f"run-{base}"[:64].rstrip("-")


def _command_family(command: str) -> str:
    first = re.split(r"\s+", command.strip(), maxsplit=1)[0].strip('"\'')
    family = Path(first.replace("\\", "/")).name.lower()
    return re.sub(r"\.(exe|cmd|bat|ps1|sh)$", "", family)
