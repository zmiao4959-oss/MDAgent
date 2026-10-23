"""GPUMD 轨迹后处理：PTM、势能、盒子体积、RDF、MSD 和后台 CSV 绘图。

所有 OVITO 计算都在独立进程主线程运行，避免 Qt 的线程限制。
长度、能量、时间沿用 GPUMD 的 Å、eV、fs；没有 Time 属性时留空，不猜时间。
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import csv
import json
import math
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile

from ..logger import get_logger
from .paths import resolve_workspace_path
from .registry import tool_registry

logger = get_logger(__name__)


@tool_registry.register(
    name="analyze_phase_fraction",
    schema={
        "type": "function",
        "function": {
            "name": "analyze_phase_fraction",
            "description": (
                "Use OVITO Polyhedral Template Matching (PTM) to analyze a GPUMD "
                "extended XYZ trajectory frame by frame. Write a CSV containing Frame, "
                "N_atoms and Other/FCC/HCP/BCC/ICO fractions (0 to 1). All four crystalline "
                "templates are explicitly enabled. These are local structure fractions, "
                "not a thermodynamic phase determination. Return the CSV path, a saved JSON "
                "summary, and per-structure first/last/min/max/mean with frame/time locations. "
                "Input files are never modified."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "input_file": {
                        "type": "string",
                        "description": "Trajectory path under the workspace; default: dump.xyz.",
                        "default": "dump.xyz",
                    },
                    "output_file": {
                        "type": "string",
                        "description": (
                            "CSV path relative to the active workspace or absolute within it. "
                            "If omitted, write phase_fraction.csv beside the trajectory. "
                            "An existing CSV is replaced only after successful analysis."
                        ),
                    },
                    "rmsd_cutoff": {
                        "type": "number",
                        "minimum": 0,
                        "default": 0.1,
                        "description": "PTM RMSD cutoff (dimensionless); 0 disables the cutoff.",
                    },
                    "start_frame": {
                        "type": "integer", "minimum": 0, "default": 0,
                        "description": "First frame to analyze, zero-based and inclusive.",
                    },
                    "end_frame": {
                        "type": "integer", "minimum": 1,
                        "description": "Exclusive end frame; omitted means all remaining frames.",
                    },
                    "frame_step": {
                        "type": "integer", "minimum": 1, "default": 1,
                        "description": "Analyze every Nth frame within the selected range.",
                    },
                },
            },
        },
    },
    risk_level="medium",
    tags=["execution", "postprocessing", "ovito"],
)
async def analyze_phase_fraction(
    input_file: str = "dump.xyz",
    output_file: str | None = None,
    rmsd_cutoff: float = 0.1,
    start_frame: int = 0,
    end_frame: int | None = None,
    frame_step: int = 1,
    **kwargs,
) -> str:
    """输出逐帧原子比例 CSV；帧区间采用 Python 的左闭右开约定。

    默认处理全部帧，比例取值为 0～1，每帧以该帧的原子总数为分母。
    在临时文件中计算，全部成功后才替换目标 CSV，避免留下半份结果。
    OVITO 在独立进程的主线程中运行，避免 Qt 在线程工具中的原生崩溃。
    取消工具时终止分析进程；numpy 和 ovito 仅在该进程中导入。
    """
    if not isinstance(input_file, str):
        return "Error: input_file must be a string"
    if output_file is not None and (not isinstance(output_file, str) or not output_file.strip()):
        return "Error: output_file must be a non-empty string"
    if (
        isinstance(rmsd_cutoff, bool)
        or not isinstance(rmsd_cutoff, (int, float))
        or not math.isfinite(rmsd_cutoff)
        or rmsd_cutoff < 0
    ):
        return "Error: rmsd_cutoff must be a finite non-negative number"
    for name, value, minimum in (
        ("start_frame", start_frame, 0),
        ("frame_step", frame_step, 1),
        ("end_frame", end_frame, 1),
    ):
        if name == "end_frame" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            return f"Error: {name} must be an integer >= {minimum}"

    trajectory, error = resolve_workspace_path(input_file, must_exist=True)
    if error:
        return error
    if not trajectory.is_file():
        return "Error: input_file must be a trajectory file"
    destination, error = resolve_workspace_path(
        output_file if output_file is not None else str(trajectory.with_name("phase_fraction.csv")),
        allow_create=True,
    )
    if error:
        return error
    if destination.exists() and destination.is_dir():
        return "Error: output_file must be a file, not a directory"
    if destination == trajectory or (
        destination.exists() and os.path.samefile(destination, trajectory)
    ):
        return "Error: output_file must not overwrite the input trajectory"

    temporary: Path | None = None
    process = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=destination.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
        payload = {
            "trajectory": str(trajectory), "destination": str(destination),
            "temporary": str(temporary), "rmsd_cutoff": float(rmsd_cutoff),
            "start_frame": start_frame, "end_frame": end_frame, "frame_step": frame_step,
        }
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        launch_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "miniclaw.tools.postprocessing", "--ptm-worker",
            json.dumps(payload), stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, **launch_options,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()[-1500:]
            return f"Error: PTM worker exited with code {process.returncode}: {detail}"
        result = json.loads(stdout.decode("utf-8"))
        if result.get("status") != "SUCCEEDED":
            return f"Error: PTM phase fraction analysis failed: {result.get('error', 'unknown error')}"
        summary_path = _summary_destination(destination.with_suffix(".summary.json"), [trajectory, destination])
        result["summary_file"] = str(summary_path)
        result["summary"]["phase_fraction"]["source_csv"] = str(destination)
        with tempfile.TemporaryDirectory(prefix=".postprocessing-summary-", dir=destination.parent) as stage:
            staged_summary = Path(stage) / "summary.json"
            staged_summary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
            _publish_outputs(
                {"csv": temporary, "summary": staged_summary},
                {"csv": destination, "summary": summary_path}, Path(stage),
            )
        temporary = None
        return json.dumps(result, ensure_ascii=False)
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
        raise
    except Exception as exc:
        logger.exception("PTM analysis failed for %s", trajectory)
        return f"Error: PTM phase fraction analysis failed: {exc}"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _compute_phase_fraction(
    trajectory: str,
    destination: str,
    temporary: str,
    rmsd_cutoff: float,
    start_frame: int,
    end_frame: int | None,
    frame_step: int,
) -> dict:
    """仅在分析子进程主线程执行；目标 CSV 由父进程确认成功后发布。"""
    try:
        import numpy as np
        from ovito.io import import_file
        from ovito.modifiers import PolyhedralTemplateMatchingModifier
    except ImportError as exc:
        return {"status": "FAILED", "error": f"PTM analysis requires numpy and ovito: {exc}"}

    try:
        pipeline = import_file(trajectory)
        total_frames = pipeline.source.num_frames
        stop = total_frames if end_frame is None else end_frame
        if not 0 <= start_frame < stop <= total_frames:
            raise ValueError(
                f"invalid frame range [{start_frame}, {stop}); "
                f"trajectory contains {total_frames} frame(s)"
            )

        names = ("Other", "FCC", "HCP", "BCC", "ICO")
        ptm = PolyhedralTemplateMatchingModifier(rmsd_cutoff=float(rmsd_cutoff))
        # OVITO 默认不开启 ICO；显式限定模板，保证五列覆盖所有原子。
        selected_types = {
            int(ptm.Type.OTHER), int(ptm.Type.FCC), int(ptm.Type.HCP),
            int(ptm.Type.BCC), int(ptm.Type.ICO),
        }
        for structure_type in ptm.structures:
            structure_type.enabled = structure_type.id in selected_types
        pipeline.modifiers.append(ptm)

        frames = range(start_frame, stop, frame_step)
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Frame", "N_atoms", *[name + "_fraction" for name in names]])
            for frame in frames:
                data = pipeline.compute(frame)
                if data.particles is None or "Structure Type" not in data.particles:
                    raise ValueError(f"Frame {frame}: PTM did not produce Structure Type")
                structure = np.asarray(data.particles["Structure Type"])
                n_atoms = len(structure)
                if n_atoms == 0:
                    raise ValueError(f"Frame {frame}: trajectory contains no atoms")
                counts = np.bincount(structure, minlength=len(names))
                if len(counts) != len(names):
                    raise ValueError(f"Frame {frame}: unexpected PTM structure type")
                fractions = counts / n_atoms
                writer.writerow([frame, n_atoms, *fractions.tolist()])
                logger.debug(
                    "PTM frame %s: atoms=%s FCC=%.2f%% HCP=%.2f%% BCC=%.2f%% ICO=%.2f%%",
                    frame, n_atoms, *(100 * fractions[index] for index in (1, 2, 3, 4)),
                )

        return {
            "status": "SUCCEEDED",
            "input_file": str(trajectory),
            "output_file": str(destination),
            "total_frames": total_frames,
            "frames_processed": len(frames),
            "start_frame": start_frame,
            "end_frame": stop,
            "frame_step": frame_step,
            "rmsd_cutoff": float(rmsd_cutoff),
            "structure_types": list(names),
            "summary": {"phase_fraction": _summarize_csv(
                temporary, frame_times=_read_xyz_metadata(trajectory, set(frames)),
            )},
            "last_frame": {
                "frame": frame,
                "n_atoms": n_atoms,
                "fractions": dict(zip(names, fractions.tolist())),
            },
        }
    except Exception as exc:
        return {"status": "FAILED", "error": str(exc)}


_OUTPUT_NAMES = {
    "phase_fraction": "phase_fraction.csv",
    "potential_energy": "potential_energy.csv",
    "volume": "volume.csv",
    "rdf": "rdf.csv",
    "msd": "msd.csv",
}
_FRAME_PROPERTIES = {
    "input_file": {"type": "string", "default": "dump.xyz",
                   "description": "GPUMD extended XYZ trajectory in the workspace."},
    "start_frame": {"type": "integer", "minimum": 0, "default": 0,
                    "description": "Inclusive first frame, zero-based."},
    "end_frame": {"type": "integer", "minimum": 1,
                  "description": "Exclusive end frame; omitted means end of trajectory."},
    "frame_step": {"type": "integer", "minimum": 1, "default": 1,
                   "description": "Output every Nth frame. MSD unwrapping still uses intervening frames."},
}
_RDF_PROPERTIES = {
    "cutoff": {"type": "number", "exclusiveMinimum": 0, "default": 5.0,
               "description": "RDF maximum radius in angstroms."},
    "number_of_bins": {"type": "integer", "minimum": 1, "default": 200,
                       "description": "Number of equally spaced RDF histogram bins."},
}
_RDF_PEAK_PROPERTIES = {
    "rdf_peak_ranges": {
        "type": "array", "maxItems": 8,
        "items": {"type": "array", "minItems": 2, "maxItems": 2,
                  "items": {"type": "number", "minimum": 0}},
        "description": (
            "Optional RDF search windows [[r_min,r_max], ...] in angstroms, inclusive. "
            "No windows means no peak identification. Select physical windows explicitly; "
            "window order does not imply coordination-shell order. Save per-frame peaks CSV "
            "and summarize peak radii/heights across frames. No interpolation or smoothing."
        ),
    },
    "rdf_peak_rule": {
        "type": "string", "enum": ["highest_local_maximum", "window_maximum"],
        "default": "highest_local_maximum",
        "description": (
            "highest_local_maximum selects the highest positive local maximum within each "
            "window, using neighboring bins from the full curve. Flat peaks use the lower "
            "middle bin; ties select smaller radius. Missing peaks are reported, never filled. "
            "window_maximum selects the highest positive bin in the window and flags boundary bins."
        ),
    },
}
_MSD_PROPERTIES = {
    "reference_frame": {"type": "integer", "minimum": 0, "default": 0,
                        "description": "Fixed reference frame, must be <= start_frame. This is not a time-origin average."},
    "unwrap": {"type": "boolean", "default": True,
               "description": "Unwrap periodic crossings using all saved frames. Without image flags, adjacent-frame motion must be resolvable (less than half a periodic cell). Set false for already unwrapped input."},
    "remove_com_drift": {"type": "boolean", "default": False,
                         "description": "Subtract mass-weighted center-of-mass displacement before MSD. Requires positive particle masses, read or inferred by OVITO from elements."},
    "affine_mapping": {"type": "string", "enum": ["off", "to_reference"], "default": "off",
                       "description": "off includes box deformation; to_reference maps current positions to the reference cell to remove homogeneous cell deformation."},
}


def _postprocessing_tool(name: str, description: str, extra: dict | None = None, *, all_outputs=False):
    """共享 schema 定义，工具仍有明确的函数签名，便于 agent 单独选择。"""
    output_key = "output_dir" if all_outputs else "output_file"
    properties = {
        **_FRAME_PROPERTIES,
        output_key: {
            "type": "string",
            "description": (
                "Output directory under the workspace; default: postprocessing/ beside the trajectory. "
                "Writes five CSVs. Existing files are replaced only after all analyses succeed."
                if all_outputs else
                "CSV path under the workspace; omitted means the analysis-named CSV beside the trajectory. "
                "An existing file is replaced only after success."
            ),
        },
        **(extra or {}),
    }
    return tool_registry.register(
        name=name,
        schema={"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "additionalProperties": False, "properties": properties},
        }},
        risk_level="medium", tags=["execution", "postprocessing", "ovito"],
    )


@_postprocessing_tool(
    "analyze_potential_energy",
    "Extract GPUMD frame-header energy into CSV: total potential energy (eV) and mean potential "
    "energy per atom (eV). Does not include kinetic energy or evaluate a potential from coordinates. "
    "Fails if any selected frame lacks a finite energy attribute. Includes frame, atom count and Time (fs) if stored. "
    "Return saved JSON summary and first/last/min/max/mean with frame/time locations, distinct from last_frame.",
)
async def analyze_potential_energy(
    input_file: str = "dump.xyz", output_file: str | None = None,
    start_frame: int = 0, end_frame: int | None = None, frame_step: int = 1, **kwargs,
) -> str:
    return await _run_postprocessing(
        ("potential_energy",), input_file, output_file, None, start_frame, end_frame, frame_step,
    )


@_postprocessing_tool(
    "analyze_volume",
    "Extract the 3D simulation cell volume |det(H)| in angstrom^3 and volume per atom for every "
    "selected GPUMD trajectory frame. This is box volume, including any vacuum, not material volume. "
    "Requires a valid nonzero 3D cell; writes CSV with frame, atom count and stored Time (fs). "
    "Return saved JSON summary and first/last/min/max/mean with frame/time locations.",
)
async def analyze_volume(
    input_file: str = "dump.xyz", output_file: str | None = None,
    start_frame: int = 0, end_frame: int | None = None, frame_step: int = 1, **kwargs,
) -> str:
    return await _run_postprocessing(
        ("volume",), input_file, output_file, None, start_frame, end_frame, frame_step,
    )


@_postprocessing_tool(
    "analyze_rdf",
    "Compute total RDF g(r) with OVITO CoordinationAnalysisModifier for each selected frame. "
    "Write a long-format CSV: Frame,N_atoms,Time_fs,Radius_A,RDF (one row per bin per frame). "
    "Uses simulation-cell density for normalization; vacuum and free surfaces affect g(r). "
    "This is a per-frame RDF, not a time average or element-pair partial RDF. "
    "Optional explicit search windows identify per-frame peaks and return cross-frame "
    "radius/height statistics, missing counts and a peaks CSV. Return a saved JSON summary.",
    {**_RDF_PROPERTIES, **_RDF_PEAK_PROPERTIES},
)
async def analyze_rdf(
    input_file: str = "dump.xyz", output_file: str | None = None,
    cutoff: float = 5.0, number_of_bins: int = 200,
    start_frame: int = 0, end_frame: int | None = None, frame_step: int = 1,
    rdf_peak_ranges: list[list[float]] | None = None,
    rdf_peak_rule: str = "highest_local_maximum", **kwargs,
) -> str:
    return await _run_postprocessing(
        ("rdf",), input_file, output_file, None, start_frame, end_frame, frame_step,
        cutoff=cutoff, number_of_bins=number_of_bins,
        rdf_peak_ranges=rdf_peak_ranges, rdf_peak_rule=rdf_peak_rule,
    )


@_postprocessing_tool(
    "analyze_msd",
    "Compute atom-averaged mean squared displacement relative to a fixed reference frame, in "
    "angstrom^2, including x/y/z components and stored elapsed time (fs). Default: unwrap "
    "periodic crossings, include drift and box deformation. Uses particle IDs if present; "
    "otherwise assumes GPUMD's unchanged atom order. Requires unchanged atom identities and count. "
    "Not a multiple-time-origin average; does not fit a diffusion coefficient. "
    "Return saved JSON summary: first/last/min/max/mean for total and components, with frame, "
    "stored time and elapsed-time locations. A last value is not a maximum.",
    _MSD_PROPERTIES,
)
async def analyze_msd(
    input_file: str = "dump.xyz", output_file: str | None = None,
    reference_frame: int = 0, unwrap: bool = True, remove_com_drift: bool = False,
    affine_mapping: str = "off", start_frame: int = 0, end_frame: int | None = None,
    frame_step: int = 1, **kwargs,
) -> str:
    return await _run_postprocessing(
        ("msd",), input_file, output_file, None, start_frame, end_frame, frame_step,
        reference_frame=reference_frame, unwrap=unwrap, remove_com_drift=remove_com_drift,
        affine_mapping=affine_mapping,
    )


@_postprocessing_tool(
    "analyze_all_postprocessing",
    "Analyze a GPUMD dump in one tool call and write all five CSVs: PTM local structure fractions "
    "(Other/FCC/HCP/BCC/ICO), total and per-atom mean potential energy, box volume, per-frame total "
    "RDF, and fixed-reference MSD with x/y/z components. Same definitions/options as the individual "
    "tools. Energy must be stored in the frame header; volume includes vacuum; RDF uses cell density; "
    "MSD assumes consistent atom identities and resolvable periodic crossings. Return output paths "
    "and a saved JSON summary with first/last/min/max/mean and frame/time locations. Optional RDF "
    "search windows produce per-frame peaks and cross-frame statistics. Publish only after all "
    "five calculations and their summaries succeed. Use an individual tool when "
    "only one quantity is needed.",
    {**_RDF_PROPERTIES, **_RDF_PEAK_PROPERTIES, **_MSD_PROPERTIES,
     "rmsd_cutoff": {"type": "number", "minimum": 0, "default": 0.1,
                     "description": "PTM RMSD cutoff; zero disables it."}},
    all_outputs=True,
)
async def analyze_all_postprocessing(
    input_file: str = "dump.xyz", output_dir: str | None = None,
    rmsd_cutoff: float = 0.1, cutoff: float = 5.0, number_of_bins: int = 200,
    reference_frame: int = 0, unwrap: bool = True, remove_com_drift: bool = False,
    affine_mapping: str = "off", start_frame: int = 0, end_frame: int | None = None,
    frame_step: int = 1, rdf_peak_ranges: list[list[float]] | None = None,
    rdf_peak_rule: str = "highest_local_maximum", **kwargs,
) -> str:
    return await _run_postprocessing(
        tuple(_OUTPUT_NAMES), input_file, None, output_dir, start_frame, end_frame, frame_step,
        rmsd_cutoff=rmsd_cutoff, cutoff=cutoff, number_of_bins=number_of_bins,
        reference_frame=reference_frame, unwrap=unwrap, remove_com_drift=remove_com_drift,
        affine_mapping=affine_mapping,
        rdf_peak_ranges=rdf_peak_ranges, rdf_peak_rule=rdf_peak_rule,
    )


@tool_registry.register(
    name="summarize_postprocessing",
    schema={"type": "function", "function": {
        "name": "summarize_postprocessing",
        "description": (
            "Summarize existing postprocessing CSVs without rerunning a trajectory or OVITO. "
            "Return and save JSON with first/last/min/max/mean, units, frame/time locations "
            "and missing-time counts. Select at most one CSV per analysis. Optional RDF search "
            "windows select a peak per frame, save a peaks CSV and summarize radii/heights "
            "across those selected peaks, not neighboring bins. Missing peaks stay missing. "
            "No time-averaged RDF, thermo.out parsing, diffusion fit or physical phase inference."
        ),
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "input_dir": {"type": "string", "default": ".",
                          "description": "Existing CSV directory in the workspace."},
            "csv_files": {"type": "array", "minItems": 1, "maxItems": 5,
                          "items": {"type": "string"},
                          "description": "CSV paths relative to input_dir; omitted: existing five standard CSV names. Custom names allowed, one file per analysis."},
            "output_file": {"type": "string",
                            "description": "JSON path relative to the workspace. Default: postprocessing_summary.json in input_dir. Atomic replacement after successful statistics."},
            **_RDF_PEAK_PROPERTIES,
        }},
    }},
    tags=["execution", "postprocessing"], risk_level="medium",
)
async def summarize_postprocessing(
    input_dir: str = ".", csv_files: list[str] | None = None,
    output_file: str | None = None, rdf_peak_ranges: list[list[float]] | None = None,
    rdf_peak_rule: str = "highest_local_maximum", **kwargs,
) -> str:
    error = _validate_peak_options(rdf_peak_ranges, rdf_peak_rule)
    if error:
        return error
    if not isinstance(input_dir, str) or not input_dir.strip():
        return "Error: input_dir must be a non-empty string"
    if output_file is not None and (not isinstance(output_file, str) or not output_file.strip()):
        return "Error: output_file must be a non-empty JSON path"
    if csv_files is not None and (
        not isinstance(csv_files, list) or not 1 <= len(csv_files) <= 5
        or any(not isinstance(p, str) or not p.strip() for p in csv_files)
    ):
        return "Error: csv_files must contain 1 to 5 CSV paths"
    directory, error = resolve_workspace_path(input_dir, must_exist=True)
    if error:
        return error
    if not directory.is_dir():
        return "Error: input_dir must be a directory"
    candidates = csv_files if csv_files is not None else [
        name for name in _OUTPUT_NAMES.values() if (directory / name).is_file()
    ]
    if not candidates:
        return "Error: no postprocessing CSVs found in input_dir"
    sources = {}
    for i, name in enumerate(candidates):
        path, error = resolve_workspace_path(str(directory / name), must_exist=True)
        if error:
            return error
        if not path.is_file() or path.suffix.lower() != ".csv":
            return "Error: csv_files must refer to CSV files"
        if path in sources.values():
            return "Error: duplicate CSV input"
        sources[str(i)] = path
    try:
        summary_path = _summary_destination(output_file or directory / "postprocessing_summary.json", list(sources.values()))
        if summary_path.suffix.lower() != ".json":
            return "Error: output_file must have a .json suffix"
        peaks_path = (_summary_destination(summary_path.with_suffix(".rdf_peaks.csv"),
                                          [summary_path, *sources.values()]) if rdf_peak_ranges else None)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".postprocessing-summary-", dir=summary_path.parent) as stage:
            staged, destinations = {}, {"summary": summary_path}
            if peaks_path:
                staged["rdf_peaks"] = Path(stage) / "peaks.csv"
                destinations["rdf_peaks"] = peaks_path
            result = await _run_worker("--summary-worker", {
                "sources": {k: str(v) for k, v in sources.items()},
                "rdf_peak_ranges": rdf_peak_ranges, "rdf_peak_rule": rdf_peak_rule,
                "peaks_output": str(staged["rdf_peaks"]) if peaks_path else None,
            })
            if result.get("status") != "SUCCEEDED":
                return f"Error: summary failed: {result.get('error', 'unknown error')}"
            result.update({"summary_file": str(summary_path), "input_files": [str(p) for p in sources.values()],
                           "options": {"rdf_peak_ranges": rdf_peak_ranges or [], "rdf_peak_rule": rdf_peak_rule}})
            if peaks_path:
                result["rdf_peaks_file"] = str(peaks_path)
            staged["summary"] = Path(stage) / "summary.json"
            staged["summary"].write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
            _publish_outputs(staged, destinations, Path(stage))
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return f"Error: summary failed: {exc}"


@tool_registry.register(
    name="plot_postprocessing",
    schema={"type": "function", "function": {
        "name": "plot_postprocessing",
        "description": (
            "Plot existing postprocessing CSVs as PNG files using the non-interactive Agg backend. "
            "Never opens a window or displays a figure. Supports PTM fractions, potential energy, "
            "box volume, per-frame RDF and MSD generated by the analysis tools. Auto-detect available "
            "standard CSVs in input_dir, or select csv_files (including custom filenames). Save PNGs "
            "in input_dir by default, one per CSV, with the same basename. Return saved file paths. "
            "Uses stored time when complete and increasing, otherwise frame index. RDF overlays "
            "up to eight evenly spaced frames by default, including first and last; no averaging."
        ),
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "input_dir": {"type": "string", "default": ".",
                          "description": "CSV directory in the workspace. Default: current active workspace. For analyze_all_postprocessing outputs, use its postprocessing directory."},
            "csv_files": {"type": "array", "minItems": 1, "items": {"type": "string"},
                          "description": "Optional CSV paths, relative to input_dir or absolute within allowed roots. Omitted: detect the five standard filenames that exist."},
            "output_dir": {"type": "string",
                           "description": "PNG directory relative to the active workspace; default: input_dir. Existing images are replaced only after all plots succeed."},
            "dpi": {"type": "integer", "minimum": 72, "maximum": 600, "default": 180,
                    "description": "PNG resolution in dots per inch."},
            "rdf_frames": {"type": "array", "minItems": 1, "uniqueItems": True,
                           "items": {"type": "integer", "minimum": 0},
                           "description": "Optional exact RDF frame indices to overlay. Default: at most eight representative frames. Must exist in every selected RDF CSV."},
        }},
    }},
    risk_level="medium", tags=["execution", "postprocessing", "plotting"],
)
async def plot_postprocessing(
    input_dir: str = ".", csv_files: list[str] | None = None,
    output_dir: str | None = None, dpi: int = 180,
    rdf_frames: list[int] | None = None, **kwargs,
) -> str:
    """读取已有 CSV，在后台保存 PNG；不运行新的轨迹分析，不弹出绘图窗口。"""
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 600:
        return "Error: dpi must be an integer between 72 and 600"
    for name, value in (("input_dir", input_dir), ("output_dir", output_dir)):
        if (name == "input_dir" or value is not None) and (not isinstance(value, str) or not value.strip()):
            return f"Error: {name} must be a non-empty string"
    if csv_files is not None and (
        not isinstance(csv_files, list) or not csv_files
        or any(not isinstance(value, str) or not value.strip() for value in csv_files)
    ):
        return "Error: csv_files must be a non-empty list of CSV paths"
    if rdf_frames is not None and (
        not isinstance(rdf_frames, list) or not rdf_frames
        or any(isinstance(frame, bool) or not isinstance(frame, int) or frame < 0 for frame in rdf_frames)
        or len(set(rdf_frames)) != len(rdf_frames)
    ):
        return "Error: rdf_frames must contain unique non-negative integer frame indices"
    directory, error = resolve_workspace_path(input_dir, must_exist=True)
    if error:
        return error
    if not directory.is_dir():
        return "Error: input_dir must be a directory"
    output, error = resolve_workspace_path(output_dir or str(directory), allow_create=True)
    if error:
        return error
    if output.exists() and not output.is_dir():
        return "Error: output_dir must be a directory"
    candidates = csv_files if csv_files is not None else [
        name for name in _OUTPUT_NAMES.values() if (directory / name).is_file()
    ]
    if not candidates:
        return "Error: no postprocessing CSVs found in input_dir"
    sources, destinations = {}, {}
    for index, name in enumerate(candidates):
        path, error = resolve_workspace_path(str(directory / name), must_exist=True)
        if error:
            return error
        if not path.is_file() or path.suffix.lower() != ".csv":
            return "Error: csv_files must refer to CSV files"
        if path in sources.values():
            return "Error: duplicate CSV input"
        destination, error = resolve_workspace_path(str(output / (path.stem + ".png")), allow_create=True)
        if error:
            return error
        if destination.exists() and not destination.is_file():
            return "Error: PNG destination must be a file, not a directory"
        if destination in destinations.values():
            return "Error: CSV basenames collide in output_dir; plot them separately"
        key = f"plot_{index}"
        sources[key], destinations[key] = path, destination
    if any(destination == source or (destination.exists() and source.exists() and os.path.samefile(destination, source))
           for destination in destinations.values() for source in sources.values()):
        return "Error: PNG destination must not overwrite an input CSV"
    try:
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".postprocessing-plot-", dir=output) as temp_dir:
            staged = {key: Path(temp_dir) / (key + ".png") for key in sources}
            result = await _run_worker("--plot-worker", {
                "sources": {k: str(v) for k, v in sources.items()},
                "staged": {k: str(v) for k, v in staged.items()},
                "dpi": dpi, "rdf_frames": rdf_frames,
            })
            if result.get("status") != "SUCCEEDED":
                return f"Error: plotting failed: {result.get('error', 'unknown error')}"
            _publish_outputs(staged, destinations, Path(temp_dir))
        plots = []
        for key, info in result.pop("plots").items():
            plots.append({"input_file": str(sources[key]), "output_file": str(destinations[key]), **info})
        result.update({"plots": plots, "output_files": [str(p) for p in destinations.values()]})
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Postprocessing plotting failed")
        return f"Error: plotting failed: {exc}"


async def _run_worker(mode: str, payload: dict) -> dict:
    """隐藏的可取消子进程；在调用方清理暂存文件前终止原生计算。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["MPLBACKEND"] = "Agg"
    launch_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "miniclaw.tools.postprocessing", mode,
            json.dumps(payload), stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, **launch_options,
        )
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
        raise
    if process.returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()[-1500:]
        raise RuntimeError(f"postprocessing worker exited with code {process.returncode}: {detail}")
    return json.loads(stdout.decode("utf-8"))


