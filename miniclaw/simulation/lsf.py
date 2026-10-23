"""College HPC LSF adapter for LAMMPS; enabled only by explicit target config."""
from __future__ import annotations

import re
import shlex
from typing import Any

from .remote import JOB_ID, SIMPLE, RemoteError, SshTarget, positive


class LsfTarget(SshTarget):
    script_name = "job.lsf"

    def __init__(self, name: str, raw: dict[str, Any]):
        if raw.get("scheduler") != "lsf" or raw.get("engine") != "lammps":
            raise ValueError("LSF target must configure scheduler: lsf and engine: lammps")
        super().__init__(name, raw)
        self.engine = "lammps"
        self.queue = str(raw.get("queue", ""))
        if not SIMPLE.fullmatch(self.queue):
            raise ValueError("LSF queue must use a simple name")
        self.cores = positive(raw.get("cores", 1), "cores")
        self.mpi_launcher = str(raw.get("mpi_launcher", "mpirun"))
        if not SIMPLE.fullmatch(self.mpi_launcher):
            raise ValueError("mpi_launcher must use a simple name")

    def render_script(self, *, run_id: str, entry: str) -> str:
        directory = self.remote_dir(run_id)
        lines = [
            "#!/bin/bash",
            f"#BSUB -J mc-{run_id}",
            f"#BSUB -q {self.queue}",
            f"#BSUB -n {self.cores}",
            f"#BSUB -R \"span[ptile={self.cores}]\"",
            "#BSUB -o %J.out",
            "#BSUB -e %J.err",
            "",
            "set -euo pipefail",
            f"cd {shlex.quote(directory)}",
        ]
        if self.env_script:
            lines.append(f"source {shlex.quote(self.env_script)}")
        lines += [
            "export OMP_NUM_THREADS=1",
            f"{self.mpi_launcher} -np {self.cores} {shlex.quote(self.executable)} -in {shlex.quote(entry)}",
            "",
        ]
        return "\n".join(lines)

    def submit(self, remote_dir: str) -> str:
        output = self._ssh(
            "set -eu\n"
            f"cd {shlex.quote(remote_dir)}\n"
            "if [ -s .miniclaw-job-id ]; then cat .miniclaw-job-id; exit 0; fi\n"
            "result=$(bsub < job.lsf)\n"
            "job_id=$(printf '%s\\n' \"$result\" | sed -n 's/.*Job <\\([0-9][0-9]*\\)>.*/\\1/p' | head -n 1)\n"
            "test -n \"$job_id\"\n"
            "printf '%s\\n' \"$job_id\" > .miniclaw-job-id\n"
            "printf '%s\\n' \"$job_id\"\n"
        )
        job_id = output.splitlines()[-1].strip() if output else ""
        if not JOB_ID.fullmatch(job_id):
            raise RemoteError(f"bsub did not return a numeric job ID: {output[-200:]}")
        return job_id

    def recover_job_id(self, remote_dir: str, run_id: str, created_at: str) -> str | None:
        output = self._ssh(
            "set -u\n"
            f"if [ -s {shlex.quote(remote_dir + '/.miniclaw-job-id')} ]; then "
            f"cat {shlex.quote(remote_dir + '/.miniclaw-job-id')}; exit 0; fi\n"
            f"bjobs -a -J {shlex.quote('mc-' + run_id)} -o jobid 2>/dev/null || true\n"
        )
        for line in output.splitlines():
            candidate = line.strip()
            if JOB_ID.fullmatch(candidate):
                return candidate
        return None

    def status(self, job_id: str, remote_dir: str | None = None) -> dict[str, str]:
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("invalid LSF job ID")
        output = self._ssh(
            "set -u\n"
            f"bjobs -a -noheader -o \"stat exit_code delimiter='|'\" {job_id} 2>/dev/null || true\n"
        )
        line = output.splitlines()[-1].strip() if output else ""
        match = re.fullmatch(r"([A-Z]+)\s*\|\s*([0-9]+|-)?", line)
        if match:
            return {"source": "bjobs", "scheduler_state": match.group(1),
                    "exit_code": match.group(2) or ""}
        history = self._ssh(f"bacct -l {job_id} 2>/dev/null || true\n", timeout=60)
        if re.search(r"Status <DONE>|Completed <done>", history, re.IGNORECASE):
            return {"source": "bacct", "scheduler_state": "DONE", "exit_code": "0"}
        if re.search(r"Status <EXIT>|Completed <exit>", history, re.IGNORECASE):
            code = re.search(r"Exited with exit code ([0-9]+)", history)
            return {"source": "bacct", "scheduler_state": "EXIT",
                    "exit_code": code.group(1) if code else ""}
        return {"source": "lsf", "scheduler_state": "UNKNOWN",
                "reason": "job not found in bjobs or bacct history"}
