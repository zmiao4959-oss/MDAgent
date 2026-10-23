"""Reproduce numeric assertions against preserved agent answers and fixed case specs."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "references"
TESTS = ROOT / "review/agent-tests"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def lookup(value, path):
    for part in path.split("/"):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def same(actual, expected):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            return False
        a, e = Decimal(str(actual)), Decimal(str(expected))
        return abs(a - e) <= max(abs(e) * Decimal("1e-9"), Decimal("1e-15"))
    return actual == expected


def main():
    specs = {c["id"]: c for c in read(TESTS / "agent-case-specs.json")["cases"]}
    answers = {}
    for filename in ["post-npt.json", "post-tension.json", "post-holdouts.json"]:
        for case in read(TESTS / filename)["cases"]:
            answers[case["case_id"]] = case
    assertions = []
    for binding in read(TESTS / "numeric-bindings.json")["bindings"]:
        case_id = binding["case_id"]
        expected = lookup(specs[case_id]["expected"], binding["expected_path"])
        try:
            actual = lookup(answers[case_id]["parameters"], binding["actual_path"])
            passed = same(actual, expected)
        except (KeyError, TypeError, IndexError):
            actual, passed = "MISSING", False
        assertions.append({**binding, "expected": expected, "actual": actual, "passed": passed})

    consistency = []
    for case_id, case in answers.items():
        p = case["parameters"]
        if p.get("dt_fs") and isinstance(p.get("tau_T_fs"), (int, float)):
            for physical, ratio in [("tau_T_fs", "T_coup"), ("tau_P_fs", "P_coup")]:
                consistency.append({"case_id": case_id, "check": f"{ratio}*dt={physical}", "passed": same(p[ratio] * p["dt_fs"], p[physical])})
        if isinstance(p.get("load_steps"), int):
            consistency.append({"case_id": case_id, "check": "load steps and duration", "passed": same(p["load_steps"] * p["dt_fs"] / 1000, p["load_duration_ps"])})
        if p.get("strain_definition") == "engineering" and p.get("load_steps"):
            for target, key in [(1e-5, "thermo_interval_steps"), (1e-4, "trajectory_interval_steps")]:
                delta = p["strain_end"] / p["load_steps"] * p[key]
                consistency.append({"case_id": case_id, "check": key + " strain resolution", "actual_delta": delta, "limit": target, "passed": delta <= target * (1 + 1e-9)})
        stages = p.get("stages", [])
        if stages:
            consistency.append({"case_id": case_id, "check": "stage step sum", "passed": same(sum(s["run_steps"] for s in stages), p["total_steps"])})
            for stage in stages:
                consistency.append({"case_id": case_id, "check": stage["stage_id"] + " time", "passed": same(stage["run_steps"] * stage["dt_fs"] / 1000, stage["duration_ps"])})

    reference_ids = read(ROOT / "decisions/reference-index.json")["references"]
    citation_issues = [{"case_id": cid, "id": sid} for cid, case in answers.items() for sid in case["source_ids"] if sid not in reference_ids]
    result = {
        "scope": "Fixed numerical expectations, generic conservation checks and registered source IDs on INITIAL raw reports. Semantic audit and targeted fixes are reported separately.",
        "numeric_assertions_passed": sum(a["passed"] for a in assertions), "numeric_assertions_total": len(assertions),
        "consistency_checks_passed": sum(a["passed"] for a in consistency), "consistency_checks_total": len(consistency),
        "unregistered_initial_citations": citation_issues,
        "assertions": assertions, "consistency": consistency,
        "not_measured": ["Physical validity or optimality of candidates", "Generalization beyond these cases", "MD outcomes", "Model token costs or wall-clock latency"],
    }
    (ROOT / "review/numeric-recheck.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ["assertions", "consistency"]}, ensure_ascii=False, indent=2))
    return not all(a["passed"] for a in assertions + consistency)


if __name__ == "__main__":
    raise SystemExit(main())
