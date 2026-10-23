"""GPUMD searches locate shared files without copying manuals into history."""
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from miniclaw.rag.store import RagDocument, SearchResult
from miniclaw.tools import rag_tool


DEFORM = """# deform

Deform the simulation box under a suitable NPT ensemble.

## Syntax

```text
deform <rate_x> <rate_y> <rate_z> <deform_x> <deform_y> <deform_z>
```

The rate is a box-length increment in Angstrom per time step.

## Caveats

Periodic boundary conditions are required in deformed directions.
"""
ENSEMBLE = """# ensemble_standard

Standard NPT and NVT ensembles include npt_ber, npt_scr and nvt_bdp.

## Syntax

```text
ensemble npt_ber <T_1> <T_2> <tau_T> <p_x> <p_y> <p_z> <C_x> <C_y> <C_z> <tau_p>
```

Use physical moduli and relaxation times for the system.
"""
NEP_MAPPING = """# NEP 元素对应

NEP element mapping: match model chemical species by element identity; potential header element order must not be guessed from numeric atom types.

## Checks

Read the actual potential header and model species.
"""


def page(keyword, content, *, kind="input_parameters", **metadata):
    return RagDocument(
        keyword, keyword, content,
        f"https://github.com/brucefan1983/GPUMD/blob/v5.5/doc/gpumd/{kind}/{keyword}.rst",
        version="5.5", metadata={
            "kind": kind, "keyword": keyword, "granularity": "page", "source_ref": "v5.5", **metadata,
        },
    )


@pytest.fixture
def library(tmp_path, monkeypatch):
    corpus, index = tmp_path / "corpus.jsonl", tmp_path / "index.json"
    pages = [
        page("deform", DEFORM),
        page("ensemble_standard", ENSEMBLE),
        page("compute_hnemd", "# compute_hnemd\n\nHNEMD thermal conductivity and heat current transport."),
        page("potential", "# potential\n\nLoad an NEP potential file."),
        page("model_xyz", "# model.xyz\n\nModel chemical species and coordinates.", kind="input_files"),
        page("dftd3", "# dftd3\n\nNEP potential dispersion corrections."),
        page("nep_element_mapping", NEP_MAPPING, kind="knowledge", keywords=["NEP", "元素对应", "元素顺序", "element mapping", "species", "type"], library_path="potentials/nep_element_mapping.md"),
        page("biaxial-tension", "# 双轴拉伸案例\n\nUse a relaxed orthogonal box for biaxial tension.", kind="example", keywords=["双轴", "biaxial", "tension"], library_path="examples/biaxial-tension/example.md"),
        page("uniaxial-tension", "# 单轴拉伸案例\n\nUse uniaxial tension with transverse relaxation.", kind="example", keywords=["单轴", "uniaxial", "tension"], library_path="examples/uniaxial-tension/example.md"),
    ]
    def write(documents):
        for doc in documents:
            relative = doc.metadata.setdefault("library_path", "commands/" + doc.id + ".md")
            target = tmp_path / "skill" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(doc.content, encoding="utf-8")
            doc.metadata["local_path"] = str(target)
        corpus.write_text("\n".join(json.dumps(asdict(doc), ensure_ascii=False) for doc in documents) + "\n", encoding="utf-8")
    write(pages)
    monkeypatch.setattr(rag_tool, "gpumd_corpus_path", lambda: corpus)
    monkeypatch.setattr(rag_tool, "gpumd_index_path", lambda: index)
    settings = SimpleNamespace(enabled=False, resolved_api_key="test-key", base_url="https://example.invalid", model="test-model", api_type="auto", max_concurrency=1)
    monkeypatch.setattr(rag_tool, "config", SimpleNamespace(rag=settings))
    return SimpleNamespace(pages=pages, write=write, index=index, settings=settings, root=tmp_path)


def enable_semantic(library, monkeypatch, results=None):
    library.settings.enabled = True
    library.index.write_text("{}", encoding="utf-8")
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return object()
    monkeypatch.setattr(rag_tool, "create_embedding_client", create)
    monkeypatch.setattr(rag_tool.JsonVectorStore, "search", lambda *args, **kwargs: results or [])
    return calls


