"""Locate shared GPUMD 5.5 reference files; read their full text with ``read``."""
from __future__ import annotations

import math
import re
from dataclasses import replace
from urllib.parse import urlsplit

from ..settings import config
from ..rag.embedding import create_embedding_client
from ..rag.paths import gpumd_corpus_path, gpumd_index_path
from ..rag.store import JsonVectorStore, RagDocument, SearchResult, load_documents
from .registry import tool_registry


GPUMD_VERSION = "5.5"
# Search locates files. It must not inject multiple full manuals into history.
GPUMD_CONTEXT_CHARS = 5_000
_ENSEMBLE_ALIASES = {
    "nve", "nvt_ber", "nvt_nhc", "nvt_bdp", "nvt_lan", "nvt_bao", "npt_ber", "npt_scr",
}
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
    "拉伸": "deform strain tensile ensemble_standard npt_scr",
    "应变": "deform strain ensemble_standard",
    "变形": "deform deformation ensemble_standard",
    "双轴": "biaxial deform ensemble_standard npt_scr",
    "单轴": "uniaxial deform ensemble_standard npt_scr",
    "剪切": "shear deform ensemble_standard",
    "黏度": "viscosity compute_viscosity",
    "粘度": "viscosity compute_viscosity",
    "离子电导": "ionic conductivity compute_ic",
    "径向分布": "radial distribution compute_rdf",
    "元素对应": "NEP element mapping chemical species atom type element order",
    "元素映射": "NEP element mapping chemical species atom type element order",
    "元素顺序": "NEP element order chemical species atom type mapping",
    "元素类型": "NEP chemical species atom type mapping",
}
_STOP_TERMS = {
    "gpumd", "5.5", "the", "a", "an", "and", "or", "of", "for", "in",
    "to", "how", "what", "is", "with", "please", "command", "commands",
    "syntax", "example", "examples", "documentation", "document", "参数",
    "语法", "示例", "如何", "说明",
}
_LOOKUP_MODIFIERS = {"syntax", "example", "examples", "caveats", "units", "command", "commands", "gpumd", "5.5"}


def expand_gpumd_query(query: str) -> str:
    additions = [english for chinese, english in _GPUMD_QUERY_ALIASES.items() if chinese in query]
    return " ".join([query, *dict.fromkeys(additions)]).strip()


def _page_key(document: RagDocument) -> str:
    return document.source.split("#", 1)[0] or document.id


def _merge_pages(documents: list[RagDocument]) -> list[RagDocument]:
    """Reassemble legacy fragments; prefer a full-page record when available."""
    groups: dict[str, list[RagDocument]] = {}
    for document in documents:
        if document.version and document.version.lstrip("v") != GPUMD_VERSION:
            continue
        source_version = re.search(r"/(?:v|en/v)(\d+\.\d+)(?:/|$)", document.source)
        if source_version and source_version.group(1) != GPUMD_VERSION:
            continue
        groups.setdefault(_page_key(document), []).append(document)
    pages = []
    for source, fragments in groups.items():
        full = next((doc for doc in fragments if doc.metadata.get("granularity") == "page"), None)
        if full:
            pages.append(replace(full, source=source))
            continue
        first = fragments[0]
        blocks = []
        seen: set[tuple[str, str]] = set()
        for doc in fragments:
            identity = (doc.section, doc.content)
            if identity in seen:
                continue
            seen.add(identity)
            heading = f"## {doc.section}\n\n" if doc.section and len(fragments) > 1 else ""
            blocks.append(heading + doc.content)
        pages.append(replace(first, source=source, content="\n\n".join(blocks), section=""))
    return pages


def _keyword(document: RagDocument) -> str:
    keyword = str(document.metadata.get("keyword", "")).lower().strip()
    if keyword:
        return keyword
    leaf = urlsplit(document.source).path.rsplit("/", 1)[-1]
    stem = re.sub(r"\.(?:html|rst|md)$", "", leaf.lower())
    if re.fullmatch(r"[a-z][a-z0-9_]*(?:\.in|\.out|\.xyz)?", stem):
        return stem
    title = document.title.strip(" `\n").lower()
    return title if re.fullmatch(r"[a-z][a-z0-9_]*", title) else ""


