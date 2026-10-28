"""Detached local job wrapper that leaves a durable completion marker."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

# The worker is launched by absolute script path, outside the package cwd.
if __package__:
    from .local_queue import LocalGpuQueue, validate_queued_inputs
else:
    from local_queue import LocalGpuQueue, validate_queued_inputs


def main() -> None:
    config_path = Path(sys.argv[1]).resolve()
    workdir = config_path.parent
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    pid = os.getpid()
    marker = workdir / ".miniclaw-exit.json"
    temporary = workdir / ".miniclaw-exit.tmp"
    result = {"exit_code": -1, "error": "worker did not start the simulation"}
    queue = None
    attached = False
    try:
        queue_settings = settings.get("gpu_queue")
        if queue_settings:
            queue = LocalGpuQueue(queue_settings["database"])
            queue.attach(queue_settings["run_id"], pid)
            attached = True
        (workdir / ".miniclaw-job-id").write_text(str(pid), encoding="utf-8")
        if queue_settings:
            while not queue.acquire(queue_settings["run_id"], queue_settings["runs_database"]):
                time.sleep(1)
            validate_queued_inputs(queue_settings["run_id"], queue_settings["runs_database"])
        with (workdir / f"{pid}.out").open("wb") as stdout, (workdir / f"{pid}.err").open("wb") as stderr:
            with subprocess.Popen(settings["argv"], cwd=settings.get("working_dir", workdir),
                                  stdout=stdout, stderr=stderr) as child:
                try:
                    birth = psutil.Process(child.pid).create_time()
                except psutil.NoSuchProcess:
                    birth = None
                child_tmp = workdir / ".miniclaw-child.tmp"
                child_tmp.write_text(json.dumps({"pid": child.pid, "birth": birth}), encoding="utf-8")
                child_tmp.replace(workdir / ".miniclaw-child.json")
                code = child.wait()
        result = {"exit_code": code, "error": ""}
    except Exception as exc:
        result = {"exit_code": -1, "error": str(exc)}
    finally:
        if queue is None or attached:
            temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            temporary.replace(marker)
            if queue is not None:
                queue.finish(queue_settings["run_id"])


if __name__ == "__main__":
    main()
