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

