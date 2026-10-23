"""Check KB navigation, registries and unchanged local evidence (no MD or network)."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import unquote

SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL / "references"
PROJECT = SKILL.parents[2]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    errors = []
    catalog = read_json(ROOT / "catalog.json")
    for relative in ("SKILL.md", "scripts/calculate_plan.py", "scripts/check_calculator.py", "scripts/check_knowledge.py"):
        if not (SKILL / relative).is_file():
            errors.append(f"missing skill entry: {relative}")
    pages = sorted(p for p in ROOT.rglob("*.md") if "agent-tests" not in p.parts)
    indexed = [item["path"] for item in catalog["pages"]]
    actual = sorted(p.relative_to(ROOT).as_posix() for p in pages)
    if sorted(indexed) != actual or len(indexed) != len(set(indexed)):
        errors.append("catalog does not match knowledge pages")

    link_count = 0
    for page in ROOT.rglob("*.md"):
        for match in re.finditer(r"\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)", page.read_text(encoding="utf-8-sig")):
            target = unquote(match.group(1).strip("<>"))
            if re.match(r"^(https?://|mailto:|codex:|app:|#)", target):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            link_count += 1
            destination = Path(target) if re.match(r"^[A-Za-z]:[\\/]", target) else page.parent / target
            if not destination.exists():
                errors.append(f"broken link: {page.relative_to(ROOT)} -> {target}")

    sources = read_json(ROOT / "sources/sources.json")["sources"]
    if len({s["id"] for s in sources}) != len(sources):
        errors.append("duplicate source ID")
    references_index = read_json(ROOT / "decisions/reference-index.json")["references"]
    source_ids = {source["id"] for source in sources}
    for reference_id, reference in references_index.items():
        if not (ROOT / reference["path"]).is_file():
            errors.append(f"reference index target missing: {reference_id}")
        if reference.get("kind") == "registered_source" and reference.get("locator") not in source_ids:
            errors.append(f"reference source ID missing: {reference_id}")
    policy = read_json(ROOT / "decisions/policies.json")

    def check_reference_ids(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"source_ids", "basis"}:
                    for reference_id in item:
                        if reference_id not in references_index:
                            errors.append(f"unregistered active reference: {reference_id}")
                else:
                    check_reference_ids(item)
        elif isinstance(value, list):
            for item in value:
                check_reference_ids(item)

    check_reference_ids(policy)
    check_reference_ids(read_json(ROOT / "decisions/handoff-contract.json"))
    references_checked = 0
    for source in sources:
        references = []
        if source.get("path") and not (ROOT / "sources" / source["path"]).is_file():
            errors.append(f"source registry target missing: {source['id']}")
        if source.get("local_path"):
            references.append((Path(source["local_path"]), source["local_sha256"]))
        for item in source.get("local_files", []):
            path = Path(item.get("absolute_path", item.get("path", "")))
            references.append((path if path.is_absolute() else PROJECT / path, item["sha256"]))
        for path, expected in references:
            references_checked += 1
            if not path.is_file() or digest(path) != expected:
                errors.append(f"local reference missing or changed: {source['id']} {path}")

    inventory = read_json(ROOT / "assets/local-inventory.json")
    assets = inventory["models"] + inventory["datasets"] + [inventory["training_configuration"]]
    for item in assets:
        path = Path(item["absolute_path"])
        if not path.is_file() or digest(path) != item["sha256"]:
            errors.append(f"local asset missing or changed: {path}")

    migration = read_json(ROOT / "review/skill-migration.json")
    migrated_files = migration["files"]
    mapping = {item["original_path"]: item for item in migrated_files}
    if len(mapping) != len(migrated_files):
        errors.append("duplicate original migration path")
    historical_checked = 0
    for item in migrated_files:
        target = (SKILL / item["skill_path"]).resolve()
        if not target.is_relative_to(SKILL):
            errors.append(f"migration path escapes skill: {item['skill_path']}")
            continue
        if not target.is_file():
            errors.append(f"migrated file missing: {item['skill_path']}")
            continue
        if item.get("change") == "generated_validation":
            continue
        if digest(target) != item["migrated_sha256"]:
            errors.append(f"migrated file changed: {item['skill_path']}")
        if item.get("change") == "historical_evaluation_preserved":
            historical_checked += 1
            if item["migrated_sha256"] != item["original_sha256"]:
                errors.append(f"historical evaluation was rewritten: {item['skill_path']}")
    if (PROJECT / migration["original_root"]).exists():
        errors.append("old knowledge root still exists; skill must be the single knowledge source")

    manifest = read_json(ROOT / "review/agent-tests/v0.2-decision-manifest.json")
    for item in manifest["files"]:
        original = migration["original_root"] + "/" + item["path"]
        if original not in mapping or mapping[original]["original_sha256"] != item["sha256"]:
            errors.append(f"historical decision fingerprint does not match migration source: {item['path']}")

    result = {
        "checked_on": str(date.today()),
        "knowledge_version": catalog["version"],
        "checks": {
            "markdown_pages": len(pages), "catalog_matches": sorted(indexed) == actual,
            "local_markdown_links_checked": link_count,
            "source_records": len(sources),
            "locatable_reference_ids": len(references_index),
            "external_source_urls_listed": sum(bool(s.get("url")) for s in sources),
            "local_reference_hashes_checked": references_checked,
            "local_assets_hashes_checked": len(assets),
            "inventory_model_files": len(inventory["models"]),
            "inventory_unique_models": len({m["sha256"] for m in inventory["models"]}),
            "dataset_frames": {Path(d["absolute_path"]).name: d["frames"] for d in inventory["datasets"]},
            "historical_decision_fingerprints_checked": len(manifest["files"]),
            "migration_files_checked": len(migrated_files),
            "historical_evaluation_files_preserved": historical_checked,
        },
        "errors": errors,
        "scope": "Skill navigation, catalog, registered reference IDs, local evidence and migration integrity. Historical evaluations retain their original scope and do not validate the new skill packaging.",
        "not_evaluated": ["Independent materials-science peer review", "NEP prediction accuracy", "MD convergence or phase transitions", "Runtime execution", "Live-process skill reload", "New blind agent behavior under skill packaging", "Live HTTP availability of every source URL"],
        "status": "passed" if not errors else "failed",
    }
    (ROOT / "review/validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