def _validate_analysis_options(start_frame, end_frame, frame_step, options):
    error = _validate_peak_options(options.get("rdf_peak_ranges"), options.get("rdf_peak_rule", "highest_local_maximum"))
    if error:
        return error
    for name, value, minimum in (
        ("start_frame", start_frame, 0), ("end_frame", end_frame, 1),
        ("frame_step", frame_step, 1), ("number_of_bins", options.get("number_of_bins", 200), 1),
        ("reference_frame", options.get("reference_frame", 0), 0),
    ):
        if name == "end_frame" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            return f"Error: {name} must be an integer >= {minimum}"
    if end_frame is not None and end_frame <= start_frame:
        return "Error: end_frame must be greater than start_frame"
    for name, minimum in (("cutoff", 0), ("rmsd_cutoff", 0)):
        value = options.get(name, 5.0 if name == "cutoff" else 0.1)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < minimum or (name == "cutoff" and value == 0)):
            return f"Error: {name} must be finite and {'positive' if name == 'cutoff' else 'non-negative'}"
    for name in ("unwrap", "remove_com_drift"):
        if name in options and not isinstance(options[name], bool):
            return f"Error: {name} must be a boolean"
    if options.get("affine_mapping", "off") not in ("off", "to_reference"):
        return "Error: affine_mapping must be off or to_reference"
    if options.get("reference_frame", 0) > start_frame:
        return "Error: reference_frame must be <= start_frame"
    return None


