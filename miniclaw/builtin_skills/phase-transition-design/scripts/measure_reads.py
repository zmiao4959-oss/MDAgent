"""Measure declared unique knowledge files, excluding agents' own answer artifacts."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "references"
TESTS = ROOT / "review/agent-tests"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    initial_sizes = {x["path"]: x["bytes"] for x in read(TESTS / "v0.2-initial-decision-manifest.json")["files"]}
    final_sizes = {x["path"]: x["bytes"] for x in read(TESTS / "v0.2-fast-route-manifest.json")["files"]}
    migration_sizes = {x["original_path"].removeprefix("knowledge/phase-transition/"): x["original_bytes"] for x in read(ROOT / "review/skill-migration.json")["files"]}
    output = {}
    for filename in ["post-npt.json", "post-tension.json", "post-holdouts.json", "post-fast-route.json"]:
        if not (TESTS / filename).is_file():
            continue
        report = read(TESTS / filename)
        sizes = final_sizes if filename == "post-fast-route.json" else initial_sizes

        def measure(paths):
            paths = [p.removeprefix("knowledge/phase-transition/").removeprefix("miniclaw/builtin_skills/phase-transition-design/references/") for p in paths]
            paths = sorted({p for p in paths if not p.startswith("review/agent-tests/")})
            return {"files": len(paths), "bytes": sum(sizes[p] if p in sizes else migration_sizes[p] for p in paths), "paths": paths}

        output[filename] = {
            "aggregate": measure(report["read_files"]),
            "per_case": {case.get("case_id", case.get("id")): measure(case["used_files"]) for case in report.get("cases", report.get("results", []))},
        }
    result = {"metric": "Declared unique knowledge-file bytes, not API tokens, latency or output length", "baseline": read(TESTS / "baseline.json"), "reports": output, "limitations": ["Self-reported file inventory, not a token profiler", "Small human-designed cases; no repeated randomized A/B test", "Initial and final route manifests distinguish entry revisions", "Missing historical size entries use the pre-migration byte snapshot; this is not a measurement of new skill reads", "An agent may share reads across its cases; aggregate and per-case metrics are not interchangeable"]}
    (ROOT / "review/read-measurements-recheck.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: {"aggregate": v["aggregate"]["bytes"], "per_case": {c: d["bytes"] for c, d in v["per_case"].items()}} for k, v in output.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