def _metadata_names(document: RagDocument, field: str) -> list[str]:
    value = document.metadata.get(field, [])
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    return [str(name).strip(" `").lower() for name in value if name] if isinstance(value, list) else []


def _command_catalog(pages: list[RagDocument]) -> dict[str, RagDocument]:
    catalog = {}
    for page in pages:
        if page.metadata.get("kind") not in {"input_parameters", "input_files", "output_files", "potential_files", "knowledge", "potential", "potentials", "manual"}:
            continue
        keyword = _keyword(page)
        if keyword:
            catalog[keyword] = page
            if page.metadata.get("kind") in {"input_files", "output_files", "potential_files"}:
                dotted = re.sub(r"_(in|out|xyz|txt)$", r".\1", keyword)
                if dotted != keyword:
                    catalog.setdefault(dotted, page)
    # Only publish explicit metadata aliases or known ensemble tokens that are
    # actually documented. Incidental mentions do not become command aliases.
    for page in pages:
        keyword = _keyword(page)
        if not keyword or keyword not in catalog:
            continue
        for alias in _metadata_names(page, "aliases"):
            if re.fullmatch(r"[a-z][a-z0-9_.-]*", alias):
                catalog.setdefault(alias, page)
        if keyword == "ensemble_standard":
            for alias in _ENSEMBLE_ALIASES:
                if re.search(rf"\b{re.escape(alias)}\b", page.content, re.I):
                    catalog.setdefault(alias, page)
            for family in ("nvt", "npt"):
                if any(alias.startswith(family + "_") and alias in catalog for alias in _ENSEMBLE_ALIASES):
                    catalog.setdefault(family, page)
    return catalog


def _explicit_pages(query: str, catalog: dict[str, RagDocument]) -> list[RagDocument]:
    selected: dict[str, RagDocument] = {}
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", query.lower()):
        page = catalog.get(token.rstrip("."))
        if page:
            selected.setdefault(_page_key(page), page)
    return list(selected.values())


def _lookup_only(query: str, catalog: dict[str, RagDocument]) -> bool:
    """Only name lists/simple syntax lookups bypass semantic retrieval.

    Seeing 'potential' in a relationship question is not an exact lookup.
    """
    cleaned = re.sub(r"(?:语法|示例|参数|单位|限制|命令|说明)", " ", query.lower())
    tokens = re.findall(r"[a-z][a-z0-9_.-]*|[\u4e00-\u9fff]+", cleaned)
    tokens = [token.rstrip(".") for token in tokens]
    return bool(tokens) and any(token in catalog for token in tokens) and all(
        token in catalog or token in _LOOKUP_MODIFIERS for token in tokens
    )


def _required_pages(query: str, catalog: dict[str, RagDocument]) -> list[RagDocument]:
    pages = _explicit_pages(query, catalog) if _lookup_only(query, catalog) else []
    loading = any(word in query.lower() for word in (
        "拉伸", "应变", "双轴", "变形", "剪切", "biaxial", "tensile", "strain", "deform",
    ))
    if loading:
        for name in ("deform", "ensemble_standard"):
            page = catalog.get(name)
            if page and all(_page_key(existing) != _page_key(page) for existing in pages):
                pages.append(page)
    return pages


def _keyword_results(query: str, pages: list[RagDocument]) -> list[SearchResult]:
    terms = [term for term in JsonVectorStore._query_terms(query) if term not in _STOP_TERMS]
    results = []
    for page in pages:
        title = " ".join([
            page.title, _keyword(page),
            *[" ".join(_metadata_names(page, field)) for field in ("aliases", "keywords", "topics", "search_terms", "match_terms")],
        ]).lower()
        text = page.content.lower()
        score = 0.0
        for term in terms:
            # Bounded term frequency prevents long repetitive scripts from
            # winning simply because they repeat commands hundreds of times.
            title_hits, hits = title.count(term), text.count(term)
            score += (6.0 if title_hits else 0.0) + (1.0 + math.log1p(min(hits, 5)) if hits else 0.0)
        if score:
            results.append(SearchResult(page, score, "keyword"))
    return sorted(results, key=lambda result: result.score, reverse=True)