def _validate_peak_options(ranges, rule):
    if rule not in ("highest_local_maximum", "window_maximum"):
        return "Error: rdf_peak_rule must be highest_local_maximum or window_maximum"
    if ranges is None:
        return None
    if not isinstance(ranges, list) or len(ranges) > 8:
        return "Error: rdf_peak_ranges must contain at most 8 [r_min, r_max] windows"
    seen = set()
    for window in ranges:
        if (not isinstance(window, list) or len(window) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in window)
                or not 0 <= window[0] < window[1]):
            return "Error: each RDF peak window must be finite [r_min, r_max] with 0 <= r_min < r_max"
        if tuple(window) in seen:
            return "Error: RDF peak windows must be unique"
        seen.add(tuple(window))
    return None


def _summary_destination(path, protected):
    destination, error = resolve_workspace_path(str(path), allow_create=True)
    if error:
        raise ValueError(error)
    if destination.exists() and not destination.is_file():
        raise ValueError("summary output must be a file")
    if any(destination == source or (destination.exists() and source.exists() and os.path.samefile(destination, source))
           for source in protected):
        raise ValueError("summary output must not overwrite an input or another output")
    return destination


class _SeriesSummary:
    """流式统计；极值保留首次出现的位置，明确区分末值和最大值。"""

    def __init__(self, unit):
        self.unit, self.count, self.mean = unit, 0, 0.0
        self.first = self.last = self.minimum = self.maximum = None

    def add(self, value, location):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("summary requires finite values")
        point = {"value": value, **location}
        self.count += 1
        self.mean += (value - self.mean) / self.count
        if self.first is None:
            self.first = point
        self.last = point
        if self.minimum is None or value < self.minimum["value"]:
            self.minimum = point
        if self.maximum is None or value > self.maximum["value"]:
            self.maximum = point

    def result(self):
        if not math.isfinite(self.mean):
            raise ValueError("non-finite summary mean")
        return {"unit": self.unit, "sample_count": self.count, "first": self.first,
                "last": self.last, "min": self.minimum, "max": self.maximum,
                "mean": self.mean if self.count else None}


