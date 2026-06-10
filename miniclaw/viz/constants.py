"""可视化相关常量（与 MDAgent 对齐）"""
from pathlib import Path

# 浏览器可加载 / 自动检测的扩展名
VISUAL_EXT = frozenset({
    ".gif", ".png", ".jpg", ".jpeg",
    ".xyz", ".dump", ".lammpstrj", ".lmp", ".data",
    ".csv",
})
STRUCTURE_EXT = frozenset({".xyz", ".dump", ".lammpstrj", ".lmp", ".data"})
# LAMMPS dump 轨迹（按内容 ITEM: 或扩展名识别）
DUMP_LIKE_EXT = frozenset({".dump", ".lammpstrj"})
MEDIA_IMAGE_EXT = frozenset({".gif", ".png", ".jpg", ".jpeg"})

SKIP_DIR_NAMES = frozenset({
    ".idea", "__pycache__", ".git", "web", "node_modules", ".venv", "sessions",
})

# OVITO（与 MDAgent config 一致）
OVITO_GIF_SIZE = (500, 500)
OVITO_GIF_FPS = 30
OVITO_BACKGROUND = (45 / 255, 45 / 255, 45 / 255)
OVITO_CAMERA_DIR = (1, -2.5, -1)
OVITO_CAMERA_POS = (1, 1, 1)
OVITO_DUMP_TEMP_NAME = "temp_first_frame_overwritten.lammpstrj"
OVITO_APPLY_CNA_ON_DUMP = True
