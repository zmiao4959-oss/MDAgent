"""Backward-compatible WebChat imports.

The adapter implementation lives in :mod:`miniclaw.channels.webchat_adapter`;
HTTP assembly and feature routes live under :mod:`miniclaw.channels.web`.
"""

from .web.diagnostics import collect_runtime_diagnostics
from .web.knowledge_service import gpumd_knowledge_snapshot
from .web.project_run import (
    TASK_BEHAVIOR_DEFAULTS,
    load_task_behavior,
    normalize_task_behavior,
    save_task_behavior,
)
from .webchat_adapter import WebChatAdapter

__all__ = [
    "TASK_BEHAVIOR_DEFAULTS",
    "WebChatAdapter",
    "collect_runtime_diagnostics",
    "gpumd_knowledge_snapshot",
    "load_task_behavior",
    "normalize_task_behavior",
    "save_task_behavior",
]