def _rdf_peak(curve, window, rule):
    """在明确的窗口内选峰；局部峰邻点来自完整曲线，平顶取较小的中间 bin。"""
    eligible = [i for i, (r, _) in enumerate(curve) if window[0] <= r <= window[1]]
    if not eligible:
        return None
    if rule == "window_maximum":
        candidates = eligible
    else:
        candidates = []
        i = 1
        while i < len(curve) - 1:
            end = i
            while end + 1 < len(curve) and curve[end + 1][1] == curve[i][1]:
                end += 1
            middle = (i + end) // 2
            if (end < len(curve) - 1 and curve[i][1] > curve[i - 1][1]
                    and curve[end][1] > curve[end + 1][1] and middle in eligible):
                candidates.append(middle)
            i = end + 1
    candidates = [i for i in candidates if curve[i][1] > 0]
    if not candidates:
        return None
    index = max(candidates, key=lambda i: (curve[i][1], -curve[i][0]))
    radius, height = curve[index]
    return {"radius_A": radius, "height": height,
            "at_window_boundary": index in (eligible[0], eligible[-1])}


_SUMMARY_UNITS = {
    "Potential_energy_eV": "eV", "Potential_energy_per_atom_eV": "eV/atom",
    "Volume_A3": "Å³", "Volume_per_atom_A3": "Å³/atom", "RDF": "1",
    **{name + "_fraction": "1" for name in ("Other", "FCC", "HCP", "BCC", "ICO")},
    **{name: "Å²" for name in ("MSD_A2", "MSD_x_A2", "MSD_y_A2", "MSD_z_A2")},
}