def test_search_returns_readable_file_locator_and_original_stays_complete(library):
    text = rag_tool.search_gpumd_docs_tool("deform")
    target = Path(library.pages[0].metadata["local_path"])

    assert str(target) in text
    assert target.read_text(encoding="utf-8") == DEFORM
    assert DEFORM not in text
    assert "```" not in text
    assert "read" in text and "v5.5/doc/gpumd" in text
    assert "path:" in text and "source:" in text


def test_old_command_tool_is_not_registered():
    assert rag_tool.tool_registry.get("read_gpumd_command") is None
    schema = rag_tool.tool_registry.get("search_gpumd_docs").schema
    assert "read_gpumd_command" not in str(schema)
    assert "version" not in schema["function"]["parameters"]["properties"]


@pytest.mark.parametrize("query", ["deform", "deform,ensemble_standard", "npt_scr", "deform syntax example"])
def test_pure_names_and_simple_syntax_do_not_call_embedding(library, monkeypatch, query):
    calls = enable_semantic(library, monkeypatch)

    text = rag_tool.search_gpumd_docs_tool(query)

    assert not calls
    assert "本地名称定位" in text
    assert "path:" in text


@pytest.mark.parametrize("query", [
    "NEP potential file element order species mapping",
    "NEP 势文件与 model.xyz 的元素对应怎么处理",
    "NEP 的元素顺序和模型的元素类型如何对应",
])
def test_nep_relationship_query_does_not_short_circuit_at_potential_or_model(library, monkeypatch, query):
    calls = enable_semantic(library, monkeypatch, [SearchResult(library.pages[5], 0.99, "vector")])

    text = rag_tool.search_gpumd_docs_tool(query)

    assert len(calls) == 1
    assert "关键词＋语义混合检索" in text
    assert text.index("nep_element_mapping") < text.index("path:")
    assert "potentials" in text
    assert NEP_MAPPING not in text


@pytest.mark.parametrize("query", ["NEP potential file element order species mapping", "NEP势函数元素对应"])
def test_missing_nep_mapping_returns_explicit_coverage_gap(library, monkeypatch, query):
    library.write([doc for doc in library.pages if doc.metadata.get("kind") != "knowledge"])
    calls = enable_semantic(library, monkeypatch, [SearchResult(library.pages[5], 0.99, "vector")])

    text = rag_tool.search_gpumd_docs_tool(query)

    assert len(calls) == 1
    assert "未覆盖" in text
    assert "不能回答" in text
    assert "path:" not in text
    assert "Load an NEP" not in text


def test_chinese_biaxial_query_locates_both_commands(library):
    text = rag_tool.search_gpumd_docs_tool("铜镍双轴拉伸，应变30%，如何设置", top_k=2)

    assert "deform" in text and "ensemble_standard" in text
    assert text.count("path:") == 2
    assert DEFORM not in text and ENSEMBLE not in text


@pytest.mark.parametrize("axis,expected", [("双轴", "biaxial-tension"), ("单轴", "uniaxial-tension")])
def test_similar_script_requests_rank_relevant_example_before_commands(library, axis, expected):
    text = rag_tool.search_gpumd_docs_tool(axis + "拉伸类似案例", top_k=3)

    assert expected in text
    assert text.index(expected) < text.index("### deform")
    assert "examples" in text


def test_search_budget_never_pours_a_long_page_into_context(library):
    long_content = "# deform\n\n" + "A relevant deformation constraint. " * 1000 + "\n\n```text\n" + "deform 1 1 0 1 1 0\n" * 1000 + "```"
    library.write([replace(library.pages[0], content=long_content)])

    text = rag_tool.search_gpumd_docs_tool("deform")

    assert len(text) < 1500
    assert "```" not in text
    assert "deform 1 1 0" not in text
    assert len(Path(library.pages[0].metadata["local_path"]).read_text(encoding="utf-8")) > 20_000


