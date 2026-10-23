"""Exact scalar consistency calculations for proposed research parameters.

Accepts scalar command-line parameters or a JSON request and prints a JSON result.
Never starts MD or writes assets.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
import json
import math
from pathlib import Path
import sys


def positive(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number")
    try:
        number = Decimal(str(value))
        finite_float = math.isfinite(float(number))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise ValueError(f"{name} must be a finite positive number") from None
    if not number.is_finite() or not finite_float or number <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return number


def integer(value, name):
    result = positive(value, name)
    if result != result.to_integral_value():
        raise ValueError(f"{name} must be a positive integer")
    return int(result)


def num(value):
    return float(value)


def output_interval(per_step, total_steps, resolution, dt, max_ps=None):
    limits = [total_steps]
    if resolution is not None:
        limits.append(int((resolution / per_step).to_integral_value(rounding=ROUND_FLOOR)))
    if max_ps is not None:
        limits.append(int((max_ps * 1000 / dt).to_integral_value(rounding=ROUND_FLOOR)))
    raw = min(limits)
    interval = max(1, raw)
    return {
        "interval_steps": interval,
        "time_ps": num(Decimal(interval) * dt / 1000),
        "increment": num(Decimal(interval) * per_step),
        "resolution_satisfied": raw >= 1,
    }


def calculate(request):
    if not isinstance(request, dict):
        raise ValueError("Request must be a JSON object")
    with localcontext() as context:
        context.prec = 50
        mode = request.get("mode")
        dt = positive(request.get("dt_fs"), "dt_fs")
        if mode == "engineering_tension":
            if request.get("strain_definition", "engineering") != "engineering":
                raise ValueError("Constant true-strain-rate loading requires an exponential schedule; this calculator does not silently substitute engineering strain")
            strain = positive(request.get("strain"), "strain")
            axes = request.get("axes", "x")
            if not isinstance(axes, str) or not axes or any(a not in "xyz" for a in axes) or len(set(axes)) != len(axes):
                raise ValueError("axes must contain unique x/y/z letters")
            spans = []
            if request.get("rate_s_inv") is not None:
                rate = positive(request["rate_s_inv"], "rate_s_inv")
                spans.append(("rate_s_inv", strain / rate * Decimal("1e15")))
            if request.get("duration_ps") is not None:
                spans.append(("duration_ps", positive(request["duration_ps"], "duration_ps") * 1000))
            steps_input = request.get("steps")
            if steps_input is not None:
                steps_input = integer(steps_input, "steps")
            if not spans and steps_input is None:
                raise ValueError("Provide rate_s_inv, duration_ps, or steps")
            if len(spans) == 2 and abs(spans[0][1] - spans[1][1]) > dt:
                raise ValueError("Conflicting rate and duration; do not alter user constraints")
            ideal_fs = spans[0][1] if spans else Decimal(steps_input) * dt
            calculated_steps = int((ideal_fs / dt).to_integral_value(rounding=ROUND_CEILING))
            steps = steps_input if steps_input is not None else calculated_steps
            actual_fs = Decimal(steps) * dt
            for label, target_fs in spans:
                difference = actual_fs - target_fs
                if difference < 0 or difference >= dt:
                    raise ValueError(f"Conflicting steps and {label}; only upward rounding of less than one timestep is allowed")
            actual_rate = strain / actual_fs * Decimal("1e15")
            lengths = request.get("reference_lengths_A") or {}
            if not isinstance(lengths, dict):
                raise ValueError("reference_lengths_A must be an object")
            increments, targets, missing = {}, {}, []
            for axis in axes:
                if axis not in lengths:
                    missing.append(axis)
                    continue
                length = positive(lengths[axis], f"reference_lengths_A.{axis}")
                increments[axis] = num(strain * length / Decimal(steps))
                targets[axis] = num(length * (1 + strain))
            result = {
                "status": "ok", "mode": mode, "strain_definition": "engineering",
                "strain": num(strain), "axes": axes, "dt_fs": num(dt),
                "steps": steps, "duration_fs": num(actual_fs),
                "duration_ps": num(actual_fs / 1000), "duration_ns": num(actual_fs / Decimal("1e6")),
                "actual_rate_s_inv": num(actual_rate),
                "rounding_added_fs": num(actual_fs - ideal_fs),
                "increment_A_per_step": increments, "target_lengths_A": targets,
                "reference_status": "RUNTIME" if missing else "BOUND",
                "missing_reference_axes": missing,
                "increment_formula": "strain * formal_reference_length_A / steps",
            }
            if request.get("dump_strain") is not None or request.get("dump_max_ps") is not None:
                resolution = positive(request["dump_strain"], "dump_strain") if request.get("dump_strain") is not None else None
                result["output"] = output_interval(strain / Decimal(steps), steps, resolution, dt,
                    positive(request["dump_max_ps"], "dump_max_ps") if request.get("dump_max_ps") is not None else None)
            return result
        if mode == "temperature_ramp":
            start = positive(request.get("start_K"), "start_K")
            end = positive(request.get("end_K"), "end_K")
            difference = abs(end - start)
            if not difference:
                raise ValueError("Temperature endpoints must differ; use a fixed-temperature plan for equal endpoints")
            rate = positive(request.get("rate_K_s"), "rate_K_s")
            ideal_fs = difference / rate * Decimal("1e15")
            steps = int((ideal_fs / dt).to_integral_value(rounding=ROUND_CEILING))
            actual_fs = Decimal(steps) * dt
            result = {
                "status": "ok", "mode": mode, "start_K": num(start), "end_K": num(end),
                "dt_fs": num(dt), "steps": steps, "duration_fs": num(actual_fs),
                "duration_ps": num(actual_fs / 1000), "duration_ns": num(actual_fs / Decimal("1e6")),
                "actual_rate_K_s": num(difference / actual_fs * Decimal("1e15")),
                "rounding_added_fs": num(actual_fs - ideal_fs),
            }
            if request.get("dump_delta_K") is not None or request.get("dump_max_ps") is not None:
                result["output"] = output_interval(difference / Decimal(steps), steps,
                    positive(request["dump_delta_K"], "dump_delta_K") if request.get("dump_delta_K") is not None else None,
                    dt, positive(request["dump_max_ps"], "dump_max_ps") if request.get("dump_max_ps") is not None else None)
            return result
        if mode == "fixed_state":
            duration = positive(request["duration_ps"], "duration_ps") if request.get("duration_ps") is not None else None
            steps_input = integer(request["steps"], "steps") if request.get("steps") is not None else None
            if duration is None and steps_input is None:
                raise ValueError("Provide duration_ps or steps")
            ideal_fs = duration * 1000 if duration is not None else Decimal(steps_input) * dt
            steps = steps_input if steps_input is not None else int((ideal_fs / dt).to_integral_value(rounding=ROUND_CEILING))
            actual_fs = Decimal(steps) * dt
            if duration is not None and (actual_fs < ideal_fs or actual_fs - ideal_fs >= dt):
                raise ValueError("Conflicting steps and duration_ps; only upward rounding of less than one timestep is allowed")
            result = {
                "status": "ok", "mode": mode, "dt_fs": num(dt), "steps": steps,
                "duration_fs": num(actual_fs), "duration_ps": num(actual_fs / 1000),
                "duration_ns": num(actual_fs / Decimal("1e6")),
                "rounding_added_fs": num(actual_fs - ideal_fs),
            }
            for symbol in ("t", "p"):
                key = f"tau_{symbol}_fs"
                if request.get(key) is not None:
                    tau = positive(request[key], key)
                    result[key] = num(tau)
                    result[f"tau_{symbol}_over_dt"] = num(tau / dt)
            return result
        raise ValueError("Supported modes: engineering_tension, temperature_ramp, fixed_state")


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep malformed command-line requests on the same JSON error contract."""

    def error(self, message):
        raise ValueError(message)


