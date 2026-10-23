"""Check the compact fresh-agent answers without rewriting their original format."""
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "references"
TESTS = ROOT / "review/agent-tests"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    raw = read(TESTS / "post-fast-route.json")
    specs = {c["id"]: c["expected"] for c in read(TESTS / "agent-case-specs.json")["cases"] if "expected" in c}
    cases = {c["id"]: c for c in raw["results"]}
    checks = []

    def check(label, actual, expected):
        numeric = isinstance(expected, (int, float)) and not isinstance(expected, bool)
        passed = (abs(Decimal(str(actual)) - Decimal(str(expected))) <= max(abs(Decimal(str(expected))) * Decimal("1e-9"), Decimal("1e-15"))) if numeric else actual == expected
        checks.append({"check": label, "actual": actual, "expected": expected, "passed": passed})

    common = raw["common_controls"]
    check("common dt", common["dt_fs"]["value"], 1)
    for field, expected in [("tau_T_fs", 100), ("tau_P_fs", 1000), ("input_tau_T_over_dt", 100), ("input_tau_P_over_dt", 1000)]:
        check(field, common["coupling"][field], expected)
    for field, expected in [("thermo_steps", 100), ("trajectory_steps", 1000)]:
        check(field, common["plateau_output"][field], expected)

    n1, n2, g = [cases[c]["parameters"] for c in ["N01", "N02", "G01"]]
    for cid, p in [("N01", n1), ("G01", g)]:
        check(cid + " counts", p["composition"]["counts"], specs[cid]["atom_counts"])
        check(cid + " N", p["initial_state"]["N"], specs[cid]["N"])
    for cid, p, initial, maximum in [("N01", n1, 200000, 300000), ("N02", n2, 1800000, 2500000)]:
        check(cid + " initial steps", p["budget"]["initial_steps"], initial)
        check(cid + " maximum steps", p["budget"]["max_steps"], maximum)
        check(cid + " duration", p["budget"]["initial_ps"] * 1000, p["budget"]["initial_steps"])
    check("N01 stage sum", sum(s["run_steps"] for s in n1["stages"][:2]), specs["N01"]["total_steps"])
    check("N02 compatible HCP count", n2["initial_state"]["N"], 4 * 14 * 15 * 12)
    check("N02 stage sum", sum(s.get("run_steps", s.get("per_node_steps", 0) * s.get("count", 0)) for s in n2["stages"][:4]), specs["N02"]["initial_total_steps"])
    check("N02 platform grid", n2["stages"][2]["T_K"], specs["N02"]["platform_K"])
    check("N02 external prior", n2["thermodynamic_targets"]["prior"]["alpha_beta_K"], 1166)
    for tier, prefix in [("fast", "fast"), ("slow_reference", "paper_rate")]:
        data = g[tier]
        check(tier + " cooling steps", data["stages"][1]["run_steps"], specs["G01"][prefix + "_cooling_steps"])
        check(tier + " total steps", data["budget"]["initial_steps"], specs["G01"][prefix + "_total_steps"])
        check(tier + " stage sum", sum(s["run_steps"] for s in data["stages"]), data["budget"]["initial_steps"])
        check(tier + " cooling time", 2200 / data["cooling_rate_K_s"] * 1e12, data["stages"][1]["duration_ps"])
        spec_prefix = "fast" if tier == "fast" else "slow"
        for actual, suffix in [("thermo_steps", "thermo_interval_steps"), ("trajectory_steps", "trajectory_interval_steps")]:
            check(tier + " " + actual, data["stages"][1][actual], specs["G01"][spec_prefix + "_" + suffix])
        for stage in data["stages"]:
            check(stage["id"] + " duration", stage["duration_ps"] * 1000, stage["run_steps"])
    references = read(ROOT / "decisions/reference-index.json")["references"]
    for cid, case in cases.items():
        check(cid + " registered citations", all(s in references for s in case["source_ids"]), True)
    result = {"report": "post-fast-route.json", "passed": sum(c["passed"] for c in checks), "total": len(checks), "checks": checks, "scope": "Numbers, conservation and citation registration only; initial compact provenance grouping receives a separate targeted correction"}
    (ROOT / "review/fast-numeric-recheck.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, ensure_ascii=False))
    return not all(c["passed"] for c in checks)


if __name__ == "__main__":
    raise SystemExit(main())