def test_legacy_fragments_deduplicate_by_source(library):
    source = "https://gpumd.org/gpumd/input_parameters/deform.html"
    fragments = [RagDocument(f"deform-{n}", "deform", value, source + "#" + str(n), "5.5", str(n), {"kind": "input_parameters", "keyword": "deform"}) for n, value in enumerate(("Overview.", "Angstrom/step.", "NPT and periodic boundaries required."))]
    library.write(fragments)

    text = rag_tool.search_gpumd_docs_tool("deform")
    ranked = rag_tool.rerank_gpumd_results([SearchResult(doc, 1.0, "keyword") for doc in fragments], "deform", 5)

    assert text.count("source:") == 1
    assert len(ranked) == 1
    assert "NPT and periodic" in ranked[0].document.content


def test_embedding_failure_falls_back_once_without_echoing_secrets(library, monkeypatch):
    library.settings.enabled = True
    library.index.write_text("{}", encoding="utf-8")
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("secret credentials must not be echoed")
    monkeypatch.setattr(rag_tool, "create_embedding_client", fail)

    text = rag_tool.search_gpumd_docs_tool("如何计算热导率")

    assert len(calls) == 1
    assert calls[0]["timeout_sec"] == 8.0 and calls[0]["max_retries"] == 0
    assert "已立即回退" in text
    assert "secret credentials" not in text
    assert "compute_hnemd" in text


def test_semantic_only_unknown_query_can_still_locate_files(library, monkeypatch):
    enable_semantic(library, monkeypatch, [SearchResult(library.pages[2], 0.4, "vector")])

    text = rag_tool.search_gpumd_docs_tool("energy transfer in solids")

    assert "compute_hnemd" in text
    assert "path:" in text


def test_fixed_version_excludes_other_versions(library):
    wrong = replace(library.pages[0], id="wrong", content="NEWER WRONG SYNTAX", version="6.0", source=library.pages[0].source.replace("v5.5", "v6.0"))
    library.write([*library.pages, wrong])

    text = rag_tool.search_gpumd_docs_tool("deform", version="6.0")

    assert "NEWER WRONG SYNTAX" not in text
    assert "5.5" in text


@pytest.mark.parametrize("stem,dotted,kind", [("model_xyz", "model.xyz", "input_files"), ("run_in", "run.in", "input_files"), ("thermo_out", "thermo.out", "output_files")])
def test_real_filenames_locate_shared_documents(library, stem, dotted, kind):
    library.write([page(stem, "# " + dotted + "\n\nFile layout.", kind=kind)])

    text = rag_tool.search_gpumd_docs_tool(dotted)

    assert "path:" in text and stem in text


def test_invalid_corpus_reports_actionable_error(library, monkeypatch, tmp_path):
    monkeypatch.setattr(rag_tool, "gpumd_corpus_path", lambda: tmp_path / "missing.jsonl")
    assert "找不到" in rag_tool.search_gpumd_docs_tool("deform")
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(rag_tool, "gpumd_corpus_path", lambda: bad)
    assert "重新同步" in rag_tool.search_gpumd_docs_tool("如何拉伸")


def test_relaxation_example_ranks_above_a_tension_example_with_an_npt_prestage():
    relaxation = RagDocument(
        "example:npt", "NPT 弛豫", "NPT equilibrium.", "example:npt", "5.5",
        metadata={"kind": "example", "keyword": "npt-relaxation", "match_terms": ["NPT", "弛豫", "平衡"]},
    )
    tension = RagDocument(
        "example:tension", "NPT 后单轴拉伸：相对弛豫态", "NPT equilibration before loading. " * 50,
        "example:tension", "5.5",
        metadata={"kind": "example", "keyword": "uniaxial-tension", "match_terms": ["单轴拉伸", "工程应变"]},
    )
    ranked = rag_tool.rerank_gpumd_results(
        [SearchResult(tension, 2, "vector"), SearchResult(relaxation, 1, "vector")],
        "NPT 弛豫完整脚本例子", 2,
    )
    assert ranked[0].document.id == "example:npt"
