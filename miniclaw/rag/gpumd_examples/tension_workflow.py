"""Create a portable two-stage GPUMD tension project; never launch a simulation.

Create: python tension_workflow.py create --model model.xyz --potential nep.txt
    --output project --axes xy --strain .30 --load-steps 30000
Continue: python project/workflow.py prepare-load [--trial]

The loading stage remains pending until its own real NPT restart exists.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path


CONFIG = "tension_project.json"
MANIFEST = "prepared_files.json"
ASSETS = Path(__file__).resolve().parent


def _helper(root: Path = ASSETS):
    spec = importlib.util.spec_from_file_location("_gpumd_prepare_tension", root / "prepare_tension.py")
    if spec is None or spec.loader is None:
        raise ValueError("project is missing prepare_tension.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_parameters(parameters: dict) -> None:
    if parameters["axes"] not in {"x", "xy"}:
        raise ValueError("axes must be x or xy")
    for name in ("strain", "temperature", "time_step"):
        value = parameters[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    for name in ("load_steps", "relax_steps", "trial_load_steps", "trial_relax_steps"):
        value = parameters[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if parameters["trial_load_steps"] > parameters["load_steps"]:
        raise ValueError("trial_load_steps must not exceed load_steps")
    if parameters["trial_relax_steps"] > parameters["relax_steps"]:
        raise ValueError("trial_relax_steps must not exceed relax_steps")


def _validate_model(model: Path) -> None:
    """Validate the initial frame without requiring continuation velocities."""
    lines = model.read_text(encoding="utf-8-sig").splitlines()
    try:
        count = int(lines[0])
    except (IndexError, ValueError) as exc:
        raise ValueError("model must be a complete extended-XYZ frame") from exc
    if count < 1 or len(lines) < 2 or sum(bool(line.strip()) for line in lines[2:]) != count:
        raise ValueError("model must contain exactly one complete XYZ frame")
    header_value = _helper().header_value
    raw = header_value(lines[1], "lattice") or ""
    try:
        lattice = [float(value) for value in raw.split()]
    except ValueError as exc:
        raise ValueError("model lattice must contain 9 finite real values") from exc
    if len(lattice) != 9 or not all(math.isfinite(value) for value in lattice):
        raise ValueError("model lattice must contain 9 finite real values")
    lengths = [lattice[0], lattice[4], lattice[8]]
    if min(lengths) <= 0 or any(abs(lattice[index]) > max(lengths) * 1e-10 for index in (1, 2, 3, 5, 6, 7)):
        raise ValueError("model must have a positive axis-aligned orthorhombic box")
    pbc = header_value(lines[1], "pbc")
    if pbc is not None and pbc.upper().split() != ["T", "T", "T"]:
        raise ValueError('model requires pbc="T T T"')
    properties = (header_value(lines[1], "properties") or "").lower().split(":")
    fields = {tuple(properties[index:index + 3]) for index in range(0, len(properties), 3)}
    if len(properties) % 3 or not {("species", "s", "1"), ("pos", "r", "3")}.issubset(fields):
        raise ValueError("model must include species:S:1 and pos:R:3")


def _relax_input(parameters: dict, steps: int) -> str:
    temperature = format(parameters["temperature"], ".16g")
    time_step = format(parameters["time_step"], ".16g")
    return (
        "# NPT reference preparation; equilibration must be assessed separately.\n"
        "potential nep.txt\n"
        f"velocity {temperature} seed 20261017\n"
        f"time_step {time_step}\n\n"
        f"ensemble npt_scr {temperature} {temperature} 100 0 0 0 100 100 100 1000\n"
        f"dump_thermo {math.gcd(100, steps)}\n"
        f"dump_exyz {math.gcd(1000, steps)} 0\n"
        f"dump_restart {steps}\nrun {steps}\n"
    )


def create_project(
    model: Path,
    potential: Path,
    output: Path,
    axes: str = "xy",
    strain: float = 0.30,
    load_steps: int = 30000,
    relax_steps: int = 100000,
    temperature: float = 300,
    time_step: float = 1,
    trial_relax_steps: int = 200,
    trial_load_steps: int = 200,
) -> dict:
    """Write inputs and a self-contained continuation entry point, without MD."""
    parameters = dict(
        axes=axes, strain=strain, load_steps=load_steps, relax_steps=relax_steps,
        temperature=temperature, time_step=time_step,
        trial_relax_steps=min(trial_relax_steps, relax_steps),
        trial_load_steps=min(trial_load_steps, load_steps),
    )
    _validate_parameters(parameters)
    model, potential, output = model.resolve(strict=True), potential.resolve(strict=True), output.resolve()
    if not model.is_file() or not potential.is_file() or potential.stat().st_size == 0:
        raise ValueError("model and nonempty potential must be files")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("output must be a new or empty directory; existing files will not be overwritten")
    _validate_model(model)
    template_dir = "uniaxial-tension" if axes == "x" else "biaxial-tension"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        shutil.copyfile(Path(__file__), temporary / "workflow.py")
        shutil.copyfile(ASSETS / "prepare_tension.py", temporary / "prepare_tension.py")
        (temporary / template_dir).mkdir()
        shutil.copyfile(ASSETS / template_dir / "load.run.in.template", temporary / template_dir / "load.run.in.template")
        _json(temporary / CONFIG, {"schema": 1, "gpumd_documentation": "5.5", **parameters})
        for relative, steps in (("relax", relax_steps), ("trial/relax", parameters["trial_relax_steps"])):
            stage = temporary / relative
            stage.mkdir(parents=True)
            shutil.copyfile(model, stage / "model.xyz")
            shutil.copyfile(potential, stage / "nep.txt")
            (stage / "run.in").write_text(_relax_input(parameters, steps), encoding="utf-8", newline="\n")
        (temporary / "README.md").write_text(
            "# GPUMD two-stage tension project\n\n"
            "File preparation is complete. No simulation was started.\n"
            f"Settings: {axes} loading, engineering strain {strain:g}, {load_steps} load steps; "
            f"NPT {relax_steps} steps, {temperature:g} K, {time_step:g} fs.\n\n"
            "## Short workflow check\n\n"
            "1. Run GPUMD in trial/relax (or submit trial/relax/run.in).\n"
            "2. After its restart is available: python workflow.py prepare-load --trial\n"
            "3. Run the run_in path returned in JSON. Trial strain is shortened at the full loading rate.\n\n"
            "## Formal execution when requested\n\n"
            "1. Run GPUMD in relax (or submit relax/run.in), and assess the completed relaxation.\n"
            "2. Run: python workflow.py prepare-load\n"
            "3. Run GPUMD in the returned directory using its run.in.\n\n"
            "A missing restart returns pending and does not start MD. Trial references never populate formal load inputs.\n"
            "Changed references or settings select a new load directory; old results remain.\n"
            "This project includes its helper and templates and can be moved without the original Skill.\n"
            "Short checks validate initialization and data flow, not physical equilibration or potential accuracy.\n",
            encoding="utf-8",
        )
        if output.exists():
            output.rmdir()  # Only a verified empty output can be replaced.
        temporary.rename(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "status": "created", "project": str(output), "workflow": str(output / "workflow.py"),
        "relax_run_in": str(output / "relax/run.in"),
        "trial_relax_run_in": str(output / "trial/relax/run.in"),
        "loading_status": "pending", "next": "python workflow.py prepare-load [--trial]",
        "simulations_started": 0,
    }


def _verify_prepared(output: Path, fingerprint: str) -> dict:
    try:
        manifest = json.loads((output / MANIFEST).read_text(encoding="utf-8"))
        if manifest["fingerprint"] != fingerprint:
            raise ValueError("fingerprint mismatch")
        expected_names = {"model.xyz", "nep.txt", "run.in", "tension_reference.json"}
        if set(manifest["files"]) != expected_names:
            raise ValueError("managed file list mismatch")
        for name, digest in manifest["files"].items():
            if _digest((output / name).read_bytes()) != digest:
                raise ValueError(f"modified file: {name}")
        return json.loads((output / "tension_reference.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"refusing to reuse or overwrite modified loading directory {output}: {exc}") from exc


def prepare_load(project: Path = ASSETS, trial: bool = False) -> dict:
    """Prepare a unique load directory using the corresponding real restart."""
    project = project.resolve(strict=True)
    parameters = json.loads((project / CONFIG).read_text(encoding="utf-8"))
    if parameters.get("schema") != 1:
        raise ValueError("unsupported tension project schema")
    _validate_parameters(parameters)
    mode = "trial" if trial else "formal"
    base = project / "trial" if trial else project
    restart, potential = base / "relax/restart.xyz", base / "relax/nep.txt"
    if not restart.exists():
        return {
            "status": "pending", "mode": mode, "run_in": None,
            "expected_restart": str(restart),
            "reason": "run the corresponding NPT input separately before preparing loading",
            "simulations_started": 0,
        }
    helper = _helper(project)
    helper.read_reference(restart)
    source_hashes = {"restart": _digest(restart.read_bytes()), "potential": _digest(potential.read_bytes())}
    template_dir = "uniaxial-tension" if parameters["axes"] == "x" else "biaxial-tension"
    identity = {
        "schema": 1, "mode": mode, "parameters": parameters, **source_hashes,
        "helper": _digest((project / "prepare_tension.py").read_bytes()),
        "workflow": _digest((project / "workflow.py").read_bytes()),
        "template": _digest((project / template_dir / "load.run.in.template").read_bytes()),
    }
    fingerprint = _digest(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    output = base / "load" / fingerprint
    if output.exists():
        reference = _verify_prepared(output, fingerprint)
        reused = True
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=output.parent))
        try:
            reference = helper.prepare(
                restart, potential, temporary, parameters["axes"], parameters["strain"],
                parameters["load_steps"], parameters["temperature"], parameters["time_step"],
            )
            if (_digest((temporary / "model.xyz").read_bytes()) != source_hashes["restart"]
                    or _digest((temporary / "nep.txt").read_bytes()) != source_hashes["potential"]):
                raise ValueError("reference inputs changed during preparation; retry with stable files")
            reference.update(
                mode=mode, fingerprint=fingerprint,
                source_restart=restart.relative_to(project).as_posix(),
                source_potential=potential.relative_to(project).as_posix(),
                full_load_steps=parameters["load_steps"],
                requested_engineering_strain={axis: parameters["strain"] for axis in parameters["axes"]},
            )
            if trial:
                actual_steps = parameters["trial_load_steps"]
                actual_strain = parameters["strain"] * actual_steps / parameters["load_steps"]
                reference["steps"] = actual_steps
                reference["engineering_strain"] = {axis: actual_strain for axis in parameters["axes"]}
                reference["target_lengths_angstrom"] = {
                    axis: length * (1 + actual_strain) if axis in parameters["axes"] else None
                    for axis, length in reference["reference_lengths_angstrom"].items()
                }
                commands = {
                    "run": f"run {actual_steps}", "dump_restart": f"dump_restart {actual_steps}",
                    "dump_thermo": f"dump_thermo {math.gcd(100, actual_steps)}",
                    "dump_exyz": f"dump_exyz {math.gcd(1000, actual_steps)} 0",
                }
                lines = (temporary / "run.in").read_text(encoding="utf-8").splitlines()
                lines = [
                    commands.get(line.split()[0], line) if line.split() else line
                    for line in lines
                    if not (line.startswith("#") and ("engineering strain" in line or "Loading duration" in line))
                ]
                note = f"# Trial: {actual_steps} steps; engineering strain {actual_strain:.16g}; full-run loading rate preserved.\n"
                (temporary / "run.in").write_text(note + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            _json(temporary / "tension_reference.json", reference)
            _json(temporary / MANIFEST, {
                "fingerprint": fingerprint,
                "files": {name: _digest((temporary / name).read_bytes()) for name in (
                    "model.xyz", "nep.txt", "run.in", "tension_reference.json",
                )},
            })
            temporary.rename(output)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        reused = False
    return {
        "status": "prepared", "mode": mode, "run_in": str(output / "run.in"),
        "directory": str(output), "reference": reference,
        "fingerprint": fingerprint, "reused": reused, "simulations_started": 0,
    }


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="write a portable project without running MD")
    create.add_argument("--model", type=Path, required=True)
    create.add_argument("--potential", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--axes", choices=("x", "xy"), default="xy")
    create.add_argument("--strain", type=float, default=0.30)
    create.add_argument("--load-steps", type=int, default=30000)
    create.add_argument("--relax-steps", type=int, default=100000)
    create.add_argument("--temperature", type=float, default=300)
    create.add_argument("--time-step", type=float, default=1)
    create.add_argument("--trial-relax-steps", type=int, default=200)
    create.add_argument("--trial-load-steps", type=int, default=200)
    prepare = commands.add_parser("prepare-load", help="prepare from the real restart, or report pending")
    prepare.add_argument("--project", type=Path, default=ASSETS)
    prepare.add_argument("--trial", action="store_true")
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        result = create_project(**args) if command == "create" else prepare_load(**args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Cannot prepare tension project: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
