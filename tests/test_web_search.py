"""Exercise DeepSeek tools with real HTTP request construction, no live API."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from miniclaw.agents import AgentCatalog
from miniclaw.settings import LLMConfig, workspace_scope
from miniclaw.tools import file_tools, web_search as search
from miniclaw.tools.registry import MAX_TOOL_RESULT_CHARS, tool_registry

_REAL_CLIENT = httpx.AsyncClient
_PROFILES = Path(__file__).resolve().parents[1] / "miniclaw/agents/profiles"
_KEY = "test-deepseek-key"
_RESULT = {
    "title": "Author paper", "url": "https://example.org/paper", "snippet": "Short evidence",
    "summary": "Long evidence with study conditions", "content": "", "date": None,
}


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(search, "config", SimpleNamespace(llm=LLMConfig(api_key=_KEY)))
    calls, responses = [], []

    def respond(request):
        calls.append(request)
        assert responses, "Unexpected HTTP request"
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(search.httpx, "AsyncClient", lambda **kwargs: _REAL_CLIENT(transport=httpx.MockTransport(respond), **kwargs))
    with workspace_scope(tmp_path):
        yield calls, responses


def call(name="web_search", **arguments):
    profile = AgentCatalog(roots=[_PROFILES]).require("researcher")
    return asyncio.run(tool_registry.execute(name, arguments, {"allowed_tools": list(profile.tools)}))


@pytest.mark.parametrize("base_url", ["https://api.deepseek.com", "https://api.deepseek.com/v1"])
def test_researcher_search_shares_model_key_and_maps_existing_arguments(api, base_url):
    calls, responses = api
    search.config.llm.base_url = base_url
    responses.append(httpx.Response(200, json={"results": [_RESULT]}))
    result = json.loads(call(query="UNEP-v1 site:arxiv.org", max_results=2))
    request = calls[0]
    assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
    assert request.headers["Authorization"] == f"Bearer {_KEY}"
    assert json.loads(request.content) == {"query": "UNEP-v1 site:arxiv.org", "count": 2, "need_content": False}
    assert result["results"][0]["summary"] == _RESULT["summary"]
    assert result["results"][0]["url"] == _RESULT["url"]
    assert "content" not in result["results"][0]
    assert _KEY not in json.dumps(result)


@pytest.mark.parametrize("env_name", ["DEEPSEEK_API_KEY", "MINICLAW_API_KEY"])
def test_same_llm_environment_key_resolver_is_used(api, monkeypatch, env_name):
    calls, responses = api
    search.config.llm.api_key = ""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINICLAW_API_KEY", raising=False)
    monkeypatch.setenv(env_name, "shared-env-key")
    responses.append(httpx.Response(200, json={"results": []}))
    result = json.loads(call(query="UNEP-v1"))
    assert calls[0].headers["Authorization"] == "Bearer shared-env-key"
    assert result["results"] == []
    assert "未找到相关结果" in result["message"]


def test_optional_search_content_is_fully_cached_not_returned_inline(api):
    _, responses = api
    responses.append(httpx.Response(200, json={"results": [dict(_RESULT, content="x" * 5000)]}))
    result = json.loads(call(query="UNEP-v1", need_content=True))["results"][0]
    assert "content" not in result
    cached = result["cached_page"]
    assert Path(cached["raw_file_path"]).read_bytes() == b"x" * 5000
    assert cached["total_chars"] == 5000
    assert Path(cached["index_path"]).is_file()


def test_fetch_returns_card_then_reuses_persistent_cache_without_network(api):
    calls, responses = api
    body = "# Paper\nAlloy phase transition conditions."
    responses.append(httpx.Response(200, json={"title": "Paper", "content": body}))
    first = json.loads(call("web_fetch", url=_RESULT["url"]))
    second = json.loads(call("web_fetch", url=_RESULT["url"]))
    assert "content" not in first and "content" not in second
    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert first["file_path"] == second["file_path"]
    assert Path(first["raw_file_path"]).read_bytes() == body.encode("utf-8")
    assert len(calls) == 1
    assert first["total_chars"] == len(body)
    assert str(calls[0].url) == "https://api.deepseek.com/v1/tools/fetch"
    assert json.loads(calls[0].content) == {"url": _RESULT["url"]}
    assert calls[0].headers["Authorization"] == f"Bearer {_KEY}"


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
@pytest.mark.parametrize("tool", ["web_search", "web_fetch"])
def test_permanent_failures_are_actionable_and_do_not_expose_raw_body_or_retry(api, status, tool):
    calls, responses = api
    responses.append(httpx.Response(status, json={"error": {"message": f"secret {_KEY}"}}))
    result = call(tool, **({"query": "UNEP-v1"} if tool == "web_search" else {"url": _RESULT["url"]}))
    assert result.startswith("Error:") and str(status) in result
    assert "未找到相关结果" not in result and _KEY not in result
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 504])
def test_transient_status_retries_once_and_can_recover(api, status):
    calls, responses = api
    responses.extend([httpx.Response(status), httpx.Response(200, json={"results": [_RESULT]})])
    assert json.loads(call(query="UNEP-v1"))["results"]
    assert len(calls) == 2


def test_transport_failure_remains_distinct_from_no_results(api):
    calls, responses = api
    responses.extend([httpx.ReadTimeout(f"secret {_KEY}") for _ in range(2)])
    result = call(query="UNEP-v1")
    assert result.startswith("Error:") and "ReadTimeout" in result
    assert "不能据此判断" in result and _KEY not in result
    assert len(calls) == 2


def test_cancellation_propagates_without_retry(api):
    calls, responses = api
    responses.append(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        call(query="UNEP-v1")
    assert len(calls) == 1


@pytest.mark.parametrize("arguments", [{"query": " "}, {"query": "字" * 129}, {"query": "x", "max_results": 0}, {"query": "x", "max_results": True}, {"query": "x", "need_content": "true"}])
def test_invalid_search_inputs_do_not_make_paid_requests(api, arguments):
    calls, _ = api
    assert call(**arguments).startswith("Error:")
    assert not calls


@pytest.mark.parametrize("url", ["file:///C:/secret", "https:///missing-host", "https://user:password@example.org", "https://example.org:invalid", "https://example.org/a b", "https://example.org/\x00"])
def test_invalid_fetch_urls_do_not_make_paid_requests(api, url):
    calls, _ = api
    assert call("web_fetch", url=url).startswith("Error:")
    assert not calls


@pytest.mark.parametrize("base_url", ["https://another-provider.example/v1", "http://api.deepseek.com/v1", "https://api.deepseek.com.evil.example/v1"])
def test_other_provider_credentials_are_not_sent_to_deepseek(api, base_url):
    calls, _ = api
    search.config.llm.base_url = base_url
    assert call(query="UNEP-v1").startswith("Error:")
    assert not calls


def test_missing_key_fails_without_network(api, monkeypatch):
    calls, _ = api
    search.config.llm.api_key = ""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINICLAW_API_KEY", raising=False)
    assert "未配置" in call(query="UNEP-v1")
    assert not calls


@pytest.mark.parametrize("tool,data", [("web_search", {}), ("web_search", {"results": [None]}), ("web_search", []), ("web_fetch", {"title": "Paper", "content": ""})])
def test_invalid_or_empty_content_is_not_reported_as_success(api, tool, data):
    _, responses = api
    responses.append(httpx.Response(200, json=data))
    result = call(tool, **({"query": "UNEP-v1"} if tool == "web_search" else {"url": _RESULT["url"]}))
    assert result.startswith("Error:")


def test_long_fetch_keeps_complete_raw_bytes_and_supports_bounded_existing_read(api):
    _, responses = api
    body = '# Paper\n' + '中文\\"' * 20000 + '\r\n## Methods\npressure 1 bar\n'
    responses.append(httpx.Response(200, json={"title": "Paper", "content": body}))
    rendered = call("web_fetch", url=_RESULT["url"])
    result = json.loads(rendered)
    assert len(rendered) < 8000
    assert "content" not in result
    assert Path(result["raw_file_path"]).read_bytes() == body.encode("utf-8")
    assert max(map(len, Path(result["file_path"]).read_text(encoding="utf-8").splitlines())) <= 200
    hint = result["read_hint"]
    assert len(call(hint["tool"], **hint["arguments"])) < 5000
    section = next(h for h in result["reading_index"]["headings"] if h["heading"] == "Methods")
    assert "pressure 1 bar" in call("read", path=result["file_path"], offset=section["start_line"], limit=2)


def test_large_search_keeps_whole_source_records_and_marks_omissions(api):
    _, responses = api
    results = [dict(_RESULT, summary="s" * 3000, content="c" * 5000) for _ in range(50)]
    responses.append(httpx.Response(200, json={"results": results}))
    rendered = call(query="UNEP-v1", max_results=50, need_content=True)
    result = json.loads(rendered)
    assert len(rendered) <= 10000
    assert 0 < len(result["results"]) < 50
    assert result["omitted_results"] + len(result["results"]) == 50
    assert all(r["url"] == _RESULT["url"] and r["summary_truncated"] and "content" not in r for r in result["results"])


def test_refresh_preserves_old_citations_and_publishes_new_snapshot(api):
    calls, responses = api
    responses.extend([httpx.Response(200, json={"title": "Paper", "content": text}) for text in ("Original body", "Updated body")])
    original = json.loads(call("web_fetch", url=_RESULT["url"]))
    updated = json.loads(call("web_fetch", url=_RESULT["url"], refresh=True))
    assert original["file_path"] != updated["file_path"]
    assert Path(original["raw_file_path"]).read_bytes() == b"Original body"
    assert Path(updated["raw_file_path"]).read_bytes() == b"Updated body"
    assert json.loads(call("web_fetch", url=_RESULT["url"]))["file_path"] == updated["file_path"]
    assert len(calls) == 2


def test_failed_refresh_keeps_valid_cached_snapshot(api):
    calls, responses = api
    responses.extend([httpx.Response(200, json={"content": "Original"}), httpx.Response(401)])
    original = json.loads(call("web_fetch", url=_RESULT["url"]))
    assert call("web_fetch", url=_RESULT["url"], refresh=True).startswith("Error:")
    assert json.loads(call("web_fetch", url=_RESULT["url"]))["file_path"] == original["file_path"]
    assert len(calls) == 2


def test_project_cache_does_not_cross_workspace_boundaries(api, tmp_path):
    calls, responses = api
    responses.extend([httpx.Response(200, json={"content": "Project evidence"}) for _ in range(2)])
    with workspace_scope(tmp_path / "project-a"):
        a = json.loads(call("web_fetch", url=_RESULT["url"]))
    with workspace_scope(tmp_path / "project-b"):
        b = json.loads(call("web_fetch", url=_RESULT["url"]))
    assert Path(a["file_path"]).is_relative_to(tmp_path / "project-a")
    assert Path(b["file_path"]).is_relative_to(tmp_path / "project-b")
    assert not a["cache_hit"] and not b["cache_hit"] and len(calls) == 2


def test_trusted_runtime_workspace_selects_cache_directory(api, tmp_path):
    _, responses = api
    responses.append(httpx.Response(200, json={"content": "Project evidence"}))
    p = AgentCatalog(roots=[_PROFILES]).require("researcher")
    project = tmp_path / "runtime-project"
    result = asyncio.run(tool_registry.execute("web_fetch", {"url": _RESULT["url"]}, {"allowed_tools": list(p.tools), "workspace_dir": str(project)}))
    assert Path(json.loads(result)["file_path"]).is_relative_to(project)


@pytest.mark.parametrize("damaged_file", ["reading.md", "metadata.json"])
def test_corrupt_snapshot_is_refetched_instead_of_reused(api, damaged_file):
    calls, responses = api
    responses.extend([httpx.Response(200, json={"content": "Evidence"}) for _ in range(2)])
    first = json.loads(call("web_fetch", url=_RESULT["url"]))
    (Path(first["file_path"]).parent / damaged_file).write_text("[]" if damaged_file == "metadata.json" else "corrupt", encoding="utf-8")
    second = json.loads(call("web_fetch", url=_RESULT["url"]))
    assert not second["cache_hit"] and second["file_path"] != first["file_path"]
    assert Path(second["raw_file_path"]).read_bytes() == b"Evidence" and len(calls) == 2


def test_search_body_cache_can_be_read_and_reused_by_fetch_without_new_request(api):
    calls, responses = api
    responses.append(httpx.Response(200, json={"results": [dict(_RESULT, content="# Conditions\nNPT at 1 bar")]}))
    cached = json.loads(call(query="UNEP-v1", need_content=True))["results"][0]["cached_page"]
    fetched = json.loads(call("web_fetch", url=_RESULT["url"]))
    assert fetched["cache_hit"] and fetched["retrieved_via"] == "search"
    assert cached["file_path"] == fetched["file_path"]
    assert "NPT at 1 bar" in call("read", **cached["read_hint"]["arguments"])
    assert len(calls) == 1


def test_blank_search_body_does_not_prevent_fetching_real_page(api):
    calls, responses = api
    responses.extend([httpx.Response(200, json={"results": [dict(_RESULT, content="  \n")]}), httpx.Response(200, json={"content": "Real page"})])
    assert "cached_page" not in json.loads(call(query="UNEP-v1", need_content=True))["results"][0]
    assert not json.loads(call("web_fetch", url=_RESULT["url"]))["cache_hit"]
    assert len(calls) == 2


def test_failed_cache_write_is_reported_instead_of_claiming_persistence(api, monkeypatch):
    _, responses = api
    responses.append(httpx.Response(200, json={"content": "Evidence"}))

    def fail(*args, **kwargs):
        raise OSError("cache disk is not writable")

    monkeypatch.setattr(search.WebPageCache, "save", fail)
    result = call("web_fetch", url=_RESULT["url"])
    assert result.startswith("Error:") and "not writable" in result


def test_tampered_cache_pointer_cannot_select_outside_files(api, tmp_path):
    calls, responses = api
    responses.extend([httpx.Response(200, json={"content": "Evidence"}) for _ in range(2)])
    first = json.loads(call("web_fetch", url=_RESULT["url"]))
    pointer = Path(first["file_path"]).parent.parent / "latest.json"
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched", encoding="utf-8")
    pointer.write_text(json.dumps({"version": "../../outside.txt"}), encoding="utf-8")
    second = json.loads(call("web_fetch", url=_RESULT["url"]))
    assert not second["cache_hit"] and len(calls) == 2
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_coordinator_cannot_use_new_fetch_tool(api):
    calls, _ = api
    profile = AgentCatalog(roots=[_PROFILES]).require("coordinator")
    result = asyncio.run(tool_registry.execute("web_fetch", {"url": _RESULT["url"]}, {"allowed_tools": list(profile.tools)}))
    assert "not allowed" in result
    assert not calls
