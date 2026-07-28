"""构建 GPUMD 文档向量索引的命令行入口。"""
from __future__ import annotations

import argparse
from pathlib import Path

from ..settings import WORKSPACE_DIR, config
from .embedding import create_embedding_client
from .store import BuildStats, JsonVectorStore, load_documents


def default_corpus() -> Path:
    return WORKSPACE_DIR / "skills" / "gpumd-script" / "references" / "corpus.jsonl"


def default_index() -> Path:
    return WORKSPACE_DIR / "rag" / "gpumd-script" / "index.json"


def build_index(corpus: Path, index: Path, *, full: bool = False) -> BuildStats:
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
        on_progress=lambda done, total: print(f"索引处理进度：{done}/{total}", flush=True),
        force_full=full,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 GPUMD 文档向量索引")
    parser.add_argument("build", nargs="?", default="build")
    parser.add_argument("--corpus", type=Path, default=default_corpus())
    parser.add_argument("--index", type=Path, default=default_index())
    parser.add_argument("--full", action="store_true", help="忽略旧索引，强制重新向量化全部文档")
    args = parser.parse_args()
    stats = build_index(args.corpus, args.index, full=args.full)
    mode = "全量" if stats.full_rebuild else "增量"
    print(
        f"已完成 GPUMD {mode}索引：总计 {stats.total}，复用 {stats.reused}，"
        f"新增或更新 {stats.embedded}，删除失效 {stats.removed}。"
    )
    print(f"索引文件：{args.index}")


if __name__ == "__main__":
    main()
