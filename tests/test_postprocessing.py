import asyncio
import csv
import itertools
import json
import math
from types import SimpleNamespace

import pytest

from miniclaw.settings import workspace_scope
from miniclaw.tools import ensure_tools_loaded
from miniclaw.tools import postprocessing
from miniclaw.tools.registry import tool_registry


def analyze_phase_fraction(*args, **kwargs):
    return asyncio.run(postprocessing.analyze_phase_fraction(*args, **kwargs))


def crystal_frame(kind):
    """Small periodic crystals with known PTM classifications, not MD output."""
    basis = (
        [(0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0)]
        if kind == "FCC" else [(0, 0, 0), (0.5, 0.5, 0.5)]
    )
    positions = [
        tuple(3.6 * (cell[d] + site[d]) for d in range(3))
        for cell in itertools.product(range(3), repeat=3) for site in basis
    ]
    return (
        f'{len(positions)}\nLattice="10.8 0 0 0 10.8 0 0 0 10.8" '
        'pbc="T T T" Properties=species:S:1:pos:R:3\n'
        + "".join(f"Cu {x} {y} {z}\n" for x, y, z in positions)
    )


@pytest.fixture
def trajectory(tmp_path):
    path = tmp_path / "dump.xyz"
    path.write_text(crystal_frame("FCC") + crystal_frame("BCC") + crystal_frame("FCC"), encoding="utf-8")
    return path


