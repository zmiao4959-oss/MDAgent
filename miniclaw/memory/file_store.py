"""
memory/file_store.py — 读写工作区文本文件
"""
from pathlib import Path
from typing import List
from ..config import WORKSPACE_DIR
from ..logger import get_logger

logger = get_logger(__name__)

# 系统文件列表（在组装上下文时加载）
SYSTEM_FILES = ["SOUL.md", "AGENTS.md", "IDENTITY.md", "USER.md", "TOOLS.md", "HEARTBEAT.md"]
MEMORY_FILE = "MEMORY.md"


def read_if_exists(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def load_project_context() -> str:
    """加载工作区中的项目上下文文件"""
    parts = []
    for fname in SYSTEM_FILES:
        content = read_if_exists(WORKSPACE_DIR / fname)
        if content.strip():
            parts.append(f"## {WORKSPACE_DIR / fname}\n{content}")
    return "\n\n".join(parts)


def load_memory_files() -> str:
    """加载 MEMORY.md + 最近几天的 memory/*.md"""
    parts = []
    mem_file = WORKSPACE_DIR / MEMORY_FILE
    if mem_file.exists():
        parts.append(f"## {mem_file}\n{mem_file.read_text(encoding='utf-8')}")
    
    memory_dir = WORKSPACE_DIR / "memory"
    if memory_dir.exists():
        # 加载最近 3 天的日志
        from datetime import datetime, timedelta
        for days_back in range(3):
            date_str = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            day_file = memory_dir / f"{date_str}.md"
            if day_file.exists():
                parts.append(f"## {day_file}\n{day_file.read_text(encoding='utf-8')}")
    
    return "\n\n".join(parts)