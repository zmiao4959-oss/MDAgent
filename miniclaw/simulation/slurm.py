"""One bounded SSH/Slurm adapter; no LLM-generated remote commands."""
from __future__ import annotations

import re
import shlex
from typing import Any

from .remote import JOB_ID, SIMPLE, RemoteError, SshTarget, positive


class SlurmTarget(SshTarget):
    script_name = "job.slurm"

    def __init__(self, name: str, raw: dict[str, Any]):
        if not isinstance(raw, dict) or raw.get("scheduler") != "slurm":
            raise ValueError(f"target '{name}' is not a configured Slurm target")
        super().__init__(name, raw)
        self.engine = str(raw.get("engine", "lammps"))
        if self.engine not in {"lammps", "gpumd"}:
            raise ValueError("Slurm target engine must be lammps or gpumd")
        self.partition = str(raw.get("partition", ""))
        self.mpi = str(raw.get("mpi", "" if self.engine == "gpumd" else "pmix_v3"))
        self.walltime = str(raw.get("walltime", "24:00:00"))
        if not SIMPLE.fullmatch(self.partition) or (self.mpi and not SIMPLE.fullmatch(self.mpi)):
            raise ValueError("partition and mpi must use simple names")
        if not re.fullmatch(r"[0-9]{1,3}:[0-9]{2}:[0-9]{2}", self.walltime):
            raise ValueError("walltime must use HH:MM:SS")
        self.nodes = positive(raw.get("nodes", 1), "nodes")
        self.ntasks_per_node = positive(raw.get("ntasks_per_node", 4), "ntasks_per_node")
        self.cpus_per_task = positive(raw.get("cpus_per_task", 8), "cpus_per_task")
        self.gpus = positive(raw.get("gpus", 4), "gpus", minimum=0)
        self.launcher = raw.get("launcher", "srun")
        if self.launcher not in ("srun", "direct"):
            raise ValueError("Slurm launcher must be srun or direct")
        if self.launcher == "direct" and (self.engine != "gpumd" or self.nodes != 1
                                          or self.ntasks_per_node != 1 or self.mpi):
            raise ValueError("direct Slurm launch requires GPUMD with one node, one task, and no MPI")

    def render_script(self, *, run_id: str, entry: str) -> str:
        directory = self.remote_dir(run_id)
        job_name = f"mc-{run_id}"
        task_request = (
            "--ntasks=1" if self.launcher == "direct"
            else f"--ntasks-per-node={self.ntasks_per_node}"
        )
        lines = [
            "#!/bin/bash",
            f"#SBATCH --job-name={job_name}",
            f"#SBATCH --partition={self.partition}",
            f"#SBATCH --nodes={self.nodes}",
            f"#SBATCH {task_request}",
            f"#SBATCH --cpus-per-task={self.cpus_per_task}",
            f"#SBATCH --time={self.walltime}",
            "#SBATCH --output=%j.out",
            "#SBATCH --error=%j.err",
        ]
        if self.gpus:
            lines.append(f"#SBATCH --gres=gpu:{self.gpus}")
        lines += ["", "set -euo pipefail", f"cd {shlex.quote(directory)}"]
        if self.env_script:
            lines.append(f"source {shlex.quote(self.env_script)}")
        lines.append("export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK")
        srun = "srun" + (f" --mpi={self.mpi}" if self.mpi else "")
        if self.engine == "gpumd":
            if entry != "run.in":
                raise ValueError("GPUMD entry must be run.in at the project root")
            executable = shlex.quote(self.executable)
            lines.append(executable if self.launcher == "direct" else f"{srun} {executable}")
        else:
            lines.append(f"{srun} {shlex.quote(self.executable)} -i {shlex.quote(entry)}")
        lines.append("")
        return "\n".join(lines)

    def submit(self, remote_dir: str) -> str:
        """A remote marker makes ordinary retries idempotent."""
        qdir = shlex.quote(remote_dir)
        output = self._ssh(
            "set -eu\n"
            f"cd {qdir}\n"
            "if [ -s .miniclaw-job-id ]; then cat .miniclaw-job-id; exit 0; fi\n"
            "job_id=$(sbatch --parsable job.slurm)\n"
            "printf '%s\\n' \"$job_id\" > .miniclaw-job-id\n"
            "printf '%s\\n' \"$job_id\"\n"
        )
        job_id = output.splitlines()[-1].split(";", 1)[0].strip() if output else ""
        if not JOB_ID.fullmatch(job_id):
            raise RemoteError(f"sbatch did not return a numeric job ID: {output[-200:]}")
        return job_id

    def recover_job_id(self, remote_dir: str, run_id: str, created_at: str) -> str | None:
        """After a broken submit connection, look up the unique job name; never resubmit."""
        job_name = f"mc-{run_id}"
        start_date = created_at[:10]
        output = self._ssh(
            "set -u\n"
            f"if [ -s {shlex.quote(remote_dir + '/.miniclaw-job-id')} ]; then "
            f"cat {shlex.quote(remote_dir + '/.miniclaw-job-id')}; exit 0; fi\n"
            f"squeue -h -u \"$USER\" -n {shlex.quote(job_name)} -o '%i' 2>/dev/null | head -n 1\n"
            f"sacct -n -X -P -S {shlex.quote(start_date)} --name={shlex.quote(job_name)} "
            "-o JobIDRaw 2>/dev/null | head -n 1\n"
        )
        for line in output.splitlines():
            candidate = line.strip().split(";", 1)[0]
            if JOB_ID.fullmatch(candidate):
                return candidate
        return None

    def status(self, job_id: str, remote_dir: str | None = None) -> dict[str, str]:
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("invalid Slurm job ID")
        output = self._ssh(
            "set -u\n"
            f"queued=$(squeue -h -j {job_id} -o '%T|%R' 2>/dev/null | head -n 1)\n"
            "if [ -n \"$queued\" ]; then printf 'QUEUE|%s\\n' \"$queued\"; exit 0; fi\n"
            f"account=$(sacct -n -X -P -j {job_id} -o JobIDRaw,State,ExitCode 2>/dev/null "
            f"| awk -F '|' '$1==\"{job_id}\" {{print; exit}}')\n"
            "if [ -n \"$account\" ]; then printf 'ACCOUNT|%s\\n' \"$account\"; "
            "else printf 'MISSING\\n'; fi\n"
        )
        line = output.splitlines()[-1] if output else "MISSING"
        if line.startswith("QUEUE|"):
            parts = line.split("|", 2)
            return {"source": "squeue", "scheduler_state": parts[1], "reason": parts[2] if len(parts) > 2 else ""}
        if line.startswith("ACCOUNT|"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                return {"source": "sacct", "scheduler_state": parts[2], "exit_code": parts[3]}
        return {"source": "slurm", "scheduler_state": "UNKNOWN", "reason": "job not found in queue or accounting"}