def argument_parser():
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, metavar="PATH", help="read the existing JSON request format instead of a scalar subcommand")
    commands = parser.add_subparsers(dest="command", metavar="{tension,ramp,state}")

    tension = commands.add_parser("tension", help="calculate constant engineering-strain-rate tension")
    tension.add_argument("--strain", required=True, help="positive final engineering strain (e.g. 0.1)")
    tension.add_argument("--rate", help="strain rate in s^-1; may be combined with consistent --steps/--duration-ps")
    tension.add_argument("--steps", help="positive integer timestep count")
    tension.add_argument("--duration-ps", help="requested duration in ps")
    tension.add_argument("--dt-fs", required=True, help="timestep in fs")
    tension.add_argument("--axes", default="x", help="unique loaded axes from x/y/z (default: x)")
    tension.add_argument("--length", action="append", default=[], metavar="AXIS=ANGSTROMS", help="formal reference length; repeat for each axis (e.g. --length x=100 --length y=120)")
    tension.add_argument("--strain-definition", default="engineering", help="strain definition (only engineering is supported; true is explicitly rejected)")
    tension.add_argument("--dump-strain", help="maximum strain increment between outputs")
    tension.add_argument("--dump-max-ps", help="maximum ps between outputs")

    ramp = commands.add_parser("ramp", help="calculate a constant-rate temperature ramp")
    ramp.add_argument("--start-k", required=True, help="positive starting temperature in K")
    ramp.add_argument("--end-k", required=True, help="positive ending temperature in K")
    ramp.add_argument("--rate", required=True, help="temperature rate magnitude in K/s")
    ramp.add_argument("--dt-fs", required=True, help="timestep in fs")
    ramp.add_argument("--dump-delta-k", help="maximum temperature change between outputs in K")
    ramp.add_argument("--dump-max-ps", help="maximum ps between outputs")

    state = commands.add_parser("state", help="calculate fixed-state duration, steps, and optional tau/dt ratios")
    state.add_argument("--duration-ps", help="requested duration in ps; may be combined with consistent --steps")
    state.add_argument("--steps", help="positive integer timestep count")
    state.add_argument("--dt-fs", required=True, help="timestep in fs")
    state.add_argument("--tau-t-fs", help="positive supplied thermostat damping time in fs (no recommendation)")
    state.add_argument("--tau-p-fs", help="positive supplied barostat damping time in fs (no recommendation)")
    return parser


