"""Agent 可调用的 GPUMD 文档检索工具。"""
from __future__ import annotations

from ..settings import WORKSPACE_DIR, config
from ..rag.embedding import create_embedding_client
from ..rag.store import JsonVectorStore, SearchResult, load_documents
from .registry import tool_registry


_GPUMD_QUERY_ALIASES = {
    "恒温": "NVT ensemble thermostat",
    "恒压": "NPT ensemble barostat",
    "微正则": "NVE ensemble",
    "热导率": "thermal conductivity compute_hac compute_hnemd",
    "热输运": "thermal transport HNEMD EMD NEMD",
    "扩散": "diffusion compute_msd compute_sdc",
    "声子": "phonon compute_phonon compute_dos",
    "弹性": "elastic compute_elastic",
    "最小化": "minimize energy minimization",
    "热力学": "thermodynamic dump_thermo thermo.out",
    "轨迹": "trajectory dump_exyz dump_position",
    "势函数": "potential NEP interaction model",
    "拉伸": "deform strain tensile",
    "黏度": "viscosity compute_viscosity",
    "粘度": "viscosity compute_viscosity",
    "离子电导": "ionic conductivity compute_ic",
    "径向分布": "radial distribution compute_rdf",
}


def expand_gpumd_query(query: str) -> str:
    additions = [english for chinese, english in _GPUMD_QUERY_ALIASES.items() if chinese in query]
    return " ".join([query, *additions]).strip()


def rerank_gpumd_results(
    results: list[SearchResult], query: str, top_k: int
) -> list[SearchResult]:
    """优先官方命令页并限制单一来源占满结果，明确问示例时反向加权。"""
    wants_examples = any(word in query.lower() for word in ("示例", "例子", "tutorial", "example"))

    def adjusted(result: SearchResult) -> float:
        kind = result.document.metadata.get("kind", "")
        if wants_examples:
            factor = 1.25 if kind == "tutorial" else 1.0
        else:
            factor = 1.25 if kind == "input_parameters" else (1.15 if kind == "input_files" else 1.0)
        return result.score * factor

    ordered = sorted(results, key=adjusted, reverse=True)
    selected: list[SearchResult] = []
    source_counts: dict[str, int] = {}
    for result in ordered:
        source = result.document.source
        if source_counts.get(source, 0) >= 2:
            continue
        selected.append(result)
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(selected) >= top_k:
            break
    return selected


@tool_registry.register(
    name="search_gpumd_docs",
    description="检索 GPUMD 官方文档摘要和示例依据；编写或修改 run.in 前应先调用。",
    schema={
        "type": "function",
        "function": {
            "name": "search_gpumd_docs",
            "description": "按问题检索 GPUMD 文档，返回命令语法、注意事项和来源链接。",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "中文或英文检索问题"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 5"},
                    "version": {"type": "string", "description": "可选 GPUMD 版本，如 5.5"},
                },
            },
        },
    },
    require_approval=False,
    risk_level="low",
    tags=["rag", "gpumd"],
)
def search_gpumd_docs_tool(
    query: str, top_k: int = 5, version: str = "", **kwargs
) -> str:
    if not query or not query.strip():
        return "错误：检索问题不能为空"
    top_k = max(1, min(int(top_k), 10))
    corpus = WORKSPACE_DIR / "skills" / "gpumd-script" / "references" / "corpus.jsonl"
    index = WORKSPACE_DIR / "rag" / "gpumd-script" / "index.json"
    if not corpus.exists():
        return f"错误：找不到 GPUMD 文档语料：{corpus}"

    documents = load_documents(corpus)
    expanded_query = expand_gpumd_query(query.strip())
    store = JsonVectorStore(index)
    mode_note = "关键词检索"
    results = []
    if config.rag.enabled and config.rag.resolved_api_key and index.exists():
        client = create_embedding_client(
            base_url=config.rag.base_url,
            api_key=config.rag.resolved_api_key,
            model=config.rag.model,
            api_type=config.rag.api_type,
            max_concurrency=config.rag.max_concurrency,
        )
        results = store.search(
            expanded_query, client, top_k=min(top_k * 8, 80), version=version.strip()
        )
        mode_note = f"向量检索（{config.rag.model}）"
    else:
        results = store.keyword_search(
            expanded_query, documents, top_k=min(top_k * 8, 80), version=version.strip()
        )
        if config.rag.enabled and not config.rag.resolved_api_key:
            mode_note += "；尚未配置 embedding API 密钥"
        elif config.rag.enabled and not index.exists():
            mode_note += "；尚未构建向量索引"

    results = rerank_gpumd_results(results, query, top_k)
    if not results:
        return f"未找到匹配的 GPUMD 文档。检索方式：{mode_note}"

    lines = [f"GPUMD 文档检索结果（{mode_note}）："]
    for number, result in enumerate(results, start=1):
        doc = result.document
        lines.extend([
            "",
            f"{number}. {doc.title}" + (f" / {doc.section}" if doc.section else ""),
            f"相关度：{result.score:.4f}",
            doc.content,
            f"来源：{doc.source}",
        ])
    return "\n".join(lines)