def _summarize_csv(path, *, frame_times=None, rdf_peak_ranges=None,
                   rdf_peak_rule="highest_local_maximum", peaks_writer=None):
    """逐行读取 CSV，只保留当前 RDF 帧；不重新读取轨迹或运行 OVITO。"""
    metrics, peaks = {}, []
    for window in rdf_peak_ranges or []:
        peaks.append({"range_A": list(window), "radius": _SeriesSummary("Å"),
                      "height": _SeriesSummary("1"), "missing": 0, "boundary": 0})
    previous_frame = first_location = last_location = None
    frame_count = available_times = 0
    curve = []
    kind = None

    def finish_curve():
        for index, peak in enumerate(peaks):
            found = _rdf_peak(curve, peak["range_A"], rdf_peak_rule)
            if found is None:
                peak["missing"] += 1
            else:
                peak["radius"].add(found["radius_A"], last_location)
                peak["height"].add(found["height"], {**last_location, "radius_A": found["radius_A"]})
                peak["boundary"] += int(found["at_window_boundary"])
            if peaks_writer is not None:
                peaks_writer.writerow([
                    last_location["frame"], last_location["time_fs"], index,
                    *peak["range_A"], rdf_peak_rule, found is not None,
                    found["radius_A"] if found else "", found["height"] if found else "",
                    found["at_window_boundary"] if found else "",
                ])

    for kind, row in _plot_csv_rows(path):
        frame = int(row["Frame"])
        time = row.get("Time_fs")
        if time is None and frame_times is not None:
            stored = _stored_time(frame_times.get(frame, {}))
            time = None if stored == "" else stored
        location = {"frame": frame, "time_fs": time}
        if "Delta_time_fs" in row:
            location["delta_time_fs"] = row["Delta_time_fs"]
        if previous_frame is not None and frame < previous_frame:
            raise ValueError(f"{Path(path).name}: frames must be in increasing order")
        if frame != previous_frame:
            if curve:
                finish_curve()
                curve = []
            frame_count += 1
            available_times += int(time is not None)
            first_location = first_location or location
            last_location = location
            previous_frame = frame
        elif kind != "rdf":
            raise ValueError(f"{Path(path).name}: duplicate frame")
        elif time != last_location["time_fs"]:
            raise ValueError(f"{Path(path).name}: inconsistent time within an RDF frame")
        if kind == "rdf":
            radius, value = row["Radius_A"], row["RDF"]
            if radius < 0 or value < 0 or (curve and radius <= curve[-1][0]):
                raise ValueError(f"{Path(path).name}: RDF radii must increase and RDF must be non-negative")
            curve.append((radius, value))
            metric_location = {**location, "radius_A": radius}
        else:
            metric_location = location
        for column in _PLOT_SERIES[kind]:
            if column not in _SUMMARY_UNITS:
                continue
            value = row[column]
            if ((column.endswith("_fraction") and not 0 <= value <= 1)
                    or (column.startswith("MSD_") and value < 0)
                    or (column.startswith("Volume_") and value <= 0)):
                raise ValueError(f"{Path(path).name}: invalid {column}")
            metrics.setdefault(column, _SeriesSummary(_SUMMARY_UNITS[column])).add(value, metric_location)
    if kind is None:
        raise ValueError(f"{Path(path).name}: empty CSV")
    if curve:
        finish_curve()
    result = {
        "analysis": kind, "source_csv": str(path),
        "frames": {"count": frame_count, "first": first_location, "last": last_location},
        "time_available_frames": available_times, "time_missing_frames": frame_count - available_times,
        "metrics": {name: metric.result() for name, metric in metrics.items()},
        "averaging": "unweighted mean of selected frames" if kind != "rdf" else
                     "RDF metrics cover all selected bins; this is not a time-averaged RDF curve",
        "extrema_ties": "first occurrence",
    }
    if kind == "rdf":
        result["peak_rule"] = rdf_peak_rule
        result["peaks"] = [
            {"range_A": peak["range_A"], "found_frames": peak["radius"].count,
             "missing_frames": peak["missing"], "boundary_frames": peak["boundary"],
             "radius_A": peak["radius"].result(), "height": peak["height"].result()}
            for peak in peaks
        ]
    return result


def _compute_csv_summaries(sources, rdf_peak_ranges=None, rdf_peak_rule="highest_local_maximum",
                           peaks_output=None, frame_times=None):
    try:
        summaries = {}
        with ExitStack() as stack:
            writer = None
            if peaks_output:
                writer = csv.writer(stack.enter_context(open(peaks_output, "w", encoding="utf-8", newline="")))
                writer.writerow(["Frame", "Time_fs", "Range_index", "Radius_min_A", "Radius_max_A",
                                 "Rule", "Found", "Peak_radius_A", "Peak_height", "At_window_boundary"])
            for path in sources.values():
                summary = _summarize_csv(
                    path, frame_times=frame_times, rdf_peak_ranges=rdf_peak_ranges,
                    rdf_peak_rule=rdf_peak_rule, peaks_writer=writer,
                )
                kind = summary["analysis"]
                if kind in summaries:
                    raise ValueError("Select at most one CSV per analysis in one summary call")
                summaries[kind] = summary
        if rdf_peak_ranges and "rdf" not in summaries:
            raise ValueError("RDF peak windows require an RDF CSV")
        return {"status": "SUCCEEDED", "summary": summaries}
    except Exception as exc:
        return {"status": "FAILED", "error": str(exc)}


def _publish_outputs(staged: dict[str, Path], destinations: dict[str, Path], stage_dir: Path):
    """先备份旧结果；若发布其中一个 CSV 失败，恢复已经发布的 CSV。"""
    backups = {}
    for key, destination in destinations.items():
        if destination.exists():
            backup = stage_dir / (key + ".backup")
            shutil.copy2(destination, backup)
            backups[key] = backup
    published = []
    try:
        for key, destination in destinations.items():
            os.replace(staged[key], destination)
            published.append(key)
    except Exception:
        for key in reversed(published):
            if key in backups:
                os.replace(backups[key], destinations[key])
            else:
                destinations[key].unlink(missing_ok=True)
        raise


