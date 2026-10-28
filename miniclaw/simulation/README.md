# Simulation runner

The agent chooses a configured target with `list_simulation_backends`.
Local runs use the peer tools `run_gpumd(input_file)` and
`run_lammps(input_file)`. Pass only the input script path, such as `trial/run.in`
or `in.cuni`. The script's parent directory is the working directory; existing
models, potentials and included files stay in place. No manifest is required.
Remote runs use `prepare_simulation` → `submit_simulation`. Both return a
`run_id` for later `get_simulation` queries. Targets can be switched on or off
independently in `simulation.targets`:

| Target | Engine | Backend | Current configuration |
| --- | --- | --- | --- |
| `local-lammps` | LAMMPS | Detached local worker | Enabled |
| `local-gpumd` | GPUMD | Detached WSL worker | Enabled |
| `hpc` | LAMMPS | SSH + LSF | Disabled until paths and queue are confirmed |
| `sugon` | GPUMD | SSH + Slurm | Disabled until GPUMD path and SSH access are confirmed |

The runner executes the provided inputs without shortening scripts, estimating
duration, or asking for another confirmation after a run request. Script
generators own their validation and can use the same execution tools on their
prepared short scripts. Local runs use the detached worker; they
do not rely on a long tool call. It writes its exit marker and logs inside the
log directory under `<script-directory>/.miniclaw/simulations/<run_id>/input`.
The returned `run_dir` is the script directory containing simulation outputs;
`log_dir` contains launcher logs and status markers. Both remote schedulers stage frozen inputs and
use a unique job name and job-ID marker to avoid blind resubmission after a
connection failure.

Local GPUMD admission uses one persistent FIFO queue shared by every project,
agent and local GPUMD target in the same MiniClaw home. Configure
`simulation.local_gpumd_max_concurrent` (positive integer, default **1**) to
change the limit. Local LAMMPS and remote scheduler queues are unaffected.
Submitting many runs creates waiting workers, not many simultaneous GPUMD
processes. `PENDING` reports `queue_position`, `max_concurrent` and a reason;
`job_id` identifies the worker and does not prove GPUMD has started. Workers
automatically start the next queued simulation without agent polling and survive
MiniClaw restarts. Queue metadata is stored in
`~/.miniclaw/simulations/local-gpu-queue.sqlite3`. Tracked input hashes are
rechecked before starting a queued run; changed inputs fail that run.

Previously running managed GPUMD jobs count toward admission while their worker
or simulation child is alive; an upgrade never stops or relaunches them. Worker
identity is checked by command line (and creation time for queued workers), not
PID alone. Workers persist their child's PID and creation time. If a worker
disappears without an exit marker, recovery checks the native child and, for WSL,
the configured executable and working directory in Linux `/proc`. Confirmed
absence releases the slot as `LOST` (public status `FAILED`, exit code unknown).
Inconclusive probes retain the slot as `UNKNOWN`; never blindly resubmit them.
Pre-upgrade records are checked too, with confirmed losses cached in the queue.
Pre-upgrade prepared GPUMD launchers must be prepared again before submission.
Only managed GPUMD jobs in this MiniClaw home are counted; manually launched
GPU programs are outside this queue. Host reboot recovery of simulations is not
provided; queued workers survive app restarts, not operating-system restarts.

For remote preparation, place `simulation.yaml` in the project directory. List every input file,
including potential files and files brought in by `include`. Only regular files
inside that project are accepted. GPUMD uses `entry: run.in` at the project
root; LAMMPS uses an input script such as `in.cuni`.

```yaml
engine: gpumd
entry: run.in
inputs:
  - run.in
  - model.xyz
  - nep.txt
expected_outputs:
  - thermo.out
```

`prepare_simulation` freezes the inputs and previews the launcher without
starting a job. `submit_simulation` starts or submits exactly that snapshot,
waits `simulation.submission_check_delay_sec` (default 10 seconds, range 0–60),
and queries the state once. A job ID alone is stored as `SUBMITTED`; observed
queue/running states confirm acceptance, while an early exit returns its actual
result and logs. A lost connection or missing job yields `UNKNOWN`, preserving
the run ID for reconciliation without a second submission.

There is no background monitor or automatic notification. During an active task,
`get_simulation(wait_timeout=...)` can wait internally for an existing run without
additional LLM polling turns; the default queries once. SQLite records in
`~/.miniclaw/simulations/runs.sqlite3` and local exit markers survive MiniClaw
restarts. `SUCCEEDED` requires the scheduler or worker to
record a zero exit status and any declared expected output files to exist;
it does not validate the scientific result. SSH authentication remains in the
user's SSH configuration. New scheduler modules register through
`backends.register_backend`.

