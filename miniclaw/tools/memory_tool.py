"""
tools/memory_tool.py — Agent 记忆写入工具

允许 Agent 将重要信息持久化到 MEMORY.md，实现跨会话长期记忆。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from .registry import tool_registry
from ..config import WORKSPACE_DIR, MEMORY_FILE as _MEMORY_FILENAME
from ..logger import get_logger

logger = get_logger(__name__)

MEMORY_FILE = WORKSPACE_DIR / _MEMORY_FILENAME


def _ensure_memory_file() -> Path:
    """确保 MEMORY.md 存在且有基本结构。"""
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text(
            "# MiniClaw Memory\n\n"
            "This file stores facts the agent chooses to remember across sessions.\n\n",
            encoding="utf-8",
        )
    return MEMORY_FILE


@tool_registry.register(
    name="remember",
    description="Save a fact or piece of information to long-term memory (MEMORY.md).",
    schema={
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save an important fact, decision, or user preference to long-term memory "
                "(writes to MEMORY.md). Use this when the user explicitly asks you to remember "
                "something, or when you learn an important preference/constraint that should "
                "persist across sessions. Each entry is timestamped."
            ),
            "parameters": {
                "type": "object",
                "required": ["fact"],
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "The fact or information to remember (keep concise)",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category for organization (e.g. 'user_pref', 'project', 'decision')",
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="low",
    tags=["memory"],
)
def remember_tool(fact: str, category: Optional[str] = None, **kwargs) -> str:
    if not fact or not fact.strip():
        return "Error: fact is required"

    memory = _ensure_memory_file()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry_lines = [
        f"",
        f"## {timestamp}",
        f"",
        f"{fact.strip()}",
    ]
    if category:
        entry_lines.insert(1, f"*[{category}]*")

    try:
        with open(memory, "a", encoding="utf-8") as f:
            f.write("\n".join(entry_lines) + "\n")
        logger.info("Memory saved: %s...", fact[:80])
        return f"已记住：{fact[:200]}{'...' if len(fact) > 200 else ''}"
    except OSError as e:
        return f"Error writing to memory: {e}"
