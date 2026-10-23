"""Regression tests for complete, version-pinned GPUMD manual ingestion."""
import hashlib
import json
from pathlib import Path

import pytest

from miniclaw.rag import sync_gpumd as corpus


def test_highlighted_html_keeps_original_command_lines():
    html = b'''<html><head><title>deform - GPUMD documentation</title></head>
    <body><div role="main"><h1>deform</h1><h2>Syntax</h2>
    <pre><span class="n">deform</span> <span class="mf">0.0001</span> <span>0.0001</span> 0 1 1 0
<span>run</span> 100000
</pre></div></body></html>'''

    _, sections = corpus.extract_manual_sections(html)

    assert sections[0][0] == "Syntax"
    assert "deform 0.0001 0.0001 0 1 1 0\nrun 100000" in sections[0][1]
    assert "deform\n0.0001" not in sections[0][1]


def test_command_is_one_full_page_including_late_caveats_and_long_code():
    command = "deform " + "0.0001071 " * 400 + "0 1 1 0"
    text = (".. _kw_deform:\n\n``deform``\n==========\n\nSyntax\n------\n\n"
            f".. code::\n\n   {command}\n\nUnits\n-----\n\nAngstrom per step.\n\n"
            + "The explanation.\n\n" * 800
            + "Caveats\n-------\n\nUse the required NPT ensemble.\n")

    documents = corpus.manual_page_documents("doc/gpumd/input_parameters/deform.rst", text.encode())

    assert len(documents) == 1
    document = documents[0]
    assert document.content == text.strip()
    assert command in document.content
    assert document.content.endswith("Use the required NPT ensemble.")
    assert document.metadata["granularity"] == "page"
    assert document.metadata["keyword"] == "deform"
    assert document.metadata["kind"] == "input_parameters"


def test_pinned_source_hash_and_ids_are_stable():
    text = b"Standard ensembles\n==================\n\nensemble npt_scr\n"
    path = "doc/gpumd/input_parameters/ensemble_standard.rst"
    by_path = corpus.manual_page_documents(path, text)[0]
    by_url = corpus.manual_page_documents(corpus.RAW_BASE + path, text)[0]

    assert by_path == by_url
    assert by_path.version == "5.5"
    assert by_path.source == "https://github.com/brucefan1983/GPUMD/blob/v5.5/" + path
    assert by_path.metadata["source_ref"] == "v5.5"
    assert by_path.metadata["source_path"] == path
    assert by_path.metadata["content_hash"] == hashlib.sha256(by_path.content.encode()).hexdigest()
    assert "npt_scr" in by_path.metadata["aliases"]


@pytest.mark.parametrize("source", [
    "https://raw.githubusercontent.com/brucefan1983/GPUMD/main/doc/gpumd/input_parameters/run.rst",
    "https://github.com/brucefan1983/GPUMD/blob/v5.4/doc/gpumd/input_parameters/run.rst",
    "https://gpumd.org/gpumd/input_parameters/run.html",
    "doc/nep/input_parameters/run.rst",
])
def test_other_versions_and_rolling_sources_cannot_be_relabelled_55(source):
    with pytest.raises(ValueError):
        corpus.manual_page_documents(source, b"run\n===\n\nrun 10\n")


def test_sitemap_discovery_only_yields_pinned_raw_sources():
    sitemap = b'''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://gpumd.orgen/v5.5/gpumd/input_parameters/run.html</loc></url>
    <url><loc>https://gpumd.org/gpumd/input_parameters/deform.html</loc></url>
    <url><loc>https://gpumd.orgen/v5.5/nep/index.html</loc></url>
    </urlset>'''

    urls = corpus.discover_manual_urls(sitemap)

    assert urls == sorted([
        corpus.RAW_BASE + "doc/gpumd/input_parameters/run.rst",
        corpus.RAW_BASE + "doc/gpumd/input_parameters/deform.rst",
    ])


def test_pinned_toctrees_are_recursive_deduplicated_and_cached(tmp_path: Path, monkeypatch):
    sources = {
        "doc/gpumd/index.rst": b"GPUMD\n=====\n\n.. toctree::\n   :maxdepth: 2\n\n   input_parameters/index\n",
        "doc/gpumd/input_parameters/index.rst": b"Commands\n========\n\n.. toctree::\n   :caption: Setup\n   deform\n   Standard ensembles <ensemble_standard>\n",
        "doc/gpumd/input_parameters/deform.rst": b"deform\n======\n\nSee :doc:`ensemble_standard` and :doc:`outside <../../nep/index>`.\n",
        "doc/gpumd/input_parameters/ensemble_standard.rst": b"Ensembles\n=========\n\nSee :doc:`deform`.\n",
    }
    requested = []

    def fetch(url):
        assert url.startswith(corpus.RAW_BASE)
        requested.append(url)
        return sources[url[len(corpus.RAW_BASE):]]

    monkeypatch.setattr(corpus, "_fetch", fetch)
    result = corpus.collect_manual_sources(tmp_path, workers=2, include_domains=False)
    assert result == sources
    assert len(requested) == len(sources)
    for path, raw in sources.items():
        assert (tmp_path / path).read_bytes() == raw

    requested.clear()
    assert corpus.collect_manual_sources(tmp_path, workers=2, include_domains=False) == sources
    assert requested == []