def _nep_mapping_query(query: str) -> bool:
    text = query.lower()
    mapping = any(term in text for term in (
        "元素对应", "元素映射", "元素顺序", "元素类型", "element order", "element mapping",
        "species mapping", "type mapping", "species order", "atom type", "atomic number",
    )) or ("element" in text and "mapping" in text) or (
        "元素" in text and any(term in text for term in ("对应", "映射", "顺序", "索引", "类型"))
    )
    return mapping and any(term in text for term in ("nep", "势", "potential", "model.xyz"))


def _nep_mapping_pages(pages: list[RagDocument]) -> list[RagDocument]:
    """Only a matching knowledge page establishes coverage of this FAQ.

    Generic potential syntax and incidental dftd3/RDF mentions do not answer
    how model chemical symbols map to an NEP file's elements.
    """
    matches = []
    for page in pages:
        if page.metadata.get("kind") not in {"knowledge", "potential", "potentials"}:
            continue
        labels = " ".join([page.title, _keyword(page), *_metadata_names(page, "keywords"), *_metadata_names(page, "topics")]).lower()
        if "nep" in labels and any(term in labels for term in ("mapping", "元素对应", "元素顺序", "元素映射")):
            matches.append(page)
    return matches


def _example_results(query: str, pages: list[RagDocument]) -> list[SearchResult]:
    terms = [term for term in JsonVectorStore._query_terms(query) if term not in _STOP_TERMS]
    results = []
    for page in pages:
        if page.metadata.get("kind") != "example":
            continue
        labels = " ".join(_metadata_names(page, "match_terms") + _metadata_names(page, "keywords"))
        title = page.title.lower()
        score = sum(6 * (term in labels) + 2 * (term in title) for term in terms)
        if score:
            results.append(SearchResult(page, score, "example"))
    return sorted(results, key=lambda result: result.score, reverse=True)


def rerank_gpumd_results(results: list[SearchResult], query: str, top_k: int) -> list[SearchResult]:
    """Rank complete pages and remove repeat sources, including legacy chunks."""
    pages = _merge_pages([result.document for result in results])
    required = _required_pages(query, _command_catalog(pages))
    preferred = {_page_key(page): rank for rank, page in enumerate(required)}
    if _nep_mapping_query(query):
        preferred = {_page_key(page): rank for rank, page in enumerate(_nep_mapping_pages(pages))}
    syntax_query = any(word in query.lower() for word in ("syntax", "语法", "参数", "单位", "caveat", "命令"))
    wants_tutorial = not syntax_query and any(word in query.lower() for word in ("教程", "tutorial", "完整案例", "完整示例"))
    wants_example = not syntax_query and any(word in query.lower() for word in ("例子", "示例", "案例", "example"))
    if wants_example and not _lookup_only(query, _command_catalog(pages)):
        # Match the task labels, not every setup command mentioned in a full
        # script. A tension example also contains NPT equilibration, but it
        # should not outrank the dedicated relaxation example for that task.
        examples = _example_results(query, pages)
        ordered = [item.document for item in examples[:1]] + required
        if examples:
            catalog = _command_catalog(pages)
            dependencies = [catalog[name] for name in _metadata_names(examples[0].document, "command_dependencies") if name in catalog]
            ordered.extend(item.document for item in _keyword_results(expand_gpumd_query(query), dependencies)[:2])
        preferred = {_page_key(page): rank for rank, page in enumerate(ordered)}
    scores: dict[str, tuple[float, str]] = {}
    for result in results:
        key = _page_key(result.document)
        if key not in scores or result.score > scores[key][0]:
            scores[key] = (result.score, result.mode)

    def adjusted(page: RagDocument) -> float:
        key = _page_key(page)
        if key in preferred:
            return 100_000.0 - preferred[key]
        kind = page.metadata.get("kind", "")
        factor = 1.25 if kind == "input_parameters" else 1.15 if kind == "input_files" else 1.0
        if kind == "tutorial":
            factor = 1.15 if wants_tutorial else 0.75
        return scores[key][0] * factor

    pages.sort(key=adjusted, reverse=True)
    return [SearchResult(page, scores[_page_key(page)][0], scores[_page_key(page)][1]) for page in pages[:top_k]]