def scalar_request(args):
    """Map CLI spellings to the JSON contract without a float conversion."""
    if args.command == "tension":
        lengths = {}
        for item in args.length:
            axis, separator, value = item.partition("=")
            if not separator or axis not in ("x", "y", "z") or not value:
                raise ValueError("--length must be AXIS=ANGSTROMS with axis x, y, or z")
            if axis in lengths:
                raise ValueError(f"Duplicate reference length for axis {axis}; provide each --length once")
            positive(value, f"reference_lengths_A.{axis}")
            lengths[axis] = value
        return {
            "mode": "engineering_tension", "strain": args.strain,
            "rate_s_inv": args.rate, "steps": args.steps,
            "duration_ps": args.duration_ps, "dt_fs": args.dt_fs,
            "axes": args.axes, "reference_lengths_A": lengths,
            "strain_definition": args.strain_definition,
            "dump_strain": args.dump_strain, "dump_max_ps": args.dump_max_ps,
        }
    if args.command == "ramp":
        return {
            "mode": "temperature_ramp", "start_K": args.start_k,
            "end_K": args.end_k, "rate_K_s": args.rate, "dt_fs": args.dt_fs,
            "dump_delta_K": args.dump_delta_k, "dump_max_ps": args.dump_max_ps,
        }
    if args.command == "state":
        return {
            "mode": "fixed_state", "duration_ps": args.duration_ps,
            "steps": args.steps, "dt_fs": args.dt_fs,
            "tau_t_fs": args.tau_t_fs, "tau_p_fs": args.tau_p_fs,
        }
    raise ValueError("Provide --input PATH or a tension/ramp/state subcommand")


def main():
    try:
        args = argument_parser().parse_args()
        if args.input is not None:
            if args.command is not None:
                raise ValueError("--input cannot be combined with a scalar subcommand")
            request = json.loads(args.input.read_text(encoding="utf-8"))
        else:
            request = scalar_request(args)
        result = calculate(request)
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    except (ValueError, OSError, ArithmeticError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
