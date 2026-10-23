"""Shared SSH transport for independently configured batch schedulers."""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


SIMPLE = re.compile(r"^[A-Za-z0-9_.-]+$")
JOB_ID = re.compile(r"^[0-9]+$")


class RemoteError(RuntimeError):
    pass


def positive(value: Any, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if number < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return number


class SshTarget:
    script_name = ""

    def __init__(self, name: str, raw: dict[str, Any]):
        self.name = name
        self.alias = str(raw.get("ssh_alias", ""))
        self.base = str(raw.get("remote_base", ""))
        self.executable = str(raw.get("executable", ""))
        self.env_script = str(raw.get("env_script", ""))
        if not SIMPLE.fullmatch(self.alias):
            raise ValueError("ssh_alias must use a simple name")
        for label, path in (("remote_base", self.base), ("executable", self.executable)):
            if not PurePosixPath(path).is_absolute() or "\n" in path or "\r" in path:
                raise ValueError(f"{label} must be an absolute POSIX path")
        if self.env_script and (not PurePosixPath(self.env_script).is_absolute() or
                                "\n" in self.env_script or "\r" in self.env_script):
            raise ValueError("env_script must be an absolute POSIX path")

    def remote_dir(self, run_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{16}", run_id):
            raise ValueError("invalid run_id")
        return str(PurePosixPath(self.base) / run_id)

    def _ssh(self, script: str, *, timeout: int = 30) -> str:
        try:
            result = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.alias, "bash", "-l", "-s"],
                input=script, text=True, encoding="utf-8", errors="replace",
                capture_output=True, timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RemoteError(f"SSH call failed: {exc}") from exc
        if result.returncode:
            raise RemoteError(f"SSH exit {result.returncode}: {result.stderr[-1200:].strip()}")
        return result.stdout.strip()

    def stage(self, remote_dir: str, archive: Path) -> None:
        qdir = shlex.quote(remote_dir)
        command = f"mkdir -p -- {qdir} && tar -xf - -C {qdir}"
        try:
            with archive.open("rb") as stream:
                result = subprocess.run(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.alias, command],
                    stdin=stream, capture_output=True, timeout=300, check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RemoteError(f"upload failed: {exc}") from exc
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace")[-1200:]
            raise RemoteError(f"upload exit {result.returncode}: {detail.strip()}")

    def missing_outputs(self, remote_dir: str, paths: list[str]) -> list[str]:
        if not paths:
            return []
        checks = "\n".join(
            f"test -e {shlex.quote(remote_dir + '/' + path)} || printf '%s\\n' {shlex.quote(path)}"
            for path in paths
        )
        output = self._ssh("set -u\n" + checks + "\n")
        return output.splitlines() if output else []

    def log_tail(self, remote_dir: str, job_id: str, *, lines: int = 30) -> str:
        output = self._ssh(
            "set -u\n"
            f"for file in {shlex.quote(remote_dir + '/' + job_id + '.err')} "
            f"{shlex.quote(remote_dir + '/' + job_id + '.out')}; do\n"
            "  if [ -f \"$file\" ]; then printf '%s\\n' \"== $file ==\"; "
            f"tail -n {int(lines)} -- \"$file\"; fi\n"
            "done\n"
        )
        return output[-4000:]
