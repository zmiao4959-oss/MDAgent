from pathlib import Path

import httpx

from miniclaw.rag.embedding import (
    VolcengineMultimodalEmbeddingClient,
    create_embedding_client,
)
from miniclaw.rag.store import JsonVectorStore, RagDocument, load_documents
from miniclaw.rag.sync_gpumd import discover_manual_urls, extract_manual_sections, split_text
from miniclaw.tools.rag_tool import expand_gpumd_query, rerank_gpumd_results


class FakeEmbeddingClient:
    model = "fake-embedding"

    def embed(self, texts):
        return [[float(text.count("NVT")), float(len(text))] for text in texts]


def test_vector_store_build_and_search(tmp_path: Path):
    docs = [
        RagDocument("nvt", "NVT 系综", "使用 ensemble nvt_nhc", "official"),
        RagDocument("run", "运行", "使用 run 指定步数", "official"),
    ]
    store = JsonVectorStore(tmp_path / "index.json")
    assert store.build(docs, FakeEmbeddingClient()) == 2

    results = store.search("NVT", FakeEmbeddingClient(), top_k=1)

    assert results[0].document.id == "nvt"
    assert results[0].mode == "vector"


def test_load_documents_and_keyword_fallback(tmp_path: Path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        '{"id":"run","title":"run 命令","content":"run 1000 运行一千步",'
        '"source":"https://gpumd.org","version":"5.5"}\n',
        encoding="utf-8",
    )
    docs = load_documents(corpus)

    results = JsonVectorStore.keyword_search("run", docs, top_k=2)

    assert results[0].document.id == "run"
    assert results[0].mode == "keyword"


def test_keyword_fallback_supports_chinese_query():
    docs = [
        RagDocument("ensemble", "系综设置", "NVT 用于恒温分子动力学", "official"),
        RagDocument("run", "运行", "run 设置模拟步数", "official"),
    ]

    results = JsonVectorStore.keyword_search("如何配置恒温系综", docs, top_k=1)

    assert results[0].document.id == "ensemble"


def test_discover_manual_urls_repairs_sitemap_path():
    sitemap = b'''<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://gpumd.orgen/v5.5/gpumd/input_parameters/run.html</loc></url>
      <url><loc>https://gpumd.orgen/v5.5/nep/index.html</loc></url>
    </urlset>'''

    assert discover_manual_urls(sitemap) == [
        "https://gpumd.org/gpumd/input_parameters/run.html"
    ]


def test_extract_manual_sections_and_split():
    html = b'''<html><head><title>run - GPUMD documentation</title></head>
    <body><div role="main"><h1>run</h1><p>Run MD steps.</p>
    <h2>Syntax</h2><pre>run 1000</pre></div></body></html>'''

    title, sections = extract_manual_sections(html)

    assert title == "run"
    assert sections[0] == ("run", "Run MD steps.")
    assert "run 1000" in sections[1][1]
    assert len(split_text("a" * 6000)) >= 3


def test_gpumd_query_expansion_supports_chinese_topics():
    expanded = expand_gpumd_query("计算热导率和扩散")

    assert "compute_hac" in expanded
    assert "compute_msd" in expanded


def test_gpumd_rerank_limits_repeated_sources_and_prefers_manual():
    from miniclaw.rag.store import SearchResult

    tutorial = RagDocument(
        "tutorial", "教程", "NPT", "tutorial-url", metadata={"kind": "tutorial"}
    )
    manual = RagDocument(
        "manual", "ensemble", "NPT", "manual-url", metadata={"kind": "input_parameters"}
    )
    candidates = [
        SearchResult(tutorial, 10.0, "keyword"),
        SearchResult(tutorial, 9.0, "keyword"),
        SearchResult(tutorial, 8.0, "keyword"),
        SearchResult(manual, 8.5, "keyword"),
    ]

    results = rerank_gpumd_results(candidates, "NPT 怎么设置", 3)

    assert results[0].document.id == "manual"
    assert sum(item.document.source == "tutorial-url" for item in results) == 2


def test_volcengine_multimodal_embedding_uses_correct_endpoint_and_payload():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = __import__("json").loads(request.content)
        assert body["input"] == [{"type": "text", "text": "GPUMD run.in"}]
        return httpx.Response(
            200,
            json={"data": [{"object": "embedding", "embedding": [[0.1, 0.2, 0.3]]}]},
        )

    client = VolcengineMultimodalEmbeddingClient(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key="test-key",
        model="doubao-embedding-vision-251215",
        transport=httpx.MockTransport(handler),
    )

    assert client.embed(["GPUMD run.in"]) == [[0.1, 0.2, 0.3]]
    assert requests[0].url.path == "/api/v3/embeddings/multimodal"


def test_embedding_factory_routes_versioned_vision_model_to_multimodal():
    client = create_embedding_client(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key="test-key",
        model="doubao-embedding-vision-251215",
    )

    assert isinstance(client, VolcengineMultimodalEmbeddingClient)
