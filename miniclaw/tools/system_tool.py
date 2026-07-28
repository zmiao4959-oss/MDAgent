"""
tools/system_tool.py — 系统信息查询工具

让 Agent 可以查询自身运行状态，包括 token 用量、会话信息、工作区文件数等。
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .registry import tool_registry
from ..settings import active_workspace_dir, config
from ..logger import get_logger

logger = get_logger(__name__)

_START_TIME = time.time()


@tool_registry.register(
    name="system_info",
    description="Query the agent's own runtime status, token usage, and workspace info.",
    schema={
        "type": "function",
        "function": {
            "name": "system_info",
            "description": (
                "Get information about the agent's current runtime: uptime, workspace stats, "
                "configuration summary, and available resources. Useful for self-monitoring."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "detail": {
                        "type": "string",
                        "description": "What to query: 'status', 'workspace', 'config', or 'all' (default: 'all')",
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="low",
    tags=["system"],
)
def system_info_tool(detail: str = "all", **kwargs) -> str:
    detail = detail.lower()
    lines = [f"## MiniClaw System Info ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})", ""]

    uptime_sec = time.time() - _START_TIME
    hours = int(uptime_sec // 3600)
    minutes = int((uptime_sec % 3600) // 60)

    if detail in ("status", "all"):
        lines.append("### Runtime")
        lines.append(f"- Uptime: {hours}h {minutes}m")
        lines.append(f"- LLM Provider: {config.llm.provider} / {config.llm.model}")
        lines.append(f"- Max tool rounds: {config.agent.max_tool_rounds}")
        lines.append("")

    if detail in ("workspace", "all"):
        lines.append("### Workspace")
        root = active_workspace_dir()
        lines.append(f"- Root: `{root}`")
        try:
            files = list(root.rglob("*"))
            file_count = sum(1 for f in files if f.is_file())
            dir_count = sum(1 for d in files if d.is_dir())
            lines.append(f"- Files: {file_count}, Directories: {dir_count}")
        except Exception:
            lines.append("- (unable to count files)")
        lines.append("")

    if detail in ("config", "all"):
        lines.append("### Configuration")
        lines.append(f"- Temperature: {config.llm.temperature}")
        lines.append(f"- Max tokens/response: {config.llm.max_tokens}")
        lines.append(f"- Context limit: {config.agent.max_context_tokens}")
        lines.append(f"- Heartbeat interval: {config.agent.heartbeat_interval_min} min")
        lines.append(f"- Thinking mode: {config.agent.thinking}")
        enabled_channels = [k for k, v in config.channels.enabled.items() if v]
        lines.append(f"- Enabled channels: {', '.join(enabled_channels) or '(none)'}")
        lines.append("")

    return "\n".join(lines)
