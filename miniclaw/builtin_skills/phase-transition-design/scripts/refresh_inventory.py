"""Read local NEP assets and write a small, reproducible metadata snapshot.

Only the output JSON is written. Potential files and XYZ datasets are read-only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re


def fingerprint(path: Path, root: Path) -> dict:
    before = path.stat()
    sha256, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(block)
            md5.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Asset changed during reading: {path}")
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "absolute_path": str(path.resolve()),
        "bytes": after.st_size,
        "sha256": sha256.hexdigest(),
        "md5": md5.hexdigest(),
    }


def model_record(path: Path, root: Path, role: str) -> dict:
    result = fingerprint(path, root)
    lines = []
    with path.open(encoding="utf-8") as stream:
        for _ in range(7):
            line = stream.readline()
            if not line:
                break
            lines.append(line.strip())
            if line.startswith("ANN "):
                break
    first = lines[0].split()
    count = int(first[1])
    if len(first[2:]) != count:
        raise ValueError(f"Element count does not match model header: {path}")
    result.update(role=role, format=first[0], elements=first[2:], header=lines)
    return result


def audit_xyz(path: Path, root: Path) -> dict:
    before = path.stat()
    sizes, compositions, fields = Counter(), Counter(), Counter()
    atom_total, frames = 0, 0
    elements = set()
    with path.open(encoding="utf-8") as stream:
        while True:
            line = stream.readline()
            if not line:
                break
            if not line.strip():
                continue
            size = int(line.strip())
            if size <= 0:
                raise ValueError(f"Invalid atom count in {path}")
            header = stream.readline()
            if not header or "Properties=species:S:1:" not in header:
                raise ValueError(f"Unsupported XYZ property order in {path}, frame {frames}")
            fields.update(set(re.findall(r"\b([A-Za-z_][A-Za-z_0-9]*)=", header)))
            counts = Counter()
            for _ in range(size):
                row = stream.readline().split()
                if not row:
                    raise ValueError(f"Truncated frame in {path}")
                counts[row[0]] += 1
            frames += 1
            atom_total += size
            sizes[size] += 1
            elements.update(counts)
            compositions[tuple(sorted(counts.items()))] += 1
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Dataset changed during parsing: {path}")
    result = fingerprint(path, root)
    result.update(
        frames=frames,
        total_atom_records=atom_total,
        atom_count_range=[min(sizes), max(sizes)],
        elements=sorted(elements),
        distinct_atom_count_compositions=len(compositions),
        composition_definition="Exact element counts per frame, not reduced atomic fractions",
        top_compositions=[
            {"atom_counts": dict(composition), "frames": number}
            for composition, number in compositions.most_common(6)
        ],
        metadata_field_frame_counts=dict(sorted(fields.items())),
        temperature_or_pressure_label_frames=sum(
            number for key, number in fields.items()
            if key.lower() in {"temperature", "temp", "t", "pressure", "p"}
        ),
        interpretation="File-content audit only; not a potential accuracy or phase validation",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--potential-root", required=True, type=Path)
    parser.add_argument("--observed-on", default=datetime.now(timezone(timedelta(hours=8))).date().isoformat())
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "references" / "assets" / "local-inventory.json")
    args = parser.parse_args()
    root = args.potential_root.resolve(strict=True)
    package = root / "PdCuNiP/PdCuNiP_NEP-main/PdCuNiP_NEP/PdCuNiP_NEP"
    models = [model_record(root / "16元/UNEP-v1-main.txt", root, "unep_v1_main")]
    ensemble = root / "16元/ensemble-nep-models/ensemble-nep-models"
    models.extend(model_record(path, root, "unep_v1_ensemble") for path in sorted(ensemble.glob("*.txt")))
    models.append(model_record(root / "PdCuNiP/nep4.txt", root, "pdcunip_main"))
    models.append(model_record(package / "nep.population160.generation1013200.txt", root, "pdcunip_package_copy"))
    groups = {}
    for model in models:
        groups.setdefault(model["sha256"], []).append(model["relative_path"])
    expected_md5 = "337669f3a94bd42ebb765e773deaf05e"
    data = {
        "schema_version": 1,
        "observed_on": args.observed_on,
        "timezone": "Asia/Shanghai",
        "source_root": str(root),
        "scope": ["UNEP-v1", "Pd-Cu-Ni-P"],
        "models": models,
        "same_content_groups": [paths for paths in groups.values() if len(paths) > 1],
        "unep_published_identity_check": {
            "source": "https://zenodo.org/records/11533864",
            "published_file": "UNEP-v1-main.txt",
            "published_md5": expected_md5,
            "local_matches": models[0]["md5"] == expected_md5,
            "meaning": "Byte identity against the published checksum; not scientific validation",
        },
        "datasets": [audit_xyz(package / name, root) for name in ("train.xyz", "test.xyz")],
        "training_configuration": fingerprint(package / "nep.in", root),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "model_files": len(models),
        "unique_models": len(groups),
        "dataset_frames": {Path(item["relative_path"]).name: item["frames"] for item in data["datasets"]},
        "unep_published_checksum_matches": data["unep_published_identity_check"]["local_matches"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
