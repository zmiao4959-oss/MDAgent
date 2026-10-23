"""Reference-state arithmetic and continuation files used by the examples."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from miniclaw.rag.gpumd_examples.prepare_tension import prepare


EXAMPLES = Path(__file__).parents[1] / "miniclaw/rag/gpumd_examples"


@pytest.fixture
def inputs(tmp_path):
    restart = tmp_path / "restart.xyz"
    restart.write_text(
        '2\nLattice="35 0 0 0 36 0 0 0 37" Properties=species:S:1:pos:R:3:vel:R:3 pbc="T T T"\n'
        'Cu 0 0 0 0.01 0 0\nNi 1 1 1 0 0.01 0\n', encoding="utf-8",
    )
    potential = tmp_path / "nep.txt"
    potential.write_text("nep4 2 Cu Ni\n", encoding="utf-8")
    return restart, potential


def create(inputs, output, axes="xy", steps=100000):
    return prepare(inputs[0], inputs[1], output, axes, 0.3, steps, 300.0, 1.0)


def test_biaxial_uses_each_relaxed_length_and_preserves_state(inputs, tmp_path):
    output = tmp_path / "load"
    result = create(inputs, output)
    assert (output / "model.xyz").read_bytes() == inputs[0].read_bytes()
    assert (output / "nep.txt").read_bytes() == inputs[1].read_bytes()
    run = (output / "run.in").read_text(encoding="utf-8")
    deform = next(line.split() for line in run.splitlines() if line.startswith("deform "))
    assert list(map(float, deform[1:4])) == pytest.approx([0.000105, 0.000108, 0])
    assert deform[4:] == ["1", "1", "0"]
    assert result["target_lengths_angstrom"]["x"] == pytest.approx(45.5)
    assert result["target_lengths_angstrom"]["y"] == pytest.approx(46.8)
    assert result["target_lengths_angstrom"]["z"] is None
    assert not any(line.strip().startswith("velocity ") for line in run.splitlines())
    assert json.loads((output / "tension_reference.json").read_text())["steps"] == 100000


def test_uniaxial_does_not_prescribe_transverse_final_lengths(inputs, tmp_path):
    result = create(inputs, tmp_path / "load", axes="x", steps=30000)
    assert result["target_lengths_angstrom"] == {"x": 45.5, "y": None, "z": None}
    assert result["increments_angstrom_per_step"]["y"] == 0


def test_existing_output_is_preserved(inputs, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    original = output / "run.in"
    original.write_text("user input", encoding="utf-8")
    with pytest.raises(ValueError, match="overwritten"):
        create(inputs, output)
    assert original.read_text() == "user input"


@pytest.mark.parametrize("change", ["triclinic", "missing_velocity", "incomplete"])
def test_invalid_continuation_is_rejected_before_writing(inputs, tmp_path, change):
    content = inputs[0].read_text()
    if change == "triclinic":
        content = content.replace("35 0 0", "35 1 0")
    elif change == "missing_velocity":
        content = content.replace(":vel:R:3", "")
    else:
        content = content.rsplit("Ni ", 1)[0]
    inputs[0].write_text(content)
    output = tmp_path / "invalid"
    with pytest.raises(ValueError):
        create(inputs, output)
    assert not output.exists()


def test_cli_creates_loading_files_without_launching_md(inputs, tmp_path):
    output = tmp_path / "加载目录"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(EXAMPLES / "prepare_tension.py"),
         "--restart", str(inputs[0]), "--axes", "xy", "--steps", "30000", "--output", str(output)],
        capture_output=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["steps"] == 30000
    assert (output / "run.in").exists()


def test_manifest_contains_complete_template_assets_and_known_command_names():
    entries = json.loads((EXAMPLES / "manifest.json").read_text(encoding="utf-8"))["examples"]
    assert len({entry["id"] for entry in entries}) == 6
    for entry in entries:
        for name in entry["files"]:
            assert (EXAMPLES / name).is_file(), (entry["id"], name)
        assert (EXAMPLES / entry["path"]).read_text(encoding="utf-8").strip()
        for name in entry["files"]:
            if name.endswith(".in"):
                script = (EXAMPLES / name).read_text(encoding="utf-8")
                for line in script.splitlines():
                    line = line.split("#", 1)[0].strip()
                    if line:
                        assert line.split()[0] in entry["command_dependencies"]
