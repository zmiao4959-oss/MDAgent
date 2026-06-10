"""OVITO 后台渲染（移植自 MDAgent）"""
from __future__ import annotations

import multiprocessing as mp
import os
import shutil
from pathlib import Path
from typing import Optional

from ..logger import get_logger
from .constants import (
    DUMP_LIKE_EXT,
    OVITO_APPLY_CNA_ON_DUMP,
    OVITO_BACKGROUND,
    OVITO_CAMERA_DIR,
    OVITO_CAMERA_POS,
    OVITO_DUMP_TEMP_NAME,
    OVITO_GIF_FPS,
    OVITO_GIF_SIZE,
)

logger = get_logger(__name__)


def _is_lammps_dump_file(file_path: str) -> bool:
    """按内容（ITEM:）或扩展名判断是否为 LAMMPS dump 轨迹。"""
    low = file_path.lower()
    if low.endswith(tuple(DUMP_LIKE_EXT)) or "dump" in os.path.basename(low):
        return True
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(8):
                line = f.readline()
                if not line.strip():
                    continue
                return line.lstrip().startswith("ITEM:")
    except OSError:
        pass
    return False


def _run_ovito_in_process(work_dir: str, ovito_file: str) -> Optional[str]:
    try:
        from ovito.io import import_file
        from ovito.modifiers import CommonNeighborAnalysisModifier
        from ovito.vis import TachyonRenderer, Viewport
    except ImportError:
        logger.error("OVITO Python 包未安装，无法渲染")
        return None

    try:
        input_file = os.path.normpath(os.path.join(work_dir, ovito_file))
        temp_file = None

        if _is_lammps_dump_file(input_file):
            temp_file = os.path.join(work_dir, OVITO_DUMP_TEMP_NAME)
            shutil.copyfile(input_file, temp_file)

            with open(temp_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            num_atoms = int(lines[3].strip())
            frame_lines = 9 + num_atoms
            last_frame_start = len(lines) - frame_lines
            last_frame_data = lines[last_frame_start : last_frame_start + frame_lines]
            lines = last_frame_data + lines[frame_lines:]

            with open(temp_file, "w", encoding="utf-8") as f:
                f.writelines(lines)

            pipeline = import_file(temp_file)
            if OVITO_APPLY_CNA_ON_DUMP:
                pipeline.modifiers.append(CommonNeighborAnalysisModifier())
        else:
            pipeline = import_file(input_file)

        pipeline.add_to_scene()
        viewport = Viewport(
            type=Viewport.Type.Perspective,
            camera_dir=OVITO_CAMERA_DIR,
            camera_pos=OVITO_CAMERA_POS,
        )
        viewport.zoom_all()

        gif_path = os.path.normpath(os.path.join(work_dir, ovito_file + ".gif"))
        viewport.render_anim(
            size=OVITO_GIF_SIZE,
            filename=gif_path,
            renderer=TachyonRenderer(),
            fps=OVITO_GIF_FPS,
            background=OVITO_BACKGROUND,
        )

        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        return gif_path if os.path.isfile(gif_path) else None
    except Exception as e:
        logger.exception("OVITO 渲染失败 %s/%s: %s", work_dir, ovito_file, e)
        return None


def render_ovito_subprocess(struct_file: Path) -> Optional[Path]:
    """在子进程中渲染，返回 GIF 路径。"""
    struct_file = struct_file.resolve()
    if not struct_file.is_file():
        return None
    work_dir = str(struct_file.parent)
    name = struct_file.name
    proc = mp.Process(target=_run_ovito_worker, args=(work_dir, name))
    proc.start()
    proc.join()
    if proc.exitcode != 0:
        return None
    gif = struct_file.parent / f"{struct_file.name}.gif"
    return gif if gif.is_file() else None


def _run_ovito_worker(work_dir: str, name: str) -> None:
    _run_ovito_in_process(work_dir, name)