def _local_file(page: RagDocument) -> str:
    absolute = page.metadata.get("local_path")
    if absolute:
        return str(absolute)
    relative = page.metadata.get("library_path")
    if relative:
        return str(gpumd_corpus_path().parents[2] / "skills" / "gpumd-script" / str(relative))
    # Old corpora may predate publication. Never invent a readable path.
    return ""


def _snippet(page: RagDocument, query: str, limit: int = 320) -> str:
    """Return prose hints only; command syntax remains in the source file."""
    terms = set(JsonVectorStore._query_terms(expand_gpumd_query(query))) - _STOP_TERMS
    lines = []
    fenced = False
    for line in page.content.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            continue
        if fenced or not line.strip() or line[0].isspace():
            continue
        text = line.strip()
        if text.startswith(("#", ".. ", ":")) or re.fullmatch(r"[=~^\-+*#]{3,}", text):
            continue
        lines.append(text)
    if not lines:
        return ""
    best = max(enumerate(lines), key=lambda item: (sum(term in item[1].lower() for term in terms), -item[0]))[1]
    return best if len(best) <= limit else best[:limit - 1] + "…"


def _format_results(results: list[SearchResult], mode: str, query: str = "") -> str:
    header = f"GPUMD 5.5 文件定位（{mode}）。请用 read 读取所需文件；以下片段不替代完整原文。"
    pieces = [header]
    used = len(header)
    omitted = 0
    for result in results:
        page = result.document
        path = _local_file(page)
        body = f"### {page.title}\npath: {path or '未发布本地文件，当前知识库需重新发布'}\nsource: {page.source}"
        snippet = _snippet(page, query)
        if snippet:
            body += "\n相关片段：" + snippet
        if not path:
            body += "\n此项只有旧语料记录，不能作为可读取文件定位。"
        if used + len(body) + 100 > GPUMD_CONTEXT_CHARS:
            omitted += 1
            continue
        pieces.append(body)
        used += len(body) + 2
    if omitted:
        pieces.append(f"另有 {omitted} 项未展示；缩小问题后定位对应文件。")
    return "\n\n".join(pieces)


def _load_pages() -> tuple[list[RagDocument], str]:
    corpus = gpumd_corpus_path()
    if not corpus.exists():
        return [], f"错误：找不到 GPUMD 5.5 文档语料：{corpus}"
    try:
        pages = _merge_pages(load_documents(corpus))
    except (OSError, ValueError) as exc:
        return [], f"错误：无法读取 GPUMD 5.5 文档语料（{type(exc).__name__}）。请检查或重新同步本地知识库。"
    return pages, "" if pages else "错误：本地语料中没有 GPUMD 5.5 文档，请同步固定版知识库。"


def read_gpumd_command_tool(command: str, section: str = "", **kwargs) -> str:
    """Unregistered Python compatibility helper; returns file locations only."""
    if not command or not command.strip():
        return "错误：命令名不能为空"
    pages, error = _load_pages()
    if error:
        return error
    catalog = _command_catalog(pages)
    names = [name.strip(" `\n").lower() for name in re.split(r"[,，]", command) if name.strip(" `\n")]
    selected: dict[str, RagDocument] = {}
    missing = []
    for name in names:
        page = catalog.get(name)
        if page is None:
            missing.append(name)
        else:
            selected.setdefault(_page_key(page), page)
    if not names:
        return "错误：命令名不能为空"
    if not selected:
        return f"未找到 GPUMD 5.5 命令 {command!r}。未知功能可调用 search_gpumd_docs(query='功能描述')，不要猜测命令语法。"
    results = [SearchResult(page, 1.0, "exact") for page in selected.values()]
    response = _format_results(results, "本地名称定位，无embedding", command)
    if missing:
        response += "\n未找到命令：" + "、".join(missing) + "。请用 search_gpumd_docs 查询功能。"
    return response