def test_split_never_cuts_long_lines_or_fenced_code_blocks():
    line = "deform " + "0.0001 " * 1000 + "0 1 1 0"
    code = f"```text\n{line}\n\nrun 100000\n```"
    text = "Overview.\n\n" + code + "\n\nCaveats."

    chunks = corpus.split_text(text, max_chars=80)

    assert code in chunks
    assert len(chunks) == 3
    assert corpus.split_text(line, max_chars=80) == [line]


def test_split_keeps_rst_code_directive_and_literal_block_together():
    block = ".. code-block:: text\n\n   deform 0.0001 0.0001 0 1 1 0\n\n   run 100000"
    chunks = corpus.split_text("Overview.\n\n" + block + "\n\nCaveats.", max_chars=20)
    assert block in chunks


def test_fenced_code_is_preserved_without_blank_line_before_opening_fence():
    block = "```text\ndeform 0.0001 0.0001 0 1 1 0\n\nrun 100000\n```"
    chunks = corpus.split_text("Example:\n" + block + "\nAfterwards.", max_chars=20)
    assert block in chunks


def test_failed_source_download_preserves_existing_corpus(tmp_path: Path, monkeypatch):
    output = tmp_path / "corpus.jsonl"
    output.write_text("previous corpus\n", encoding="utf-8")

    def fail(url):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(corpus, "_fetch", fail)
    with pytest.raises(RuntimeError, match="network unavailable"):
        corpus.sync(output, workers=1)
    assert output.read_text(encoding="utf-8") == "previous corpus\n"


def test_atomic_replace_failure_preserves_existing_corpus_and_cleans_temp(tmp_path: Path, monkeypatch):
    output = tmp_path / "corpus.jsonl"
    output.write_text("previous corpus\n", encoding="utf-8")
    document = corpus.manual_page_documents("doc/gpumd/input_parameters/run.rst", b"run\n===\n\nrun 10")[0]

    def fail_replace(*args):
        raise OSError("replace denied")

    monkeypatch.setattr(corpus.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace denied"):
        corpus.write_corpus([document], output)
    assert output.read_text(encoding="utf-8") == "previous corpus\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_sync_publishes_only_complete_pinned_manual_pages(tmp_path: Path, monkeypatch):
    sources = {f"doc/gpumd/input_parameters/command{index}.rst": b"Command\n=======\n\nSyntax and caveats." for index in range(101)}
    monkeypatch.setattr(corpus, "collect_manual_sources", lambda *args, **kwargs: sources)
    output = tmp_path / "corpus.jsonl"

    knowledge_count = len(corpus.knowledge_documents())
    assert corpus.sync(output) == (101, 101 + knowledge_count + 6, 0)
    documents = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(documents) == 101 + knowledge_count + 6
    assert all(document["version"] == "5.5" for document in documents)
    assert all(document["metadata"]["granularity"] == "page" for document in documents)
    assert all("/blob/v5.5/" in document["source"] for document in documents if document["metadata"]["kind"] != "example")
    assert all(document["metadata"]["kind"] != "tutorial" for document in documents)
    for document in documents:
        path = Path(document["metadata"]["local_path"])
        assert path.is_relative_to(tmp_path / "library")
        assert path.read_text(encoding="utf-8") == document["content"]


def test_nep_domains_have_pinned_sources_and_reference_aliases():
    document = corpus.manual_page_documents(
        "doc/potentials/nep.rst",
        b".. _nep_formalism:\n\nNEP\n===\n\nA complete potential page.\n",
    )[0]
    assert document.metadata["kind"] == "potentials"
    assert "nep_formalism" in document.metadata["aliases"]
    assert "/v5.5/doc/potentials/" in document.source
    file_document = corpus.manual_page_documents(
        "doc/nep/output_files/nep_txt.rst", b"nep.txt\n=======\n\nPotential output."
    )[0]
    assert file_document.metadata["kind"] == "potential_files"


def test_html_error_response_is_not_cached_or_published(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(corpus, "_fetch", lambda *args, **kwargs: b"<!doctype html><html>upstream error</html>")
    with pytest.raises(RuntimeError, match="HTML"):
        corpus.collect_manual_sources(tmp_path, workers=1)
    assert not (tmp_path / "doc/gpumd/index.rst").exists()