async def _run_postprocessing(
    analyses, input_file, output_file, output_dir, start_frame, end_frame, frame_step, **options,
) -> str:
    error = _validate_analysis_options(start_frame, end_frame, frame_step, options)
    if error:
        return error
    if any(window[1] > options.get("cutoff", 5.0) for window in options.get("rdf_peak_ranges") or []):
        return "Error: RDF peak windows must not exceed cutoff"
    if not isinstance(input_file, str) or not input_file.strip():
        return "Error: input_file must be a non-empty string"
    for name, value in (("output_file", output_file), ("output_dir", output_dir)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return f"Error: {name} must be a non-empty string"
    trajectory, error = resolve_workspace_path(input_file, must_exist=True)
    if error:
        return error
    if not trajectory.is_file():
        return "Error: input_file must be a trajectory file"
    if len(analyses) == 1:
        paths = {analyses[0]: output_file or str(trajectory.with_name(_OUTPUT_NAMES[analyses[0]]))}
    else:
        directory, error = resolve_workspace_path(
            output_dir or str(trajectory.parent / "postprocessing"), allow_create=True,
        )
        if error:
            return error
        if directory.exists() and not directory.is_dir():
            return "Error: output_dir must be a directory"
        paths = {key: str(directory / _OUTPUT_NAMES[key]) for key in analyses}
    destinations = {}
    for key, path in paths.items():
        destination, error = resolve_workspace_path(path, allow_create=True)
        if error:
            return error
        if destination.exists() and not destination.is_file():
            return "Error: output_file must be a file, not a directory"
        if destination == trajectory or (destination.exists() and os.path.samefile(destination, trajectory)):
            return "Error: output_file must not overwrite the input trajectory"
        destinations[key] = destination
    try:
        parent = next(iter(destinations.values())).parent
        summary_path = _summary_destination(
            next(iter(destinations.values())).with_suffix(".summary.json") if len(analyses) == 1
            else parent / "postprocessing_summary.json", [trajectory, *destinations.values()],
        )
        peaks_path = None
        if "rdf" in analyses and options.get("rdf_peak_ranges"):
            peaks_path = _summary_destination(
                destinations["rdf"].with_suffix(".peaks.csv"),
                [trajectory, summary_path, *destinations.values()],
            )
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".postprocessing-", dir=parent) as temp_dir:
            staged = {key: Path(temp_dir) / _OUTPUT_NAMES[key] for key in analyses}
            staged_peaks = Path(temp_dir) / "rdf.peaks.csv" if peaks_path else None
            payload = {
                "trajectory": str(trajectory), "staged": {k: str(v) for k, v in staged.items()},
                "start_frame": start_frame, "end_frame": end_frame, "frame_step": frame_step,
                "options": options,
                "peaks_output": str(staged_peaks) if staged_peaks else None,
            }
            result = await _run_worker("--analysis-worker", payload)
            if result.get("status") != "SUCCEEDED":
                return f"Error: postprocessing failed: {result.get('error', 'unknown error')}"
            result["input_file"] = str(trajectory)
            result["output_files"] = {k: str(v) for k, v in destinations.items()}
            result["summary_file"] = str(summary_path)
            for kind, summary in result["summary"].items():
                summary["source_csv"] = str(destinations[kind])
            if len(analyses) == 1:
                result["output_file"] = str(next(iter(destinations.values())))
            if peaks_path:
                result["rdf_peaks_file"] = str(peaks_path)
                staged["rdf_peaks"], destinations["rdf_peaks"] = staged_peaks, peaks_path
            staged["summary"] = Path(temp_dir) / "summary.json"
            staged["summary"].write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
            destinations["summary"] = summary_path
            _publish_outputs(staged, destinations, Path(temp_dir))
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Trajectory postprocessing failed for %s", trajectory)
        return f"Error: postprocessing failed: {exc}"