def test_ptm_classifies_all_frames_and_uses_each_frames_atom_count(trajectory):
    pytest.importorskip("ovito")
    before = trajectory.read_bytes()
    with workspace_scope(trajectory.parent):
        # Exercise the actual registry/thread invocation used by agents.
        ensure_tools_loaded()
        result = json.loads(asyncio.run(tool_registry.execute("analyze_phase_fraction", {})))
    assert result["status"] == "SUCCEEDED"
    assert result["frames_processed"] == result["total_frames"] == 3
    assert result["last_frame"]["fractions"]["FCC"] == 1.0
    with open(result["output_file"], newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == [
        "Frame", "N_atoms", "Other_fraction", "FCC_fraction",
        "HCP_fraction", "BCC_fraction", "ICO_fraction",
    ]
    assert [int(row["N_atoms"]) for row in rows] == [108, 54, 108]
    for frame, (row, expected) in enumerate(zip(rows, ("FCC", "BCC", "FCC"))):
        assert int(row["Frame"]) == frame
        assert float(row[expected + "_fraction"]) == 1.0
        assert sum(float(value) for key, value in row.items() if key.endswith("_fraction")) == pytest.approx(1.0)
    assert trajectory.read_bytes() == before


def test_frame_range_preserves_original_frame_indices(trajectory):
    pytest.importorskip("ovito")
    with workspace_scope(trajectory.parent):
        result = json.loads(analyze_phase_fraction(
            end_frame=3, frame_step=2, output_file="analysis/selected.csv", rmsd_cutoff=0,
        ))
        selected = json.loads(analyze_phase_fraction(start_frame=1, end_frame=2))
    with open(result["output_file"], newline="", encoding="utf-8") as handle:
        assert [int(row["Frame"]) for row in csv.DictReader(handle)] == [0, 2]
    assert selected["frames_processed"] == 1
    assert selected["last_frame"]["frame"] == 1
    assert selected["last_frame"]["fractions"]["BCC"] == 1.0


def test_ico_recognition_is_enabled(tmp_path):
    pytest.importorskip("ovito")
    golden = (1 + math.sqrt(5)) / 2
    vertices = [(0, 0, 0)]
    for a, b in itertools.product((-1, 1), repeat=2):
        vertices.extend([(0, a, b * golden), (a, b * golden, 0), (b * golden, 0, a)])
    path = tmp_path / "ico.xyz"
    path.write_text(
        '13\nLattice="20 0 0 0 20 0 0 0 20" pbc="F F F" Properties=species:S:1:pos:R:3\n'
        + "".join(f"Cu {x + 10} {y + 10} {z + 10}\n" for x, y, z in vertices),
        encoding="utf-8",
    )
    with workspace_scope(tmp_path):
        result = json.loads(analyze_phase_fraction("ico.xyz"))
    assert result["last_frame"]["fractions"]["ICO"] == pytest.approx(1 / 13)


def test_hcp_recognition_in_a_triclinic_periodic_cell(tmp_path):
    pytest.importorskip("ovito")
    a = 3.0
    vectors = ((a, 0, 0), (-a / 2, math.sqrt(3) * a / 2, 0), (0, 0, math.sqrt(8 / 3) * a))
    positions = [
        tuple(sum((cell[i] + site[i]) * vectors[i][d] for i in range(3)) for d in range(3))
        for cell in itertools.product(range(3), repeat=3)
        for site in ((0, 0, 0), (2 / 3, 1 / 3, 0.5))
    ]
    lattice = " ".join(str(3 * coordinate) for vector in vectors for coordinate in vector)
    (tmp_path / "hcp.xyz").write_text(
        f'{len(positions)}\nLattice="{lattice}" pbc="T T T" Properties=species:S:1:pos:R:3\n'
        + "".join(f"Cu {x} {y} {z}\n" for x, y, z in positions), encoding="utf-8",
    )
    with workspace_scope(tmp_path):
        result = json.loads(analyze_phase_fraction("hcp.xyz"))
    assert result["last_frame"]["fractions"]["HCP"] == 1.0


@pytest.mark.parametrize("arguments", [
    {"rmsd_cutoff": -0.1}, {"rmsd_cutoff": float("nan")},
    {"rmsd_cutoff": float("inf")}, {"rmsd_cutoff": True},
    {"start_frame": -1}, {"frame_step": 0}, {"start_frame": True},
    {"end_frame": 0}, {"end_frame": 1.5},
])
def test_invalid_parameters_do_not_create_output(trajectory, arguments):
    with workspace_scope(trajectory.parent):
        assert analyze_phase_fraction(**arguments).startswith("Error:")
    assert not trajectory.with_name("phase_fraction.csv").exists()


def test_invalid_paths_and_ranges_leave_input_untouched(trajectory):
    pytest.importorskip("ovito")
    before = trajectory.read_bytes()
    with workspace_scope(trajectory.parent):
        assert "not overwrite" in analyze_phase_fraction(output_file="dump.xyz")
        assert "allowed roots" in analyze_phase_fraction(output_file="../outside.csv")
        assert "path not found" in analyze_phase_fraction("missing.xyz")
        assert "invalid frame range" in analyze_phase_fraction(start_frame=3)
        assert "invalid frame range" in analyze_phase_fraction(end_frame=4)
    assert trajectory.read_bytes() == before


def test_failure_in_later_frame_preserves_existing_csv(trajectory, monkeypatch):
    destination = trajectory.with_name("phase_fraction.csv")
    destination.write_text("previous successful analysis\n", encoding="utf-8")

    async def launch(*args, **kwargs):
        payload = json.loads(args[-1])
        with open(payload["temporary"], "w", encoding="utf-8") as handle:
            handle.write("partial frame 0 output\n")

        async def communicate():
            return json.dumps({"status": "FAILED", "error": "broken frame 1"}).encode(), b""

        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(postprocessing.asyncio, "create_subprocess_exec", launch)
    with workspace_scope(trajectory.parent):
        assert "broken frame 1" in analyze_phase_fraction()
    assert destination.read_text(encoding="utf-8") == "previous successful analysis\n"
    assert not list(trajectory.parent.glob("phase_fraction.csv.*.tmp"))


def test_cancel_analysis_terminates_worker_and_preserves_previous_output(trajectory, monkeypatch):
    destination = trajectory.with_name("phase_fraction.csv")
    destination.write_text("previous successful analysis\n", encoding="utf-8")

    async def run():
        started = asyncio.Event()
        process = SimpleNamespace(returncode=None, killed=False)

        def kill():
            process.killed = True
            process.returncode = -9

        async def communicate():
            if process.killed:
                return b"", b""
            started.set()
            await asyncio.Event().wait()

        async def launch(*args, **kwargs):
            return process

        process.kill = kill
        process.communicate = communicate
        monkeypatch.setattr(postprocessing.asyncio, "create_subprocess_exec", launch)
        with workspace_scope(trajectory.parent):
            task = asyncio.create_task(postprocessing.analyze_phase_fraction())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert process.killed

    asyncio.run(run())
    assert destination.read_text(encoding="utf-8") == "previous successful analysis\n"
    assert not list(trajectory.parent.glob("phase_fraction.csv.*.tmp"))


def dump_frame(positions, *, energy=-10, time=0, lattice=(10, 0, 0, 0, 10, 0, 0, 0, 10),
               ids=None, masses=None):
    properties = "species:S:1:pos:R:3"
    if ids is not None:
        properties += ":id:I:1"
    if masses is not None:
        properties += ":mass:R:1"
    header = f'Lattice="{" ".join(map(str, lattice))}" pbc="T T T" Properties={properties}'
    if energy is not None:
        header += f" energy={energy}"
    if time is not None:
        header += f" Time={time}"
    rows = []
    for i, position in enumerate(positions):
        fields = ["Cu", *map(str, position)]
        if ids is not None:
            fields.append(str(ids[i]))
        if masses is not None:
            fields.append(str(masses[i]))
        rows.append(" ".join(fields))
    return f"{len(rows)}\n{header}\n" + "\n".join(rows) + "\n"


def run_registered(name, arguments=None):
    ensure_tools_loaded()
    value = asyncio.run(tool_registry.execute(name, arguments or {}))
    assert not value.startswith("Error:"), value
    return json.loads(value)


def csv_rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_energy_is_total_potential_and_volume_is_triclinic_cell_determinant(tmp_path):
    pytest.importorskip("ovito")
    path = tmp_path / "dump.xyz"
    path.write_text(
        dump_frame([(0, 0, 0), (2, 0, 0)], energy=-12, time=50,
                   lattice=(10, 0, 0, 3, 10, 0, 2, 1, 20)), encoding="utf-8",
    )
    with workspace_scope(tmp_path):
        energy = run_registered("analyze_potential_energy")
        volume = run_registered("analyze_volume")
    row = csv_rows(energy["output_file"])[0]
    assert float(row["Potential_energy_eV"]) == -12
    assert float(row["Potential_energy_per_atom_eV"]) == -6
    assert float(row["Time_fs"]) == 50
    row = csv_rows(volume["output_file"])[0]
    assert float(row["Volume_A3"]) == pytest.approx(2000)
    assert float(row["Volume_per_atom_A3"]) == pytest.approx(1000)
    assert energy["frames_processed"] == volume["frames_processed"] == 1


def test_rdf_two_atom_distance_and_integrated_coordination(tmp_path):
    pytest.importorskip("ovito")
    (tmp_path / "dump.xyz").write_text(
        dump_frame([(1, 1, 1), (3, 1, 1)])
        + dump_frame([(1, 1, 1), (4, 1, 1)], time=20), encoding="utf-8",
    )
    with workspace_scope(tmp_path):
        result = run_registered("analyze_rdf", {"cutoff": 4, "number_of_bins": 40})
    rows = csv_rows(result["output_file"])
    assert len(rows) == 80
    for frame, distance in ((0, 2), (1, 3)):
        histogram = [row for row in rows if int(row["Frame"]) == frame]
        peak = max(histogram, key=lambda row: float(row["RDF"]))
        assert abs(float(peak["Radius_A"]) - distance) <= 0.051
        # Integral rho*g(r)*shell_volume equals the known one neighbor per atom.
        coordination = sum(
            float(row["RDF"]) * (4 * math.pi / 3) * (((i + 1) * 0.1) ** 3 - (i * 0.1) ** 3)
            for i, row in enumerate(histogram)
        ) * 2 / 1000
        assert coordination == pytest.approx(1, rel=1e-5)


def test_msd_unwraps_accumulated_motion_and_uses_skipped_frames(tmp_path):
    pytest.importorskip("ovito")
    (tmp_path / "dump.xyz").write_text("".join(
        dump_frame([((9 + 2 * frame) % 10, 1, 1), ((1 + 2 * frame) % 10, 3, 3)], time=10 * frame)
        for frame in range(5)
    ), encoding="utf-8")
    with workspace_scope(tmp_path):
        result = run_registered("analyze_msd", {"frame_step": 4})
        drift_removed = run_registered("analyze_msd", {"frame_step": 4, "remove_com_drift": True,
                                                      "output_file": "no_drift.csv"})
    rows = csv_rows(result["output_file"])
    assert [int(row["Frame"]) for row in rows] == [0, 4]
    assert float(rows[-1]["MSD_A2"]) == pytest.approx(64)
    assert float(rows[-1]["MSD_x_A2"]) == pytest.approx(64)
    assert float(rows[-1]["MSD_y_A2"]) == pytest.approx(0)
    assert float(rows[-1]["Delta_time_fs"]) == 40
    assert float(csv_rows(drift_removed["output_file"])[-1]["MSD_A2"]) == pytest.approx(0)


def test_msd_particle_ids_allow_reordered_rows_and_reference_time_can_be_missing(tmp_path):
    pytest.importorskip("ovito")
    (tmp_path / "dump.xyz").write_text(
        dump_frame([(1, 1, 1), (4, 1, 1)], ids=[1, 2], time=None)
        + dump_frame([(4, 3, 1), (2, 1, 1)], ids=[2, 1], time=None), encoding="utf-8",
    )
    with workspace_scope(tmp_path):
        result = run_registered("analyze_msd", {"unwrap": False})
    row = csv_rows(result["output_file"])[-1]
    assert float(row["MSD_A2"]) == pytest.approx((1 + 4) / 2)
    assert row["Time_fs"] == row["Delta_time_fs"] == ""


def test_msd_mass_weighted_drift_and_unwrapped_input(tmp_path):
    pytest.importorskip("ovito")
    (tmp_path / "dump.xyz").write_text(
        dump_frame([(1, 1, 1), (3, 1, 1)], masses=[1, 3])
        + dump_frame([(3, 1, 1), (3, 1, 1)], masses=[1, 3], time=10), encoding="utf-8",
    )
    with workspace_scope(tmp_path):
        result = run_registered("analyze_msd", {"unwrap": False, "remove_com_drift": True})
    # COM displacement=(1*2+3*0)/4=0.5; atom-averaged squared residual=(1.5^2+0.5^2)/2.
    assert float(csv_rows(result["output_file"])[-1]["MSD_A2"]) == pytest.approx(1.25)


def test_msd_affine_mapping_removes_uniform_cell_deformation(tmp_path):
    pytest.importorskip("ovito")
    (tmp_path / "dump.xyz").write_text(
        dump_frame([(2, 2, 2), (4, 4, 4)])
        + dump_frame([(4, 4, 4), (8, 8, 8)], time=10,
                     lattice=(20, 0, 0, 0, 20, 0, 0, 0, 20)), encoding="utf-8",
    )
    with workspace_scope(tmp_path):
        raw = run_registered("analyze_msd", {"unwrap": False, "output_file": "raw.csv"})
        mapped = run_registered("analyze_msd", {"affine_mapping": "to_reference"})
    assert float(csv_rows(raw["output_file"])[-1]["MSD_A2"]) == pytest.approx(30)
    assert float(csv_rows(mapped["output_file"])[-1]["MSD_A2"]) == pytest.approx(0)


def test_all_tool_matches_five_individual_tools(tmp_path):
    pytest.importorskip("ovito")
    (tmp_path / "dump.xyz").write_text("".join(
        crystal_frame("FCC").replace('pbc="T T T"', f'pbc="T T T" energy={-108 * (i + 1)} Time={i * 20}')
        for i in range(2)
    ), encoding="utf-8")
    before = (tmp_path / "dump.xyz").read_bytes()
    with workspace_scope(tmp_path):
        combined = run_registered("analyze_all_postprocessing", {"cutoff": 4.5, "number_of_bins": 12})
        assert set(combined["output_files"]) == set(postprocessing._OUTPUT_NAMES)
        for analysis in postprocessing._OUTPUT_NAMES:
            arguments = {"cutoff": 4.5, "number_of_bins": 12} if analysis == "rdf" else {}
            individual = run_registered("analyze_" + analysis, arguments)
            assert csv_rows(combined["output_files"][analysis]) == csv_rows(individual["output_file"])
        pytest.importorskip("matplotlib")
        plots = run_registered("plot_postprocessing", {"input_dir": "postprocessing"})
    assert plots["backend"] == "Agg"
    assert {plot["analysis"] for plot in plots["plots"]} == set(postprocessing._OUTPUT_NAMES)
    for path in plots["output_files"]:
        from PIL import Image
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.width > 1000 and image.height > 600
            assert image.convert("L").getextrema()[0] < 100
        assert path.endswith(".png")
    assert combined["frames_processed"] == 2
    assert combined["last_frame"]["phase_fraction"]["FCC"] == 1
    assert (tmp_path / "dump.xyz").read_bytes() == before


def test_batch_failure_preserves_all_existing_outputs_and_single_tools_are_independent(tmp_path):
    pytest.importorskip("ovito")
    (tmp_path / "dump.xyz").write_text(
        dump_frame([(1, 1, 1), (3, 1, 1)])
        + dump_frame([(1, 1, 1), (3, 1, 1)], energy=None, time=10), encoding="utf-8",
    )
    output_dir = tmp_path / "postprocessing"
    output_dir.mkdir()
    for name in postprocessing._OUTPUT_NAMES.values():
        (output_dir / name).write_text("old result\n", encoding="utf-8")
    with workspace_scope(tmp_path):
        error = asyncio.run(postprocessing.analyze_all_postprocessing())
        assert "Frame 1" in error and "energy" in error
        assert run_registered("analyze_volume")["frames_processed"] == 2
    assert all((output_dir / name).read_text(encoding="utf-8") == "old result\n"
               for name in postprocessing._OUTPUT_NAMES.values())
    assert not list(output_dir.glob(".postprocessing-*"))


@pytest.mark.parametrize("options", [
    {"cutoff": 0}, {"cutoff": float("nan")}, {"number_of_bins": True},
    {"number_of_bins": 0}, {"reference_frame": 1}, {"unwrap": "true"},
    {"remove_com_drift": 1}, {"affine_mapping": "unknown"}, {"rmsd_cutoff": -1},
    {"end_frame": 0}, {"start_frame": 1, "end_frame": 1}, {"frame_step": False},
])
def test_all_rejects_invalid_options_before_starting_worker(trajectory, options):
    with workspace_scope(trajectory.parent):
        assert asyncio.run(postprocessing.analyze_all_postprocessing(**options)).startswith("Error:")
    assert not (trajectory.parent / "postprocessing").exists()


def test_msd_rejects_atom_count_change(trajectory):
    pytest.importorskip("ovito")
    with workspace_scope(trajectory.parent):
        error = asyncio.run(postprocessing.analyze_msd(unwrap=False))
    assert "unchanged atom count" in error
    assert not (trajectory.parent / "msd.csv").exists()


def test_missing_time_is_not_inherited_from_a_previous_frame(tmp_path):
    pytest.importorskip("ovito")
    (tmp_path / "dump.xyz").write_text(
        dump_frame([(1, 1, 1), (3, 1, 1)], time=20)
        + dump_frame([(2, 1, 1), (4, 1, 1)], time=None), encoding="utf-8",
    )
    with workspace_scope(tmp_path):
        result = run_registered("analyze_msd", {"unwrap": False})
    rows = csv_rows(result["output_file"])
    assert float(rows[0]["Time_fs"]) == 20
    assert rows[1]["Time_fs"] == rows[1]["Delta_time_fs"] == ""


def test_volume_requires_an_explicit_cell_on_every_frame(tmp_path):
    pytest.importorskip("ovito")
    missing_cell = dump_frame([(1, 1, 1), (3, 3, 3)], time=10).replace(
        'Lattice="10 0 0 0 10 0 0 0 10" ', "",
    )
    (tmp_path / "dump.xyz").write_text(
        dump_frame([(1, 1, 1), (3, 3, 3)]) + missing_cell, encoding="utf-8",
    )
    with workspace_scope(tmp_path):
        error = asyncio.run(postprocessing.analyze_volume())
    assert "Frame 1" in error and "cell" in error
    assert not (tmp_path / "volume.csv").exists()


def test_msd_triclinic_crossing_with_nonzero_reference_frame(tmp_path):
    pytest.importorskip("ovito")
    lattice = (10, 0, 0, 3, 10, 0, 0, 0, 10)
    # y fractional coordinate 0.2 adds x=0.6; the x boundary is crossed at frame 1.
    (tmp_path / "dump.xyz").write_text("".join(
        dump_frame([(x + 0.6, 2, 2), (x + 0.6, 3, 3)], lattice=lattice, time=i * 10)
        for i, x in enumerate((9, 1, 3))
    ), encoding="utf-8")
    with workspace_scope(tmp_path):
        result = run_registered("analyze_msd", {"start_frame": 1, "reference_frame": 1})
    rows = csv_rows(result["output_file"])
    assert float(rows[0]["MSD_A2"]) == pytest.approx(0)
    assert float(rows[-1]["MSD_A2"]) == pytest.approx(4)
    assert float(rows[-1]["Delta_time_fs"]) == 10


def test_all_cancel_kills_worker_before_removing_staged_results(trajectory, monkeypatch):
    async def run():
        started = asyncio.Event()
        process = SimpleNamespace(returncode=None, killed=False)

        def kill():
            process.killed = True
            process.returncode = -9

        async def communicate():
            if process.killed:
                return b"", b""
            started.set()
            await asyncio.Event().wait()

        async def launch(*args, **kwargs):
            payload = json.loads(args[-1])
            for path in payload["staged"].values():
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("partial\n")
            return process

        process.kill = kill
        process.communicate = communicate
        monkeypatch.setattr(postprocessing.asyncio, "create_subprocess_exec", launch)
        with workspace_scope(trajectory.parent):
            task = asyncio.create_task(postprocessing.analyze_all_postprocessing())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert process.killed

    asyncio.run(run())
    assert not list((trajectory.parent / "postprocessing").iterdir())


def test_publish_failure_rolls_back_already_replaced_csvs(tmp_path, monkeypatch):
    stage_dir = tmp_path / "staged"
    stage_dir.mkdir()
    destinations = {key: tmp_path / (key + ".csv") for key in ("energy", "rdf")}
    staged = {key: stage_dir / (key + ".csv") for key in destinations}
    for path in destinations.values():
        path.write_text("old\n", encoding="utf-8")
    for path in staged.values():
        path.write_text("new\n", encoding="utf-8")
    original = postprocessing.os.replace

    def replace(source, destination):
        if source == staged["rdf"]:
            raise PermissionError("output locked")
        return original(source, destination)

    monkeypatch.setattr(postprocessing.os, "replace", replace)
    with pytest.raises(PermissionError, match="locked"):
        postprocessing._publish_outputs(staged, destinations, stage_dir)
    assert all(path.read_text(encoding="utf-8") == "old\n" for path in destinations.values())


def write_plot_csv(path, columns, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def test_csv_summary_separates_last_from_extrema_and_selects_peaks_before_aggregating(tmp_path):
    write_plot_csv(tmp_path / "potential_energy.csv",
                   ["Frame", "Time_fs", "Potential_energy_eV", "Potential_energy_per_atom_eV"],
                   [(2, 100, -30, -3), (4, 200, -10, -1), (6, 300, -20, -2)])
    write_plot_csv(tmp_path / "volume.csv", ["Frame", "Time_fs", "Volume_A3", "Volume_per_atom_A3"],
                   [(2, 100, 100, 10), (4, 200, 120, 12), (6, 300, 110, 11)])
    write_plot_csv(tmp_path / "msd.csv",
                   ["Frame", "Time_fs", "Delta_time_fs", "Reference_frame", "MSD_A2", "MSD_x_A2", "MSD_y_A2", "MSD_z_A2"],
                   [(2, 100, 0, 2, 0, 0, 0, 0), (4, 200, 100, 2, .04, .02, .01, .01),
                    (6, 300, 200, 2, .02, .01, .005, .005)])
    write_plot_csv(tmp_path / "phase_fraction.csv",
                   ["Frame", "Other_fraction", "FCC_fraction", "HCP_fraction", "BCC_fraction", "ICO_fraction"],
                   [(2, 0, 1, 0, 0, 0), (4, .5, .5, 0, 0, 0), (6, 1, 0, 0, 0, 0)])
    write_plot_csv(tmp_path / "rdf.csv", ["Frame", "Time_fs", "Radius_A", "RDF"],
                   [(frame, time, radius, height)
                    for frame, time, heights in [(2, 100, [4.82, 4.97, 4.5]), (6, 300, [4.3, 4.9, 4.1])]
                    for radius, height in zip([4.125, 4.15, 4.175], heights)])
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.csv")}
    with workspace_scope(tmp_path):
        result = run_registered("summarize_postprocessing", {"rdf_peak_ranges": [[4.0, 4.3]]})
    assert set(result["summary"]) == set(postprocessing._OUTPUT_NAMES)
    metric = result["summary"]["msd"]["metrics"]["MSD_A2"]
    assert metric["last"]["value"] == .02
    assert metric["max"] == {"value": .04, "frame": 4, "time_fs": 200, "delta_time_fs": 100}
    assert metric["mean"] == pytest.approx(.02)
    assert metric["unit"] == "Å²"
    energy = result["summary"]["potential_energy"]["metrics"]["Potential_energy_eV"]
    assert energy["first"]["value"] == -30
    assert energy["min"]["frame"] == 2
    assert energy["max"]["frame"] == 4
    assert energy["mean"] == -20
    assert result["summary"]["volume"]["metrics"]["Volume_A3"]["mean"] == 110
    phase = result["summary"]["phase_fraction"]
    assert phase["time_missing_frames"] == 3
    assert phase["metrics"]["FCC_fraction"]["mean"] == .5
    peak = result["summary"]["rdf"]["peaks"][0]
    assert peak["found_frames"] == 2
    assert peak["missing_frames"] == 0
    assert peak["height"]["min"]["value"] == 4.9  # Not the lower bin beside a peak.
    assert peak["height"]["max"]["value"] == 4.97
    assert peak["radius_A"]["mean"] == 4.15
    assert len(csv_rows(result["rdf_peaks_file"])) == 2
    assert json.loads((tmp_path / "postprocessing_summary.json").read_text(encoding="utf-8")) == result
    assert all((tmp_path / name).read_bytes() == data for name, data in before.items())


def test_rdf_peak_rules_missing_peaks_plateaus_and_equal_height_ties(tmp_path):
    path = tmp_path / "rdf.csv"
    write_plot_csv(path, ["Frame", "Time_fs", "Radius_A", "RDF"],
                   [(frame, frame * 10, radius, height)
                    for frame, heights in [(0, [0, 3, 3, 0, 3, 0]), (1, [0, 1, 2, 3, 4, 5])]
                    for radius, height in enumerate(heights)])
    with workspace_scope(tmp_path):
        local = run_registered("summarize_postprocessing", {"rdf_peak_ranges": [[0, 5], [10, 11]]})
        window = run_registered("summarize_postprocessing", {
            "rdf_peak_ranges": [[0, 5]], "rdf_peak_rule": "window_maximum"})
    peak = local["summary"]["rdf"]["peaks"][0]
    assert peak["found_frames"] == peak["missing_frames"] == 1
    assert peak["radius_A"]["first"]["value"] == 1  # Lower middle of plateau; smaller radius wins ties.
    assert peak["height"]["mean"] == 3
    missing = local["summary"]["rdf"]["peaks"][1]
    assert missing["found_frames"] == 0
    assert missing["missing_frames"] == 2
    assert missing["height"]["mean"] is None
    assert missing["height"]["min"] is None
    assert window["summary"]["rdf"]["peaks"][0]["height"]["last"]["value"] == 5
    assert window["summary"]["rdf"]["peaks"][0]["boundary_frames"] == 1


@pytest.mark.parametrize("options", [
    {"rdf_peak_ranges": [[True, 3]]}, {"rdf_peak_ranges": [[2, 1]]},
    {"rdf_peak_ranges": [[0, float("nan")]]}, {"rdf_peak_ranges": [[-1, 2]]},
    {"rdf_peak_ranges": [[1, 2], [1, 2]]}, {"rdf_peak_ranges": [1, 2]},
    {"rdf_peak_rule": "first_guess"}, {"rdf_peak_ranges": [[0, 1]] * 9},
])
def test_summary_rejects_invalid_peak_options_without_writing(tmp_path, options):
    with workspace_scope(tmp_path):
        assert asyncio.run(postprocessing.summarize_postprocessing(**options)).startswith("Error:")
    assert not list(tmp_path.iterdir())


def test_summary_failure_preserves_existing_result_and_enforces_paths(tmp_path):
    path = tmp_path / "potential_energy.csv"
    write_plot_csv(path, ["Frame", "Potential_energy_eV", "Potential_energy_per_atom_eV"], [(0, -10, -1)])
    output = tmp_path / "postprocessing_summary.json"
    output.write_text("old result", encoding="utf-8")
    with workspace_scope(tmp_path):
        assert "RDF CSV" in asyncio.run(postprocessing.summarize_postprocessing(rdf_peak_ranges=[[1, 3]]))
        assert "allowed roots" in asyncio.run(postprocessing.summarize_postprocessing(output_file="../outside.json"))
        assert "overwrite" in asyncio.run(postprocessing.summarize_postprocessing(output_file="potential_energy.csv"))
        assert "duplicate CSV" in asyncio.run(postprocessing.summarize_postprocessing(csv_files=[path.name, path.name]))
        write_plot_csv(path, ["Frame", "Potential_energy_eV", "Potential_energy_per_atom_eV"], [(0, "nan", -1)])
        assert "non-finite" in asyncio.run(postprocessing.summarize_postprocessing())
    assert output.read_text(encoding="utf-8") == "old result"
    assert not list(tmp_path.glob(".postprocessing-*"))


def test_trajectory_analysis_publishes_summaries_and_peak_table_in_one_call(tmp_path):
    pytest.importorskip("ovito")
    path = tmp_path / "dump.xyz"
    path.write_text("".join(
        dump_frame([(1 + shift, 1, 1), (3 + shift, 1, 1)], energy=energy, time=time)
        for shift, energy, time in [(0, -10, 100), (.2, -12, 200), (.1, -11, 300)]
    ), encoding="utf-8")
    original = path.read_bytes()
    with workspace_scope(tmp_path):
        combined = run_registered("analyze_all_postprocessing", {"rdf_peak_ranges": [[1.8, 2.2]]})
        phase = run_registered("analyze_phase_fraction")
        rdf = run_registered("analyze_rdf", {"rdf_peak_ranges": [[1.8, 2.2]]})
    assert set(combined["output_files"]) == set(postprocessing._OUTPUT_NAMES)
    assert (tmp_path / "postprocessing/postprocessing_summary.json").is_file()
    assert len(csv_rows(combined["rdf_peaks_file"])) == 3
    metric = combined["summary"]["msd"]["metrics"]["MSD_A2"]
    assert metric["last"]["value"] == pytest.approx(.01)
    assert metric["max"]["value"] == pytest.approx(.04)
    assert metric["max"]["frame"] == 1
    assert metric["max"]["time_fs"] == 200
    assert metric["max"]["delta_time_fs"] == 100
    assert phase["summary"]["phase_fraction"]["frames"]["first"]["time_fs"] == 100
    assert phase["summary"]["phase_fraction"]["source_csv"] == phase["output_file"]
    assert json.loads((tmp_path / "phase_fraction.summary.json").read_text(encoding="utf-8")) == phase
    assert rdf["summary"]["rdf"]["peaks"] == combined["summary"]["rdf"]["peaks"]
    assert path.read_bytes() == original


def test_plot_custom_csv_and_incomplete_time_falls_back_to_frame(tmp_path):
    pytest.importorskip("matplotlib")
    write_plot_csv(tmp_path / "custom.csv", ["Frame", "Time_fs", "Potential_energy_eV", "Potential_energy_per_atom_eV"],
                   [(0, 0, -20, -2), (1, "", -21, -2.1)])
    before = (tmp_path / "custom.csv").read_bytes()
    with workspace_scope(tmp_path):
        result = run_registered("plot_postprocessing", {"csv_files": ["custom.csv"], "output_dir": "figures"})
    assert result["plots"][0]["x_axis"] == "Frame"
    assert result["plots"][0]["analysis"] == "potential_energy"
    assert result["output_files"] == [str(tmp_path / "figures" / "custom.png")]
    assert (tmp_path / "custom.csv").read_bytes() == before


def test_plot_rdf_subsamples_without_averaging_and_can_select_exact_frames(tmp_path):
    pytest.importorskip("matplotlib")
    write_plot_csv(tmp_path / "rdf.csv", ["Frame", "Radius_A", "RDF"],
                   [(frame, radius, frame / 10 + 1) for frame in range(12) for radius in (0.5, 1.5)])
    with workspace_scope(tmp_path):
        automatic = run_registered("plot_postprocessing")
        selected = run_registered("plot_postprocessing", {"rdf_frames": [3, 8]})
        before = (tmp_path / "rdf.png").read_bytes()
        error = asyncio.run(postprocessing.plot_postprocessing(rdf_frames=[100]))
    chosen = automatic["plots"][0]["plotted_frames"]
    assert len(chosen) == 8 and chosen[0] == 0 and chosen[-1] == 11
    assert selected["plots"][0]["plotted_frames"] == [3, 8]
    assert selected["plots"][0]["frames_in_csv"] == 12
    assert "requested RDF frames are missing" in error
    assert (tmp_path / "rdf.png").read_bytes() == before


def test_invalid_csv_preserves_all_previous_images(tmp_path):
    pytest.importorskip("matplotlib")
    write_plot_csv(tmp_path / "potential_energy.csv", ["Frame", "Potential_energy_eV", "Potential_energy_per_atom_eV"],
                   [(0, -10, -1)])
    write_plot_csv(tmp_path / "volume.csv", ["Frame", "Volume_A3", "Volume_per_atom_A3"],
                   [(0, "nan", 1)])
    for name in ("potential_energy.png", "volume.png"):
        (tmp_path / name).write_bytes(b"previous image")
    with workspace_scope(tmp_path):
        error = asyncio.run(postprocessing.plot_postprocessing())
    assert "non-finite Volume_A3" in error
    assert all((tmp_path / name).read_bytes() == b"previous image" for name in ("potential_energy.png", "volume.png"))
    assert not list(tmp_path.glob(".postprocessing-plot-*"))


@pytest.mark.parametrize("options", [
    {"dpi": True}, {"dpi": 0}, {"dpi": 601}, {"input_dir": ""},
    {"csv_files": []}, {"csv_files": "rdf.csv"}, {"rdf_frames": []},
    {"rdf_frames": [-1]}, {"rdf_frames": [True]}, {"rdf_frames": [1, 1]},
])
def test_plot_validates_arguments_without_creating_images(tmp_path, options):
    with workspace_scope(tmp_path):
        assert asyncio.run(postprocessing.plot_postprocessing(**options)).startswith("Error:")
    assert not list(tmp_path.iterdir())


def test_plot_missing_inputs_and_workspace_boundary(tmp_path):
    with workspace_scope(tmp_path):
        assert "no postprocessing CSVs" in asyncio.run(postprocessing.plot_postprocessing())
        assert "path not found" in asyncio.run(postprocessing.plot_postprocessing(csv_files=["missing.csv"]))
        assert "allowed roots" in asyncio.run(postprocessing.plot_postprocessing(output_dir="../outside"))


def test_plot_cancellation_kills_worker_and_cleans_temporary_files(tmp_path, monkeypatch):
    write_plot_csv(tmp_path / "rdf.csv", ["Frame", "Radius_A", "RDF"], [(0, 1, 1)])
    async def run():
        started = asyncio.Event()
        process = SimpleNamespace(returncode=None, killed=False)

        def kill():
            process.killed = True
            process.returncode = -9

        async def communicate():
            if process.killed:
                return b"", b""
            started.set()
            await asyncio.Event().wait()

        async def launch(*args, **kwargs):
            assert args[-2] == "--plot-worker"
            assert kwargs["env"]["MPLBACKEND"] == "Agg"
            if postprocessing.os.name == "nt":
                assert kwargs["creationflags"] == postprocessing.subprocess.CREATE_NO_WINDOW
            payload = json.loads(args[-1])
            for path in payload["staged"].values():
                with open(path, "wb") as handle:
                    handle.write(b"partial")
            return process

        process.kill, process.communicate = kill, communicate
        monkeypatch.setattr(postprocessing.asyncio, "create_subprocess_exec", launch)
        with workspace_scope(tmp_path):
            task = asyncio.create_task(postprocessing.plot_postprocessing())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert process.killed
    asyncio.run(run())
    assert not list(tmp_path.glob("*.png"))
    assert not list(tmp_path.glob(".postprocessing-plot-*"))
