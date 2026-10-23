"""Detached local job wrapper that leaves a durable completion marker."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    config_path = Path(sys.argv[1]).resolve()
    workdir = config_path.parent
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    pid = os.getpid()
    (workdir / ".miniclaw-job-id").write_text(str(pid), encoding="utf-8")
    marker = workdir / ".miniclaw-exit.json"
    temporary = workdir / ".miniclaw-exit.tmp"
    result = {"exit_code": -1, "error": "worker did not start the simulation"}
    try:
        with (workdir / f"{pid}.out").open("wb") as stdout, (workdir / f"{pid}.err").open("wb") as stderr:
            completed = subprocess.run(settings["argv"], cwd=settings.get("working_dir", workdir), stdout=stdout, stderr=stderr, check=False)
        result = {"exit_code": completed.returncode, "error": ""}
    except Exception as exc:
        result = {"exit_code": -1, "error": str(exc)}
    finally:
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(marker)


if __name__ == "__main__":
    main()
