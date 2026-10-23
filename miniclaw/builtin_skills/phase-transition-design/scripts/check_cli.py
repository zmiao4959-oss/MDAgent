"""Exercise the real scalar CLI and its JSON/exit-code contract using stdlib only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


SCRIPT = Path(__file__).resolve().with_name("calculate_plan.py")
TENSION = "tension --strain .1 --rate 1e8 --dt-fs 1"
RAMP = "ramp --start-k 2500 --end-k 300 --rate 2e12 --dt-fs 1"


def ok(name, arguments, **expected):
    return {"name": name, "arguments": arguments.split(), "expected": expected}


def error(name, arguments, message):
    return {"name": name, "arguments": arguments.split(), "error_contains": message}


CASES = [
    ok("z formal reference", TENSION + " --axes z --length z=100",
       steps=1000000, duration_ps=1000.0, reference_status="BOUND",
       increment_A_per_step={"z": 0.00001}, target_lengths_A={"z": 110.0}),
    ok("xy distinct formal references", TENSION + " --axes xy --length x=100 --length y=120",
       reference_status="BOUND", increment_A_per_step={"x": 0.00001, "y": 0.000012},
       target_lengths_A={"x": 110.0, "y": 132.0}),
    ok("missing formal reference stays runtime", TENSION + " --axes z",
       reference_status="RUNTIME", missing_reference_axes=["z"], increment_A_per_step={}),
    ok("partial formal references stay runtime", TENSION + " --axes xy --length x=100",
       reference_status="RUNTIME", missing_reference_axes=["y"], increment_A_per_step={"x": 0.00001}),
    ok("steps without rate", "tension --strain .1 --steps 200 --dt-fs 1", steps=200, duration_ps=0.2),
    ok("duration without rate", "tension --strain .1 --duration-ps .25 --dt-fs 1", steps=250, duration_ps=0.25),
    ok("consistent simultaneous constraints", TENSION + " --steps 1000000 --duration-ps 1000", steps=1000000),
    ok("Decimal precision crosses a timestep", "tension --strain .100000000000000001 --rate 1e14 --dt-fs 1",
       steps=2, duration_fs=2.0),
    ok("strain dump resolution", TENSION + " --dump-strain .00002",
       output={"interval_steps": 200, "time_ps": 0.2, "increment": 0.00002, "resolution_satisfied": True}),
    ok("time-only tension dump constraint", TENSION + " --dump-max-ps .1",
       output={"interval_steps": 100, "time_ps": 0.1, "increment": 0.00001, "resolution_satisfied": True}),
    ok("tighter simultaneous tension dump constraint", TENSION + " --dump-strain .00002 --dump-max-ps .1",
       output={"interval_steps": 100, "time_ps": 0.1, "increment": 0.00001, "resolution_satisfied": True}),
    ok("unattainable strain resolution is explicit", TENSION + " --dump-strain .00000001",
       output={"interval_steps": 1, "time_ps": 0.001, "increment": 0.0000001, "resolution_satisfied": False}),
    ok("unattainable time resolution is explicit", TENSION + " --dump-max-ps .0001",
       output={"interval_steps": 1, "time_ps": 0.001, "increment": 0.0000001, "resolution_satisfied": False}),
    ok("cooling ramp with output resolution", RAMP + " --dump-delta-k 1 --dump-max-ps 1",
       steps=1100000, duration_ps=1100.0,
       output={"interval_steps": 500, "time_ps": 0.5, "increment": 1.0, "resolution_satisfied": True}),
    ok("heating ramp", "ramp --start-k 300 --end-k 2500 --rate 2e12 --dt-fs 1",
       steps=1100000, duration_ps=1100.0, actual_rate_K_s=2000000000000.0),
    ok("time-only ramp dump constraint", RAMP + " --dump-max-ps .1",
       output={"interval_steps": 100, "time_ps": 0.1, "increment": 0.2, "resolution_satisfied": True}),
    ok("unattainable temperature resolution is explicit", RAMP + " --dump-delta-k .0001",
       output={"interval_steps": 1, "time_ps": 0.001, "increment": 0.002, "resolution_satisfied": False}),
    ok("fixed state with supplied damping times", "state --duration-ps 100 --dt-fs .5 --tau-t-fs 100 --tau-p-fs 1000",
       mode="fixed_state", steps=200000, duration_ps=100.0, rounding_added_fs=0.0,
       tau_t_fs=100.0, tau_p_fs=1000.0, tau_t_over_dt=200.0, tau_p_over_dt=2000.0),
    ok("fixed state steps only", "state --steps 125 --dt-fs 2", steps=125, duration_ps=0.25),
    ok("fixed state ceiling", "state --duration-ps .0025 --dt-fs 1", steps=3, duration_fs=3.0, rounding_added_fs=0.5),
    ok("fixed state explicit upward rounding", "state --duration-ps .0025 --steps 3 --dt-fs 1", steps=3, rounding_added_fs=0.5),
    ok("fixed state precision crosses a timestep", "state --duration-ps .00100000000000000001 --dt-fs 1", steps=2),
    error("missing tension strain", "tension --rate 1e8 --dt-fs 1", "--strain"),
    error("missing timestep", "tension --strain .1 --rate 1e8", "--dt-fs"),
    error("missing span", "tension --strain .1 --dt-fs 1", "Provide rate_s_inv, duration_ps, or steps"),
    error("true strain rejected", TENSION + " --strain-definition true", "exponential schedule"),
    error("short conflicting steps", TENSION + " --steps 999999", "Conflicting steps and rate_s_inv"),
    error("long conflicting steps", TENSION + " --steps 1000001", "Conflicting steps and rate_s_inv"),
    error("conflicting duration", TENSION + " --duration-ps 500", "Conflicting rate and duration"),
    error("fractional steps rejected", "tension --strain .1 --steps 10.5 --dt-fs 1", "positive integer"),
    error("duplicate loaded axes", TENSION + " --axes xx", "unique x/y/z"),
    error("duplicate length rejected", TENSION + " --length x=100 --length x=101", "Duplicate reference length"),
    error("malformed length rejected", TENSION + " --length z", "AXIS=ANGSTROMS"),
    error("unknown length axis rejected", TENSION + " --length xy=100", "AXIS=ANGSTROMS"),
    error("NaN rate rejected", "tension --strain .1 --rate NaN --dt-fs 1", "finite positive"),
    error("infinite timestep rejected", "tension --strain .1 --rate 1e8 --dt-fs Infinity", "finite positive"),
    error("infinite length rejected", TENSION + " --axes z --length z=inf", "finite positive"),
    error("unused malformed length rejected", TENSION + " --axes z --length x=bad", "finite positive"),
    error("invalid numeric text rejected", "tension --strain invalid --steps 10 --dt-fs 1", "finite positive"),
    error("zero strain rejected", "tension --strain 0 --steps 10 --dt-fs 1", "finite positive"),
    error("negative value rejected", "tension --strain=-.1 --steps 10 --dt-fs 1", "finite positive"),
    error("zero dump resolution rejected", TENSION + " --dump-strain 0", "finite positive"),
    error("negative time dump constraint rejected", TENSION + " --dump-max-ps=-1", "finite positive"),
    error("equal ramp endpoints rejected", "ramp --start-k 300 --end-k 300 --rate 2e12 --dt-fs 1", "endpoints must differ"),
    error("missing ramp rate", "ramp --start-k 2500 --end-k 300 --dt-fs 1", "--rate"),
    error("nonfinite ramp endpoint rejected", "ramp --start-k NaN --end-k 300 --rate 2e12 --dt-fs 1", "finite positive"),
    error("fixed state missing span", "state --dt-fs 1", "Provide duration_ps or steps"),
    error("fixed state conflicting short steps", "state --duration-ps .0025 --steps 2 --dt-fs 1", "Conflicting steps and duration_ps"),
    error("fixed state conflicting long steps", "state --duration-ps .0025 --steps 4 --dt-fs 1", "Conflicting steps and duration_ps"),
    error("fixed state rejects a whole extra timestep", "state --duration-ps .002 --steps 3 --dt-fs 1", "Conflicting steps and duration_ps"),
    error("fixed state fractional steps", "state --steps 1.5 --dt-fs 1", "positive integer"),
    error("fixed state zero duration", "state --duration-ps 0 --dt-fs 1", "finite positive"),
    error("fixed state zero thermostat tau", "state --steps 10 --dt-fs 1 --tau-t-fs 0", "tau_t_fs must be a finite positive"),
    error("fixed state negative barostat tau", "state --steps 10 --dt-fs 1 --tau-p-fs=-1", "tau_p_fs must be a finite positive"),
    error("fixed state nonfinite tau", "state --steps 10 --dt-fs 1 --tau-t-fs NaN", "tau_t_fs must be a finite positive"),
    error("fixed state malformed tau", "state --steps 10 --dt-fs 1 --tau-p-fs invalid", "tau_p_fs must be a finite positive"),
    error("fixed state missing timestep", "state --steps 10", "--dt-fs"),
    error("unknown argument remains JSON", TENSION + " --unknown 1", "unrecognized arguments"),
    error("missing command remains JSON", "", "Provide --input PATH"),
    error("missing input file remains JSON", "--input nonexistent-request.json", "nonexistent-request.json"),
    error("mixed input modes rejected", "--input ignored.json " + TENSION, "cannot be combined"),
    error("derived nonfinite output remains JSON", "tension --strain 1e308 --steps 1 --dt-fs 1 --length x=1e308", "Out of range float values"),
]


def snapshot(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def invoke(arguments, directory):
    before = snapshot(directory)
    process = subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=directory,
                             capture_output=True, text=True, encoding="utf-8", timeout=15)
    if snapshot(directory) != before:
        raise AssertionError("calculator wrote or changed a file")
    if process.stderr:
        raise AssertionError(f"unexpected stderr: {process.stderr}")
    return process


def check_case(case, directory):
    process = invoke(case["arguments"], directory)
    result = json.loads(process.stdout)
    if "error_contains" in case:
        assert process.returncode != 0, "invalid request exited successfully"
        assert result["status"] == "error", result
        assert case["error_contains"] in result["error"], result
    else:
        assert process.returncode == 0, result
        assert result["status"] == "ok", result
        for key, expected in case["expected"].items():
            assert result[key] == expected, f"{key}: expected {expected!r}, got {result[key]!r}"
    return {"name": case["name"], "passed": True, "exit_code": process.returncode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="optional path for a JSON verification report")
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix="phase-plan-cli-") as folder:
        directory = Path(folder)
        for case in CASES:
            try:
                results.append(check_case(case, directory))
            except (AssertionError, ValueError, KeyError, subprocess.SubprocessError) as failure:
                results.append({"name": case["name"], "passed": False, "error": str(failure)})

        request = {"mode": "engineering_tension", "strain": 0.1, "rate_s_inv": 1e8,
                   "dt_fs": 1, "axes": "z", "reference_lengths_A": {"z": 100}}
        request_path = directory / "legacy-request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        try:
            legacy = invoke(["--input", str(request_path)], directory)
            scalar = invoke((TENSION + " --axes z --length z=100").split(), directory)
            assert legacy.returncode == scalar.returncode == 0
            assert json.loads(legacy.stdout) == json.loads(scalar.stdout), "JSON and scalar results differ"
            results.append({"name": "legacy --input matches scalar CLI", "passed": True})
        except (AssertionError, ValueError, subprocess.SubprocessError) as failure:
            results.append({"name": "legacy --input matches scalar CLI", "passed": False, "error": str(failure)})

        for command, flags in [([], ["--input", "tension", "ramp", "state"]),
                               (["tension"], ["--length", "--steps", "--duration-ps", "--strain-definition", "--dump-strain", "--dump-max-ps"]),
                               (["ramp"], ["--start-k", "--end-k", "--dump-delta-k", "--dump-max-ps"]),
                               (["state"], ["--duration-ps", "--steps", "--dt-fs", "--tau-t-fs", "--tau-p-fs"])]:
            name = f"{' '.join(command) or 'root'} help is discoverable"
            try:
                help_result = invoke(command + ["--help"], directory)
                assert help_result.returncode == 0
                assert all(flag in help_result.stdout for flag in flags)
                results.append({"name": name, "passed": True})
            except (AssertionError, subprocess.SubprocessError) as failure:
                results.append({"name": name, "passed": False, "error": str(failure)})

    report = {"calculator": SCRIPT.relative_to(SCRIPT.parents[1]).as_posix(), "total": len(results),
              "passed": sum(item["passed"] for item in results),
              "failed": sum(not item["passed"] for item in results), "results": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