def _compute_analyses(trajectory, staged, start_frame, end_frame, frame_step, options, peaks_output=None) -> dict:
    """子进程入口；所有结果只写暂存路径，不直接修改用户结果。"""
    try:
        import numpy as np
        from ovito.io import import_file
        from ovito.modifiers import (
            CalculateDisplacementsModifier, CoordinationAnalysisModifier,
            PolyhedralTemplateMatchingModifier, UnwrapTrajectoriesModifier,
        )
        from ovito.pipeline import Pipeline

        pipeline = import_file(trajectory)
        total_frames = pipeline.source.num_frames
        stop = total_frames if end_frame is None else end_frame
        if not 0 <= start_frame < stop <= total_frames:
            raise ValueError(f"invalid frame range [{start_frame}, {stop}); trajectory contains {total_frames} frame(s)")
        frames = range(start_frame, stop, frame_step)
        notes = []
        reference = None
        msd_pipeline = None
        reference_frame = options.get("reference_frame", 0)
        # OVITO 的 source attributes 可能为缺失属性提供前一帧/首帧的值。
        # 势能和物理时间必须来自当前帧的实际帧头，不能信任这种回填。
        metadata_frames = set(frames)
        if "msd" in staged:
            metadata_frames.add(reference_frame)
        metadata = _read_xyz_metadata(trajectory, metadata_frames)
        if "msd" in staged:
            if total_frames < 2:
                raise ValueError("MSD requires a trajectory containing at least two frames")
            reference = pipeline.compute(reference_frame)
            msd_pipeline = Pipeline(source=pipeline.source)
            if options.get("unwrap", True):
                msd_pipeline.modifiers.append(UnwrapTrajectoriesModifier())
                notes.append("MSD unwrapping uses all saved frames; without image flags, crossings must be resolvable between adjacent saved frames.")
            displacement = CalculateDisplacementsModifier(
                reference_frame=reference_frame, minimum_image_convention=False,
            )
            displacement.affine_mapping = (
                displacement.AffineMapping.ToReference if options.get("affine_mapping", "off") == "to_reference"
                else displacement.AffineMapping.Off
            )
            msd_pipeline.modifiers.append(displacement)
            notes.append("MSD is an atom average relative to one fixed reference frame, not a time-origin average.")
            if reference.particles is None or reference.particles.count == 0:
                raise ValueError("MSD reference contains no atoms")
            if "Particle Identifier" not in reference.particles:
                notes.append("No particle IDs: MSD assumes unchanged GPUMD atom order, including atoms of the same element.")
        if "phase_fraction" in staged:
            ptm = PolyhedralTemplateMatchingModifier(rmsd_cutoff=options.get("rmsd_cutoff", 0.1))
            for item in ptm.structures:
                item.enabled = item.id in (0, 1, 2, 3, 4)
            pipeline.modifiers.append(ptm)
        if "rdf" in staged:
            pipeline.modifiers.append(CoordinationAnalysisModifier(
                cutoff=options.get("cutoff", 5.0), number_of_bins=options.get("number_of_bins", 200),
                partial=False,
            ))
            notes.append("RDF is per frame and normalized using simulation-cell density; vacuum/free surfaces affect normalization.")
        if "volume" in staged:
            notes.append("Volume is the simulation-cell volume, including vacuum.")
        columns = {
            "phase_fraction": ["Frame", "N_atoms", "Other_fraction", "FCC_fraction", "HCP_fraction", "BCC_fraction", "ICO_fraction"],
            "potential_energy": ["Frame", "N_atoms", "Time_fs", "Potential_energy_eV", "Potential_energy_per_atom_eV"],
            "volume": ["Frame", "N_atoms", "Time_fs", "Volume_A3", "Volume_per_atom_A3"],
            "rdf": ["Frame", "N_atoms", "Time_fs", "Radius_A", "RDF"],
            "msd": ["Frame", "N_atoms", "Time_fs", "Reference_frame", "Delta_time_fs", "MSD_A2", "MSD_x_A2", "MSD_y_A2", "MSD_z_A2"],
        }
        last_frame = {}
        with ExitStack() as stack:
            writers = {}
            for key, path in staged.items():
                writers[key] = csv.writer(stack.enter_context(open(path, "w", encoding="utf-8", newline="")))
                writers[key].writerow(columns[key])
            for frame in frames:
                data = pipeline.compute(frame)
                if data.particles is None or data.particles.count == 0:
                    raise ValueError(f"Frame {frame}: trajectory contains no atoms")
                n_atoms = data.particles.count
                time = _stored_time(metadata[frame])
                prefix = [frame, n_atoms, time]
                if "potential_energy" in writers:
                    energy = metadata[frame].get("energy")
                    if energy is None or not math.isfinite(float(energy)):
                        raise ValueError(f"Frame {frame}: missing or non-finite GPUMD frame-header energy; cannot reconstruct potential energy from coordinates")
                    energy = float(energy)
                    writers["potential_energy"].writerow([*prefix, energy, energy / n_atoms])
                    last_frame["potential_energy"] = {"total_eV": energy, "per_atom_eV": energy / n_atoms}
                if "volume" in writers or "rdf" in writers:
                    if ("Lattice" not in metadata[frame] or data.cell is None or data.cell.is2D
                            or not math.isfinite(data.cell.volume) or data.cell.volume <= 0):
                        raise ValueError(f"Frame {frame}: a valid nonzero 3D simulation cell is required")
                if "volume" in writers:
                    volume = float(data.cell.volume)
                    writers["volume"].writerow([*prefix, volume, volume / n_atoms])
                    last_frame["volume"] = {"volume_A3": volume, "per_atom_A3": volume / n_atoms}
                if "phase_fraction" in writers:
                    structure = np.asarray(data.particles["Structure Type"])
                    counts = np.bincount(structure, minlength=5)
                    if len(counts) != 5:
                        raise ValueError(f"Frame {frame}: unexpected PTM structure type")
                    fractions = (counts / n_atoms).tolist()
                    writers["phase_fraction"].writerow([frame, n_atoms, *fractions])
                    last_frame["phase_fraction"] = dict(zip(("Other", "FCC", "HCP", "BCC", "ICO"), fractions))
                if "rdf" in writers:
                    rdf = np.asarray(data.tables["coordination-rdf"].xy())
                    if rdf.shape != (options.get("number_of_bins", 200), 2) or not np.isfinite(rdf).all():
                        raise ValueError(f"Frame {frame}: invalid RDF histogram")
                    writers["rdf"].writerows([*prefix, float(radius), float(value)] for radius, value in rdf)
                    last_frame["rdf"] = {"bins": len(rdf), "cutoff_A": options.get("cutoff", 5.0)}
                if "msd" in writers:
                    _check_msd_identity(reference, data, frame)
                    msd_data = msd_pipeline.compute(frame)
                    vectors = np.array(msd_data.particles["Displacement"], dtype=float, copy=True)
                    if options.get("remove_com_drift", False):
                        masses = _particle_masses(msd_data)
                        vectors -= np.average(vectors, axis=0, weights=masses)
                    if not np.isfinite(vectors).all():
                        raise ValueError(f"Frame {frame}: non-finite displacements")
                    components = np.mean(vectors * vectors, axis=0)
                    msd = float(components.sum())
                    reference_time = _stored_time(metadata[reference_frame])
                    delta_time = time - reference_time if time != "" and reference_time != "" else ""
                    writers["msd"].writerow([*prefix, reference_frame, delta_time, msd, *components.tolist()])
                    last_frame["msd"] = {"msd_A2": msd, "components_A2": components.tolist()}
        if "msd" in staged and "Time" not in metadata[reference_frame]:
            notes.append("Reference Time is absent: elapsed time is blank; no timestep is inferred.")
        summaries = _compute_csv_summaries(
            staged, options.get("rdf_peak_ranges"), options.get("rdf_peak_rule", "highest_local_maximum"),
            peaks_output=peaks_output, frame_times=metadata,
        )
        if summaries["status"] != "SUCCEEDED":
            raise ValueError(summaries["error"])
        return {
            "status": "SUCCEEDED", "total_frames": total_frames, "frames_processed": len(frames),
            "start_frame": start_frame, "end_frame": stop, "frame_step": frame_step,
            "columns": {k: columns[k] for k in staged}, "options": options,
            "last_frame": {"frame": frame, "n_atoms": n_atoms, **last_frame}, "notes": notes,
            "summary": summaries["summary"],
        }
    except ImportError as exc:
        return {"status": "FAILED", "error": f"Postprocessing requires numpy and ovito: {exc}"}
    except Exception as exc:
        return {"status": "FAILED", "error": str(exc)}


def _read_xyz_metadata(trajectory, requested_frames):
    """只保留所需帧的实际属性；逐行跳过原子块，不把大轨迹载入内存。"""
    result = {}
    last = max(requested_frames)
    with open(trajectory, encoding="utf-8-sig") as handle:
        for frame in range(last + 1):
            line = handle.readline()
            while line and not line.strip():
                line = handle.readline()
            if not line:
                raise ValueError(f"Frame {frame}: missing XYZ atom count")
            count = int(line.strip())
            if count <= 0:
                raise ValueError(f"Frame {frame}: trajectory contains no atoms")
            header = handle.readline()
            if not header:
                raise ValueError(f"Frame {frame}: missing XYZ frame header")
            if frame in requested_frames:
                values = {}
                for token in shlex.split(header):
                    key, separator, value = token.partition("=")
                    if separator and key in ("energy", "Time", "Lattice"):
                        values[key] = value
                result[frame] = values
            for _ in range(count):
                if not handle.readline():
                    raise ValueError(f"Frame {frame}: incomplete XYZ atom block")
    return result


def _stored_time(attributes):
    """GPUMD dump_exyz 的 Time 单位是 fs；没有时间就留空。"""
    value = attributes.get("Time")
    if value is None:
        return ""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("non-finite Time attribute")
    return value


def _check_msd_identity(reference, current, frame):
    import numpy as np

    if reference.particles.count != current.particles.count:
        raise ValueError(f"Frame {frame}: MSD requires unchanged atom count")
    has_ids = "Particle Identifier" in reference.particles
    if has_ids != ("Particle Identifier" in current.particles):
        raise ValueError(f"Frame {frame}: inconsistent particle identifiers")
    if has_ids:
        original = np.asarray(reference.particles["Particle Identifier"])
        ids = np.asarray(current.particles["Particle Identifier"])
        if len(np.unique(original)) != len(original) or not np.array_equal(np.sort(original), np.sort(ids)):
            raise ValueError(f"Frame {frame}: MSD requires unchanged unique particle identifiers")
    elif "Particle Type" in reference.particles and "Particle Type" in current.particles:
        # 比较元素名，不能跨帧直接比较可能重新分配的数字 type id。
        def names(particles):
            prop = particles["Particle Type"]
            mapping = {item.id: item.name for item in prop.types}
            return [mapping[int(value)] for value in prop]
        if names(reference.particles) != names(current.particles):
            raise ValueError(f"Frame {frame}: atom order or species changed; MSD without IDs requires fixed atom order")


def _particle_masses(data):
    import numpy as np

    if "Mass" in data.particles:
        masses = np.asarray(data.particles["Mass"], dtype=float)
    elif "Particle Type" in data.particles:
        prop = data.particles["Particle Type"]
        by_type = {item.id: item.mass for item in prop.types}
        masses = np.array([by_type[int(value)] for value in prop], dtype=float)
    else:
        raise ValueError("Removing center-of-mass drift requires particle masses")
    if not np.isfinite(masses).all() or np.any(masses <= 0):
        raise ValueError("Removing center-of-mass drift requires finite positive particle masses")
    return masses


_PLOT_SERIES = {
    "phase_fraction": [name + "_fraction" for name in ("Other", "FCC", "HCP", "BCC", "ICO")],
    "potential_energy": ["Potential_energy_eV", "Potential_energy_per_atom_eV"],
    "volume": ["Volume_A3", "Volume_per_atom_A3"],
    "rdf": ["Radius_A", "RDF"],
    "msd": ["MSD_A2", "MSD_x_A2", "MSD_y_A2", "MSD_z_A2", "Reference_frame"],
}