@tool_registry.register(
    name="search_gpumd_docs",
    schema={"type": "function", "function": {
        "name": "search_gpumd_docs",
        "description": "定位固定 GPUMD 5.5 文档和相似脚本的本地文件路径，返回少量相关片段；用已有 read 工具读取完整文件。已知命令可直接查名称，关系问题用自然语言描述。",
        "parameters": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "中文或英文功能问题"},
            "top_k": {"type": "integer", "description": "最多返回文件数，默认3，上限5；只返回紧凑定位结果"},
        }},
    }},
    require_approval=False, risk_level="low", tags=["rag", "gpumd"],
)
def search_gpumd_docs_tool(query: str, top_k: int = 3, version: str = "", **kwargs) -> str:
    # Keep the Python argument for old callers; the library is always 5.5.
    if not query or not query.strip():
        return "错误：检索问题不能为空"
    try:
        top_k = max(1, min(int(top_k), 5))
    except (ValueError, TypeError):
        return "错误：top_k 必须为整数"
    pages, error = _load_pages()
    if error:
        return error
    query = query.strip()
    catalog = _command_catalog(pages)
    explicit, required = _explicit_pages(query, catalog), _required_pages(query, catalog)
    if explicit and _lookup_only(query, catalog):
        exact = [SearchResult(page, 1.0, "exact") for page in required[:top_k]]
        return _format_results(exact, "本地名称定位，无embedding", query)

    expanded = expand_gpumd_query(query)
    lexical = _keyword_results(expanded, pages)
    mode = "本地关键词检索"
    semantic: list[SearchResult] = []
    index = gpumd_index_path()
    if config.rag.enabled and config.rag.resolved_api_key and index.exists():
        try:
            client = create_embedding_client(
                base_url=config.rag.base_url, api_key=config.rag.resolved_api_key,
                model=config.rag.model, api_type=config.rag.api_type,
                max_concurrency=config.rag.max_concurrency,
                timeout_sec=8.0, max_retries=0,
            )
            semantic = JsonVectorStore(index).search(expanded, client, top_k=80, version=GPUMD_VERSION)
            mode = "关键词＋语义混合检索"
        except Exception as exc:
            # Do not echo HTTP bodies containing credentials or giant payloads.
            mode += f"；embedding不可用（{type(exc).__name__}），已立即回退，未重复请求"
    elif config.rag.enabled:
        mode += "；尚未配置embedding密钥" if not config.rag.resolved_api_key else "；尚未构建向量索引"
    if semantic and not lexical:
        mode += "；仅语义候选，内容覆盖需读取文件确认"

    mapping_pages = _nep_mapping_pages(pages) if _nep_mapping_query(query) else []
    if _nep_mapping_query(query) and not mapping_pages:
        return (
            f"未覆盖：当前本地库缺少 NEP 势文件与模型元素/类型对应关系的专门说明。检索方式：{mode}。"
            "普通 potential 命令、dftd3/RDF 页面中的 NEP 提及不能回答该问题。"
            "请查看知识库目录补充相关来源，不要重复检索同一缺失事实或据旁支片段猜测。"
        )

    # Reciprocal-rank fusion handles keyword/vector score scale differences.
    by_source = {_page_key(page): page for page in pages}
    fused: dict[str, float] = {}
    for ranking, weight in ((lexical, 1.0), (semantic, 0.7)):
        seen = set()
        for rank, result in enumerate(ranking, 1):
            key = _page_key(result.document)
            if key in seen or key not in by_source:
                continue
            seen.add(key)
            fused[key] = fused.get(key, 0.0) + weight / (30 + rank)
    for rank, page in enumerate(required):
        fused[_page_key(page)] = 10.0 - rank * 0.1
    for rank, page in enumerate(mapping_pages):
        fused[_page_key(page)] = 20.0 - rank * 0.1
    candidates = [SearchResult(by_source[key], score, "hybrid") for key, score in fused.items()]
    results = rerank_gpumd_results(candidates, query, top_k)
    if not results:
        return f"未找到匹配的 GPUMD 5.5 文档。检索方式：{mode}"
    return _format_results(results, mode, query)
