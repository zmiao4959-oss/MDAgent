"""Portable preparation and isolated short checks for two-stage tension inputs."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from miniclaw.rag.gpumd_examples.tension_workflow import create_project, prepare_load


SCRIPT = Path(__file__).parents[1] / "miniclaw/rag/gpumd_examples/tension_workflow.py"


@pytest.fixture
def inputs(tmp_path):
    model = tmp_path / "model.xyz"
    model.write_text(
        '2\nLattice="35 0 0 0 36 0 0 0 37" Properties=species:S:1:pos:R:3 pbc="T T T"\n'
        "Cu 0 0 0\nNi 1 1 1\n", encoding="utf-8",
    )
    potential = tmp_path / "potential.txt"
    potential.write_text("nep4 2 Cu Ni\n", encoding="utf-8")
    return model, potential


def make_project(inputs, tmp_path, **kwargs):
    project = tmp_path / "张拉项目"
    create_project(*inputs, project, **kwargs)
    return project


def write_restart(project, trial=False, lx=35.5, ly=36.5):
    directory = project / ("trial/relax" if trial else "relax")
    path = directory / "restart.xyz"
    path.write_text(
        f'2\nLattice="{lx} 0 0 0 {ly} 0 0 0 37" '
        'Properties=species:S:1:pos:R:3:vel:R:3 pbc="T T T"\n'
        "Cu 0 0 0 0.01 0 0\nNi 1 1 1 0 0.01 0\n", encoding="utf-8",
    )
    return path


def command(path, name):
    return next(line.split()[1:] for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith(name + " "))


def cli(*args):
    return subprocess.run([sys.executable, "-X", "utf8", *map(str, args)],
                          capture_output=True, encoding="utf-8", timeout=15)


def test_create_only_writes_inputs_and_missing_restart_is_pending(inputs, tmp_path):
    project = make_project(inputs, tmp_path)
    assert command(project / "relax/run.in", "run") == ["100000"]
    assert command(project / "trial/relax/run.in", "run") == ["200"]
    assert not (project / "load").exists()
    assert not list(project.rglob("restart.xyz"))
    assert (project / "README.md").is_file()
    pending = prepare_load(project)
    assert pending["status"] == "pending"
    assert pending["run_in"] is None
    assert pending["simulations_started"] == 0
    assert not (project / "load").exists()


def test_real_reference_uses_independent_axes_and_preserves_velocities(inputs, tmp_path):
    project = make_project(inputs, tmp_path)
    restart = write_restart(project)
    result = prepare_load(project)
    load = Path(result["directory"])
    assert (load / "model.xyz").read_bytes() == restart.read_bytes()
    assert list(map(float, command(load / "run.in", "deform")[:3])) == pytest.approx([0.3 * 35.5 / 30000, 0.3 * 36.5 / 30000, 0])
    assert result["reference"]["target_lengths_angstrom"] == pytest.approx({"x": 35.5 * 1.3, "y": 36.5 * 1.3, "z": None})
    assert not any(line.startswith("velocity ") for line in (load / "run.in").read_text().splitlines())


def test_trial_keeps_full_loading_rate_and_never_populates_formal_load(inputs, tmp_path):
    project = make_project(inputs, tmp_path)
    write_restart(project, trial=True)
    trial = prepare_load(project, trial=True)
    run = Path(trial["run_in"])
    assert command(run, "run") == ["200"]
    assert command(run, "dump_restart") == ["200"]
    assert list(map(float, command(run, "deform")[:2])) == pytest.approx([0.3 * 35.5 / 30000, 0.3 * 36.5 / 30000])
    assert trial["reference"]["engineering_strain"] == pytest.approx({"x": 0.002, "y": 0.002})
    assert trial["reference"]["engineering_strain_rate_per_s"] == pytest.approx(0.3 / (30000 * 1e-15))
    assert not (project / "load").exists()
    assert prepare_load(project)["status"] == "pending"


def test_changed_restart_or_potential_gets_new_directory_and_unchanged_reuses(inputs, tmp_path):
    project = make_project(inputs, tmp_path)
    write_restart(project)
    first = prepare_load(project)
    again = prepare_load(project)
    assert again["reused"] is True
    assert again["directory"] == first["directory"]
    write_restart(project, lx=36)
    second = prepare_load(project)
    assert second["directory"] != first["directory"]
    assert Path(first["run_in"]).exists()
    potential = project / "relax/nep.txt"
    potential.write_text(potential.read_text() + "# revised\n")
    assert prepare_load(project)["directory"] != second["directory"]


@pytest.mark.parametrize("filename", ["run.in", "model.xyz", "nep.txt", "tension_reference.json"])
def test_modified_prepared_files_are_not_reused_or_overwritten(inputs, tmp_path, filename):
    project = make_project(inputs, tmp_path)
    write_restart(project)
    result = prepare_load(project)
    changed = Path(result["directory"]) / filename
    changed.write_text("user changed this", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to reuse or overwrite modified"):
        prepare_load(project)
    assert changed.read_text() == "user changed this"


def test_project_is_portable_and_cli_does_not_need_the_source_skill(inputs, tmp_path):
    project = make_project(inputs, tmp_path)
    moved = tmp_path / "独立副本"
    shutil.copytree(project, moved)
    shutil.rmtree(project)
    pending = cli(moved / "workflow.py", "prepare-load")
    assert pending.returncode == 0, pending.stderr
    assert json.loads(pending.stdout)["status"] == "pending"
    write_restart(moved)
    prepared = cli(moved / "workflow.py", "prepare-load")
    assert prepared.returncode == 0, prepared.stderr
    assert Path(json.loads(prepared.stdout)["run_in"]).is_relative_to(moved)
    assert not list(moved.rglob("thermo.out"))


def test_create_cli_and_reject_nonempty_output_and_bad_parameters(inputs, tmp_path):
    project = tmp_path / "cli project"
    result = cli(SCRIPT, "create", "--model", inputs[0], "--potential", inputs[1],
                 "--output", project, "--axes", "x", "--load-steps", 100, "--relax-steps", 50)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["simulations_started"] == 0
    assert command(project / "trial/relax/run.in", "run") == ["50"]
    with pytest.raises(ValueError, match="will not be overwritten"):
        create_project(*inputs, project)
    with pytest.raises(ValueError, match="finite and positive"):
        create_project(*inputs, tmp_path / "invalid", strain=float("nan"))
    with pytest.raises(ValueError, match="positive integer"):
        create_project(*inputs, tmp_path / "invalid", load_steps=0)