def _plot_csv_rows(path):
    """校验表头、帧号和必需数值，缺失时间保留为空，不补零。"""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        matches = [kind for kind, names in _PLOT_SERIES.items() if {"Frame", *names}.issubset(columns)]
        if len(matches) != 1 or len(columns) != len(set(columns)):
            raise ValueError(f"{Path(path).name}: unsupported or ambiguous postprocessing CSV columns")
        kind = matches[0]
        for line, row in enumerate(reader, 2):
            values = {}
            for column in ["Frame", *_PLOT_SERIES[kind]]:
                try:
                    value = float(row[column])
                except (ValueError, TypeError):
                    raise ValueError(f"{Path(path).name}, row {line}: missing or invalid {column}") from None
                if not math.isfinite(value):
                    raise ValueError(f"{Path(path).name}, row {line}: non-finite {column}")
                values[column] = value
            if values["Frame"] < 0 or not values["Frame"].is_integer():
                raise ValueError(f"{Path(path).name}, row {line}: invalid Frame")
            for column in ("Time_fs", "Delta_time_fs"):
                raw = row.get(column)
                if raw is not None and raw.strip():
                    value = float(raw)
                    if not math.isfinite(value):
                        raise ValueError(f"{Path(path).name}, row {line}: non-finite {column}")
                    values[column] = value
            yield kind, values


def _plot_time_axis(rows, *, msd=False):
    column = "Delta_time_fs" if msd else "Time_fs"
    times = [row.get(column) for row in rows]
    if all(time is not None for time in times) and all(b > a for a, b in zip(times, times[1:])):
        return [time / 1000 for time in times], "Elapsed time (ps)" if msd else "Time (ps)"
    return [row["Frame"] for row in rows], "Frame"


def _render_postprocessing_plots(sources, staged, dpi, rdf_frames) -> dict:
    """独立进程只使用 Agg canvas 和 Figure；不导入 pyplot，不调用 show。"""
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        summaries = {}
        with matplotlib.rc_context({
            "font.family": "DejaVu Sans", "font.size": 11,
            "axes.spines.top": False, "axes.spines.right": False,
            "axes.formatter.useoffset": False, "savefig.facecolor": "white",
            "text.usetex": False,
        }):
            for key, path in sources.items():
                # RDF 第一遍只收集帧号，第二遍只保存将要绘制的曲线，避免加载整份大 CSV。
                kind = None
                rows, available = [], set()
                for kind, row in _plot_csv_rows(path):
                    if kind == "rdf":
                        available.add(int(row["Frame"]))
                    else:
                        rows.append(row)
                if kind is None:
                    raise ValueError(f"{Path(path).name}: CSV contains no data rows")
                if kind == "rdf":
                    frames = sorted(available)
                    if rdf_frames is not None:
                        selected = sorted(rdf_frames)
                    elif len(frames) > 8:
                        selected = [frames[round(i * (len(frames) - 1) / 7)] for i in range(8)]
                    else:
                        selected = frames
                    if not set(selected).issubset(available):
                        raise ValueError(f"{Path(path).name}: requested RDF frames are missing")
                    curves = {frame: [] for frame in selected}
                    for _, row in _plot_csv_rows(path):
                        if int(row["Frame"]) in curves:
                            curves[int(row["Frame"])].append(row)
                else:
                    rows.sort(key=lambda row: row["Frame"])
                    if len({row["Frame"] for row in rows}) != len(rows):
                        raise ValueError(f"{Path(path).name}: duplicate frame rows")
                    frames = [int(row["Frame"]) for row in rows]
                figure = Figure(figsize=(8, 6.5 if kind in ("potential_energy", "volume") else 4.8), layout="constrained")
                FigureCanvasAgg(figure)
                axes = figure.subplots(2 if kind in ("potential_energy", "volume") else 1, 1, squeeze=False).ravel()
                try:
                    if kind in ("potential_energy", "volume"):
                        x, xlabel = _plot_time_axis(rows)
                        if kind == "potential_energy":
                            title = "Potential energy"
                            labels = ["Total potential energy (eV)", "Mean potential energy (eV/atom)"]
                        else:
                            title = "Simulation cell volume (including vacuum)"
                            labels = ["Cell volume (Å³)", "Cell volume per atom (Å³/atom)"]
                        for axis, column, label in zip(axes, _PLOT_SERIES[kind], labels):
                            axis.plot(x, [row[column] for row in rows], color="#2563a6", marker="o", markersize=3)
                            axis.set(xlabel=xlabel, ylabel=label)
                    elif kind == "phase_fraction":
                        title, xlabel = "PTM local structure fractions", "Frame"
                        for column, color in zip(_PLOT_SERIES[kind], ("#6b7280", "#16a34a", "#dc2626", "#2563eb", "#9333ea")):
                            values = [row[column] for row in rows]
                            if any(value < 0 or value > 1 for value in values):
                                raise ValueError(f"{Path(path).name}: fractions must be between 0 and 1")
                            axes[0].plot(frames, values, label=column.removesuffix("_fraction"), color=color, marker="o", markersize=3)
                        if any(abs(sum(row[column] for column in _PLOT_SERIES[kind]) - 1) > 1e-5 for row in rows):
                            raise ValueError(f"{Path(path).name}: PTM fractions must sum to 1")
                        axes[0].set(xlabel=xlabel, ylabel="Atom fraction", ylim=(-0.02, 1.02))
                        axes[0].legend(ncols=5, fontsize=9)
                    elif kind == "msd":
                        reference_frames = {row["Reference_frame"] for row in rows}
                        if len(reference_frames) != 1:
                            raise ValueError(f"{Path(path).name}: MSD plot requires one fixed reference frame")
                        title = f"Mean squared displacement (reference frame {int(next(iter(reference_frames)))})"
                        x, xlabel = _plot_time_axis(rows, msd=True)
                        for column, label in zip(_PLOT_SERIES[kind][:4], ("Total", "x", "y", "z")):
                            values = [row[column] for row in rows]
                            if any(value < 0 for value in values):
                                raise ValueError(f"{Path(path).name}: MSD must be non-negative")
                            axes[0].plot(x, values, label=label, linewidth=2 if label == "Total" else 1.3)
                        axes[0].set(xlabel=xlabel, ylabel="MSD (Å²)")
                        axes[0].legend(ncols=4)
                    else:
                        title, xlabel = "Radial distribution function", "Radius (Å)"
                        for frame, curve in curves.items():
                            curve.sort(key=lambda row: row["Radius_A"])
                            radii = [row["Radius_A"] for row in curve]
                            if any(radius < 0 for radius in radii) or any(b <= a for a, b in zip(radii, radii[1:])):
                                raise ValueError(f"{Path(path).name}: RDF radii must be non-negative and unique per frame")
                            axes[0].plot(radii, [row["RDF"] for row in curve], label=f"Frame {frame}", linewidth=1.4)
                        axes[0].set(xlabel=xlabel, ylabel="g(r)")
                        axes[0].legend(ncols=2, fontsize=9)
                    for axis in axes:
                        axis.grid(True, alpha=0.22)
                        axis.ticklabel_format(style="plain", axis="both", useOffset=False)
                    figure.suptitle(title, fontsize=13, fontweight="bold")
                    figure.savefig(staged[key], format="png", dpi=dpi)
                    summaries[key] = {"analysis": kind, "frames_in_csv": len(frames), "x_axis": xlabel}
                    if kind == "rdf":
                        summaries[key]["plotted_frames"] = selected
                finally:
                    figure.clear()
        return {"status": "SUCCEEDED", "backend": "Agg", "dpi": dpi, "plots": summaries}
    except ImportError as exc:
        return {"status": "FAILED", "error": f"Plotting requires matplotlib: {exc}"}
    except Exception as exc:
        return {"status": "FAILED", "error": str(exc)}


if __name__ == "__main__":
    workers = {"--ptm-worker": _compute_phase_fraction, "--analysis-worker": _compute_analyses,
               "--plot-worker": _render_postprocessing_plots, "--summary-worker": _compute_csv_summaries}
    if len(sys.argv) != 3 or sys.argv[1] not in workers:
        raise SystemExit("This module provides GPUMD postprocessing agent tools.")
    print(json.dumps(workers[sys.argv[1]](**json.loads(sys.argv[2])), ensure_ascii=False, allow_nan=False))
