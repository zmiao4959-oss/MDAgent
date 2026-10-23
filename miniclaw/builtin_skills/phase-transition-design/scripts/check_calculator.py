"""Exercise the calculator CLI against fixed, independently specified cases."""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    skill = Path(__file__).resolve().parents[1]
    root = skill / "references"
    suite = json.loads((root / "review/agent-tests/calculator-cases.json").read_text(encoding="utf-8"))
    records = []
    for case in suite["cases"]:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as handle:
            json.dump(case["input"], handle)
            name = Path(handle.name)
        try:
            run = subprocess.run([sys.executable, str(skill / "scripts/calculate_plan.py"), "--input", str(name)], capture_output=True, text=True, encoding="utf-8", timeout=15)
            actual = json.loads(run.stdout)
        finally:
            name.unlink(missing_ok=True)
        failures = []
        if case.get("error"):
            if run.returncode != 2 or actual.get("status") != "error":
                failures.append("Expected a structured error")
        else:
            if run.returncode or actual.get("status") != "ok":
                failures.append("Expected success")
            for key, expected in case["expect"].items():
                value = actual
                try:
                    for part in key.split("."):
                        value = value[part]
                except (KeyError, TypeError):
                    failures.append(f"Missing {key}")
                    continue
                if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                    matches = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-14)
                else:
                    matches = value == expected
                if not matches:
                    failures.append(f"{key}: expected {expected!r}, got {value!r}")
        records.append({"id": case["id"], "passed": not failures, "failures": failures, "actual": actual})
    report = {"suite": "calculator-cli", "passed": sum(item["passed"] for item in records), "total": len(records), "scope": suite["scope"], "results": records}
    (root / "review/calculator-regression.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("suite", "passed", "total")}, ensure_ascii=False))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
