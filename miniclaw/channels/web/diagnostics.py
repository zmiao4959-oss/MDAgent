"""Runtime capability diagnostics used by WebChat."""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from ...settings import config


def collect_runtime_diagnostics() -> Dict[str, Any]:
    """Return a secret-free view of local scientific runtime capabilities."""

    def command_item(
        item_id: str,
        label: str,
        candidates: list[str],
    ) -> Dict[str, str]:
        executable = next(
            (path for name in candidates if (path := shutil.which(name))),
            "",
        )
        return {
            "id": item_id,
            "label": label,
            "state": "ready" if executable else "unavailable",
            "detail": (
                Path(executable).name
                if executable
                else "本机未发现可执行程序"
            ),
        }

    items = [
        command_item("gpumd", "GPUMD", ["gpumd", "gpumd.exe"]),
        command_item(
            "lammps",
            "LAMMPS",
            ["lmp", "lmp.exe", "lmp_mpi", "lmp_serial"],
        ),
        command_item("ssh", "远程连接", ["ssh", "ssh.exe"]),
    ]
    gpu_item = command_item(
        "gpu",
        "NVIDIA GPU",
        ["nvidia-smi", "nvidia-smi.exe"],
    )
    if gpu_item["state"] == "ready":
        _populate_gpu_detail(gpu_item)
    items.append(gpu_item)
    items.append(
        {
            "id": "embedding",
            "label": "向量服务",
            "state": (
                "ready"
                if config.rag.enabled and config.rag.resolved_api_key
                else "degraded"
            ),
            "detail": config.rag.model if config.rag.enabled else "RAG 已关闭",
        }
    )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }


def _populate_gpu_detail(item: Dict[str, str]) -> None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        names = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        if result.returncode == 0 and names:
            item["detail"] = "、".join(names)
        else:
            item.update(state="degraded", detail="驱动工具存在，但无法读取 GPU")
    except (OSError, subprocess.SubprocessError):
        item.update(state="degraded", detail="GPU 状态读取失败")
