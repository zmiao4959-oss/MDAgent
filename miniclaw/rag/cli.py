"""构建 GPUMD 文档向量索引的命令行入口。"""
from __future__ import annotations

import argparse
from pathlib import Path

from ..config import WORKSPACE_DIR, config
from .embedding import create_embedding_client
from .store import JsonVectorStore, load_documents


def default_corpus() -> Path:
    return WORKSPACE_DIR / "skills" / "gpumd-script" / "references" / "corpus.jsonl"


def default_index() -> Path:
    return WORKSPACE_DIR / "rag" / "gpumd-script" / "index.json"


def build_index(corpus: Path, index: Path) -> int:
    if not config.rag.enabled:
        raise RuntimeError("RAG 已在配置中禁用")
    client = create_embedding_client(
        base_url=config.rag.base_url,
        api_key=config.rag.resolved_api_key,
        model=config.rag.model,
        api_type=config.rag.api_type,
        max_concurrency=config.rag.max_concurrency,
    )
    documents = load_documents(corpus)
    return JsonVectorStore(index).build(
        documents,
        client,
        batch_size=config.rag.batch_size,
        on_progress=lambda done, total: print(f"向量化进度：{done}/{total}", flush=True),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 GPUMD 文档向量索引")
    parser.add_argument("build", nargs="?", default="build")
    parser.add_argument("--corpus", type=Path, default=default_corpus())
    parser.add_argument("--index", type=Path, default=default_index())
    args = parser.parse_args()
    count = build_index(args.corpus, args.index)
    print(f"已建立 GPUMD 向量索引，共 {count} 个文档块：{args.index}")


if __name__ == "__main__":
    main()
