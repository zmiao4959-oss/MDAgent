"""Versioned, per-project Skill drafts and agent-specific temporary mounts.

Generated Skills never enter global discovery or modify AgentProfile. Python
tests run in a temporary working directory with a minimal environment; this is
process isolation for validation, NOT an operating-system security sandbox.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any

import yaml

from .loader import SkillInfo, SkillLoader

BUILDER = "skill-builder"
MAX_PACKAGE_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_FILES = 100
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_LOCK = threading.RLock()
_TEST_RUNNER = """
import json, pathlib, sys, unittest
package, report = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(package))
suite = unittest.defaultTestLoader.discover(str(package / 'tests'), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
report.write_text(json.dumps({'tests_run': result.testsRun, 'failures': len(result.failures),
    'errors': len(result.errors), 'skipped': len(result.skipped)}), encoding='utf-8')
sys.exit(0 if result.wasSuccessful() and result.testsRun > len(result.skipped) else 1)
"""


def tools_allow(required: list[str], allowed: Any) -> bool:
    return "*" in allowed or set(required).issubset(set(allowed))


def snapshot(folder: Path) -> tuple[dict[str, bytes], str]:
    files: dict[str, bytes] = {}
    total = 0
    if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        raise ValueError("Skill 包不能使用符号链接或 junction")
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Skill 包不能包含符号链接或 junction")
        if not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("Skill 文件越出包目录")
        if path.is_dir():
            continue
        if path.suffix.lower() not in {".md", ".py", ".txt", ".json", ".yaml", ".yml", ".csv"}:
            raise ValueError(f"第一版只接受 Python 和文本资源：{path.name}")
        size = path.stat().st_size
        total += size
        if total > MAX_PACKAGE_BYTES or len(files) >= MAX_PACKAGE_FILES:
            raise ValueError("Skill 包超过 100 文件或 4 MiB 上限")
        data = path.read_bytes()
        if len(data) != size:
            raise ValueError("Skill 文件在校验期间发生变化，请重试")
        files[path.relative_to(folder).as_posix()] = data
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
    return files, digest.hexdigest()


class ProjectSkillStore:
    def __init__(self, workspace: Path | str, project_id: str):
        if not project_id:
            raise ValueError("临时 Skill 需要明确的项目")
        self.workspace = Path(workspace).resolve()
        self.project_id = project_id
        self.root = self.workspace / ".miniclaw" / "skill-workbench"
        if not self.root.resolve().is_relative_to(self.workspace):
            raise ValueError("Skill 工作目录越出项目")
        self.index = self.root / "index.json"

    def _read(self) -> dict:
        if self.index.is_symlink():
            raise ValueError("Skill 清单不能是符号链接")
        if not self.index.exists():
            return {"version": 1, "project_id": self.project_id, "requests": {}, "bindings": {}}
        value = json.loads(self.index.read_text(encoding="utf-8"))
        if value.get("project_id") != self.project_id or value.get("version") != 1:
            raise ValueError("Skill 清单不属于当前项目")
        for request in value["requests"].values():
            if request.get("status") == "validating" and request.get("validation_pid") != os.getpid():
                request.update(status="validation_failed", error="上次校验被进程重启中断，可重新发布")
        return value

    def _save(self, value: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f"index-{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.index)

    def draft(self, request_id: str) -> Path:
        if not re.fullmatch(r"skillreq-[a-f0-9]{32}", request_id):
            raise ValueError("无效的 Skill 请求 ID")
        path = self.root / "requests" / request_id / "package"
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Skill 草稿目录越界")
        return path

    def create_request(self, profile: Any, objective: str, acceptance: list[str]) -> dict:
        if not profile.allows_tool("read_skill"):
            raise ValueError(f"{profile.name} 没有 read_skill 权限，不能通过挂载隐式新增工具")
        if not isinstance(objective, str) or not 1 <= len(objective.strip()) <= 4000:
            raise ValueError("objective 必须是 1–4000 字符的明确需求")
        if not isinstance(acceptance, list) or not 1 <= len(acceptance) <= 20 or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 2000 for v in acceptance):
            raise ValueError("acceptance 需要 1–20 条非空验收条件")
        if sum(map(len, acceptance)) > 8000:
            raise ValueError("验收条件总长度不能超过 8000 字符")
        with _LOCK:
            index = self._read()
            for request in index["requests"].values():
                if request["requester"] == profile.name and request["objective"] == objective.strip() and request["acceptance"] == acceptance and request["status"] in ("requested", "validation_failed"):
                    return {**request, "draft_dir": str(self.draft(request["request_id"])), "reused": True}
            request_id = f"skillreq-{uuid.uuid4().hex}"
            draft = self.draft(request_id)
            draft.mkdir(parents=True)
            request = {
                "request_id": request_id, "requester": profile.name,
                "objective": objective.strip(), "acceptance": acceptance,
                "allowed_tools": list(profile.tools), "role_description": profile.description,
                "role_instructions": profile.system_prompt[:12000],
                "draft_dir": str(draft), "status": "requested", "created_at": time.time(),
            }
            index["requests"][request_id] = request
            self._save(index)
            return {**request, "reused": False}

    def requests(self, requester: str | None = None) -> list[dict]:
        with _LOCK:
            return [{**r, "draft_dir": str(self.draft(r["request_id"]))} for r in self._read()["requests"].values() if requester is None or r["requester"] == requester]

    def require_request(self, request_id: str) -> dict:
        self.draft(request_id)
        with _LOCK:
            request = self._read()["requests"].get(request_id)
            if request is None:
                raise ValueError("当前项目没有此 Skill 请求")
            return {**request, "draft_dir": str(self.draft(request_id))}

    def can_write_draft(self, path: Any) -> bool:
        if not isinstance(path, str) or not path.strip():
            return False
        resolved = (self.workspace / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        return any(
            r["status"] in ("requested", "validation_failed") and resolved.is_relative_to(self.draft(r["request_id"]).resolve())
            for r in self.requests()
        )

    def _validate(self, files: dict[str, bytes], profile: Any) -> dict:
        if "SKILL.md" not in files or "skill.yaml" not in files:
            raise ValueError("需要 SKILL.md 和 skill.yaml")
        content = files["SKILL.md"].decode("utf-8")
        frontmatter = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", content, re.DOTALL)
        if not frontmatter:
            raise ValueError("SKILL.md 需要标准 YAML frontmatter")
        try:
            header = yaml.safe_load(frontmatter.group(1))
        except yaml.YAMLError as exc:
            raise ValueError(f"SKILL.md frontmatter 无法解析：{exc}") from exc
        if not isinstance(header, dict) or not isinstance(header.get("name"), str) or not isinstance(header.get("description"), str):
            raise ValueError("Skill frontmatter 需要字符串 name 和 description")
        name, description = header["name"].strip(), header["description"].strip()
        if not content[frontmatter.end():].strip():
            raise ValueError("SKILL.md 需要非空操作说明")
        if not _NAME.fullmatch(name) or len(name) > 64 or not description or len(description) > 1000:
            raise ValueError("Skill name/description 无效；名称使用小写英文、数字和连字符")
        if SkillLoader().get(name) is not None:
            raise ValueError("临时 Skill 不能覆盖已有全局 Skill")
        try:
            manifest = yaml.safe_load(files["skill.yaml"].decode("utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"skill.yaml 无法解析：{exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("name") != name:
            raise ValueError("skill.yaml 的 schema_version/name 无效")
        version = manifest.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("Skill version 必须是如 '1.0.0' 的字符串")
        required = manifest.get("required_tools")
        if not isinstance(required, list) or any(not isinstance(v, str) or v == "*" for v in required):
            raise ValueError("required_tools 需要显式工具名称列表")
        if not tools_allow(["read_skill", *required], profile.tools):
            raise ValueError("Skill 需要请求者没有的工具权限；不能自动扩权")
        code = [name for name in files if name.endswith(".py") and not name.startswith("tests/")]
        if code and "execute" not in required:
            raise ValueError("包含 Python 实现的 Skill 必须声明 required_tools: [..., execute]")
        for path, data in files.items():
            if path.endswith(".py"):
                ast.parse(data.decode("utf-8"), filename=path)
        tests = [name for name in files if name.startswith("tests/test_") and name.endswith(".py")]
        if code and not tests:
            raise ValueError("含 Python 实现的 Skill 必须提供 tests/test_*.py")
        return {"name": name, "description": description, "version": version, "required_tools": required, "has_tests": bool(tests)}

    async def _run_tests(self, package: Path, report: Path, log: Path, timeout: float) -> dict:
        # Keep API credentials and service configuration out of generated tests.
        env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "SYSTEMDRIVE"}}
        with log.open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-B", "-c", _TEST_RUNNER, str(package), str(report),
                cwd=str(package.parent), env=env, stdout=output, stderr=output,
                **({"start_new_session": True} if os.name != "nt" else {}),
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    if os.name == "nt":
                        killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                        await killer.wait()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
                raise
        result = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {"tests_run": 0}
        with log.open("rb") as output:
            output.seek(max(0, log.stat().st_size - 12_000))
            result["log"] = output.read().decode("utf-8", errors="replace")
        if process.returncode != 0 or result.get("tests_run", 0) <= result.get("skipped", 0):
            raise ValueError("Skill 测试未通过：\n" + result["log"])
        return result

    async def publish(self, request_id: str, profile: Any, *, timeout: float = 30) -> dict:
        request = self.require_request(request_id)
        if request["requester"] != profile.name:
            raise ValueError("请求者与挂载目标不一致")
        if request["status"] == "mounted":
            active = self.list_bindings().get(profile.name, {}).get(request.get("skill_name"))
            if active is None or active.get("request_id") != request_id:
                raise ValueError("该请求的版本已被撤下或替代")
            info = self.mounted(profile).get(request.get("skill_name"))
            if info is None:
                raise ValueError("已发布版本不可用；请创建新的 Skill 请求")
            return {"status": "mounted", "request_id": request_id, "name": info.name, "reused": True}
        if request["status"] not in ("requested", "validation_failed"):
            raise ValueError("当前 Skill 请求不能发布")
        with _LOCK:
            index = self._read()
            index["requests"][request_id]["status"] = "validating"
            index["requests"][request_id]["validation_pid"] = os.getpid()
            index["requests"][request_id].pop("error", None)
            self._save(index)
        try:
            files, digest = snapshot(self.draft(request_id))
            metadata = self._validate(files, profile)
            previous = self.list_bindings().get(profile.name, {}).get(metadata["name"])
            if previous and tuple(map(int, metadata["version"].split("."))) <= tuple(map(int, previous["version"].split("."))):
                raise ValueError("替换同名 Skill 需要递增版本号")
            validation = {"tests_run": 0, "kind": "structure_only"}
            # Freeze before testing; edits to the draft cannot change the release.
            with tempfile.TemporaryDirectory(prefix="miniclaw-skill-test-") as directory:
                temporary = Path(directory).resolve()
                package = temporary / "package"
                package.mkdir()
                for relative, data in files.items():
                    path = package / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                if metadata["has_tests"]:
                    validation = await self._run_tests(package, temporary / "report.json", temporary / "tests.log", timeout)
                    validation["kind"] = "unittest_passed"
                    if snapshot(package)[1] != digest:
                        raise ValueError("测试修改了 Skill 包本身；测试输出应写入临时目录")
            release = self.root / "releases" / digest / metadata["name"]
            if not release.resolve().is_relative_to(self.root.resolve()):
                raise ValueError("Skill 发布目录越界")
            if release.exists():
                if snapshot(release)[1] != digest:
                    raise ValueError("已有发布目录的完整性校验失败")
            else:
                staging = self.root / "staging"
                staging.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="publish-", dir=staging) as directory:
                    staged = Path(directory) / metadata["name"]
                    staged.mkdir()
                    for relative, data in files.items():
                        path = staged / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                    release.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        staged.rename(release)
                    except FileExistsError:
                        if snapshot(release)[1] != digest:
                            raise ValueError("并发发布版本的完整性校验失败")
            binding = {**metadata, "digest": digest, "request_id": request_id, "location": str(release / "SKILL.md"), "validation": validation, "mounted_at": time.time()}
            with _LOCK:
                index = self._read()
                previous = index["bindings"].get(profile.name, {}).get(metadata["name"])
                if previous and tuple(map(int, metadata["version"].split("."))) <= tuple(map(int, previous["version"].split("."))):
                    raise ValueError("同名 Skill 已有相同或更新版本；请递增版本号")
                if previous:
                    index["requests"][previous["request_id"]]["status"] = "superseded"
                index["bindings"].setdefault(profile.name, {})[metadata["name"]] = binding
                index["requests"][request_id].update(status="mounted", skill_name=metadata["name"], digest=digest, validation=validation)
                self._save(index)
            return {"status": "mounted", "request_id": request_id, "name": metadata["name"], "version": metadata["version"], "digest": digest, "location": binding["location"], "validation": validation, "reused": False}
        except BaseException as exc:
            with _LOCK:
                index = self._read()
                index["requests"][request_id].update(status="validation_failed", error=str(exc)[:12_000] or type(exc).__name__)
                self._save(index)
            raise

    def mounted(self, profile: Any) -> dict[str, SkillInfo]:
        result = {}
        with _LOCK:
            bindings = dict(self._read()["bindings"].get(profile.name, {}))
        if not bindings:
            return result
        global_names = {skill.name for skill in SkillLoader().list_all()}
        for name, binding in bindings.items():
            try:
                if not _NAME.fullmatch(name) or name in global_names or not re.fullmatch(r"[a-f0-9]{64}", binding["digest"]):
                    continue
                if not tools_allow(["read_skill", *binding["required_tools"]], profile.tools):
                    continue
                release = self.root / "releases" / binding["digest"] / name
                if not release.resolve().is_relative_to(self.root.resolve()) or snapshot(release)[1] != binding["digest"]:
                    continue
                result[name] = SkillInfo(name, binding["description"], release / "SKILL.md", release)
            except (OSError, ValueError, KeyError):
                continue
        return result

    def list_bindings(self) -> dict:
        with _LOCK:
            return self._read()["bindings"]

    def revoke(self, agent: str, name: str) -> dict:
        with _LOCK:
            index = self._read()
            binding = index["bindings"].get(agent, {}).pop(name, None)
            if binding is None:
                raise ValueError("该智能体没有此临时 Skill")
            index["requests"][binding["request_id"]]["status"] = "revoked"
            self._save(index)
        return {"status": "revoked", "agent": agent, "name": name}


def project_store(metadata: dict) -> ProjectSkillStore | None:
    if metadata.get("project_id") and metadata.get("workspace_dir"):
        return ProjectSkillStore(metadata["workspace_dir"], metadata["project_id"])
    return None


def mounted_from_tool_context(context: dict) -> dict[str, SkillInfo]:
    store = project_store(context)
    if store is None or not context.get("agent_name") or context.get("allowed_tools") is None:
        return {}
    return store.mounted(SimpleNamespace(name=context["agent_name"], tools=context["allowed_tools"]))
