"""Prepare a GPUMD loading stage from the final NPT restart, without running MD.

Example:
    python prepare_tension.py --restart relax/restart.xyz --axes xy \
        --strain 0.30 --steps 100000 --output load

Only axis-aligned orthorhombic, fully periodic extended-XYZ restarts are supported.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path


def header_value(header: str, name: str) -> str | None:
    pattern = rf'\b{re.escape(name)}\s*=\s*(?:"([^"]*)"|(\S+))'
    match = re.search(pattern, header, re.IGNORECASE)
    if not match:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def read_reference(restart: Path) -> tuple[int, list[float]]:
    """Read reference cell and reject unsupported geometry or missing velocities."""
    with restart.open(encoding="utf-8-sig") as handle:
        first = handle.readline()
        header = handle.readline()
        try:
            count = int(first.strip())
        except ValueError as exc:
            raise ValueError("restart first line must contain the atom count") from exc
        if count < 1:
            raise ValueError("restart atom count must be positive")
        if sum(1 for line in handle if line.strip()) != count:
            raise ValueError("restart must contain exactly one complete XYZ frame")

    raw_lattice = header_value(header, "lattice")
    if raw_lattice is None:
        raise ValueError("restart is missing the lattice header")
    try:
        lattice = [float(value) for value in raw_lattice.split()]
    except ValueError as exc:
        raise ValueError("lattice must contain 9 real values") from exc
    if len(lattice) != 9 or not all(math.isfinite(value) for value in lattice):
        raise ValueError("lattice must contain 9 finite real values")
    lengths = [lattice[0], lattice[4], lattice[8]]
    if any(value <= 0 for value in lengths):
        raise ValueError("axis-aligned box lengths must be positive")
    tolerance = max(lengths) * 1e-10
    if any(abs(lattice[index]) > tolerance for index in (1, 2, 3, 5, 6, 7)):
        raise ValueError("only axis-aligned orthorhombic boxes are supported")

    pbc = header_value(header, "pbc")
    if pbc is not None and [flag.upper() for flag in pbc.split()] != ["T", "T", "T"]:
        raise ValueError('this example requires pbc="T T T"')
    properties = (header_value(header, "properties") or "").lower().split(":")
    if len(properties) % 3:
        raise ValueError("invalid properties header")
    fields = {tuple(properties[index:index + 3]) for index in range(0, len(properties), 3)}
    required = {("species", "s", "1"), ("pos", "r", "3"), ("vel", "r", "3")}
    if not required.issubset(fields):
        raise ValueError("a continuation restart must include species:S:1, pos:R:3 and vel:R:3")
    return count, lengths


def prepare(
    restart: Path,
    potential: Path,
    output: Path,
    axes: str,
    strain: float,
    steps: int,
    temperature: float,
    time_step: float,
) -> dict:
    if axes not in {"x", "xy"}:
        raise ValueError("axes must be x or xy")
    for name, value in (("strain", strain), ("temperature", temperature), ("time-step", time_step)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if steps < 1:
        raise ValueError("steps must be positive")
    restart = restart.resolve(strict=True)
    potential = potential.resolve(strict=True)
    if not restart.is_file() or not potential.is_file():
        raise ValueError("restart and potential must be files")
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("output must be a new or empty directory; existing files will not be overwritten")
    count, lengths = read_reference(restart)
    dx = strain * lengths[0] / steps
    dy = strain * lengths[1] / steps if axes == "xy" else 0.0
    target_lengths: list[float | None] = [lengths[0] * (1 + strain), None, None]
    if axes == "xy":
        target_lengths[1] = lengths[1] * (1 + strain)
    template_name = "uniaxial-tension" if axes == "x" else "biaxial-tension"
    template_path = Path(__file__).resolve().parent / template_name / "load.run.in.template"
    values = {
        "LX0": format(lengths[0], ".16g"),
        "LY0": format(lengths[1], ".16g"),
        "STRAIN": format(strain, ".16g"),
        "DX": format(dx, ".16g"),
        "DY": format(dy, ".16g"),
        "STEPS": steps,
        "TEMPERATURE": format(temperature, ".16g"),
        "TIME_STEP": format(time_step, ".16g"),
        "THERMO_INTERVAL": math.gcd(100, steps),
        "TRAJECTORY_INTERVAL": math.gcd(1000, steps),
    }
    run_text = template_path.read_text(encoding="utf-8").format_map(values)
    reference = {
        "gpumd_documentation": "5.5",
        "example": template_name,
        "reference_state": "supplied final NPT restart; equilibration is not evaluated by this helper",
        "source_restart": str(restart),
        "source_potential": str(potential),
        "atom_count": count,
        "reference_lengths_angstrom": dict(zip(("x", "y", "z"), lengths)),
        "engineering_strain": {axis: strain for axis in axes},
        "steps": steps,
        "time_step_fs": time_step,
        "temperature_K": temperature,
        "increments_angstrom_per_step": {"x": dx, "y": dy, "z": 0.0},
        "target_lengths_angstrom": dict(zip(("x", "y", "z"), target_lengths)),
        "engineering_strain_rate_per_s": strain / (steps * time_step * 1e-15),
        "transverse_condition": "zero external pressure on non-deformed directions; their final lengths are not prescribed",
    }
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(restart, output / "model.xyz")
    shutil.copyfile(potential, output / "nep.txt")
    (output / "run.in").write_text(run_text, encoding="utf-8", newline="\n")
    (output / "tension_reference.json").write_text(
        json.dumps(reference, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return reference


def main() -> None:
    # Predictable JSON encoding also when Windows pipes stdout through another process.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", type=Path, required=True, help="final NPT restart.xyz")
    parser.add_argument("--potential", type=Path, help="defaults to nep.txt beside the restart")
    parser.add_argument("--output", type=Path, required=True, help="new or empty loading directory")
    parser.add_argument("--axes", choices=("x", "xy"), required=True)
    parser.add_argument("--strain", type=float, default=0.30, help="engineering strain on each enabled axis")
    parser.add_argument("--steps", type=int, default=100000)
    parser.add_argument("--temperature", type=float, default=300)
    parser.add_argument("--time-step", type=float, default=1, help="fs")
    args = parser.parse_args()
    try:
        reference = prepare(
            restart=args.restart,
            potential=args.potential if args.potential is not None else args.restart.parent / "nep.txt",
            output=args.output,
            axes=args.axes,
            strain=args.strain,
            steps=args.steps,
            temperature=args.temperature,
            time_step=args.time_step,
        )
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"Cannot prepare loading stage: {exc}\n")
    print(json.dumps({"prepared": str(args.output.resolve()), **reference}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
