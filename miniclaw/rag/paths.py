"""GPUMD RAG 数据文件的默认位置。"""
from __future__ import annotations

from pathlib import Path

from ..settings import WORKSPACE_DIR


def gpumd_corpus_path() -> Path:
    return WORKSPACE_DIR / "rag" / "gpumd-script" / "corpus.jsonl"


def gpumd_index_path() -> Path:
    return WORKSPACE_DIR / "rag" / "gpumd-script" / "index.json"


def gpumd_skill_path() -> Path:
    return WORKSPACE_DIR / "skills" / "gpumd-script"
