import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from miniclaw.llm import diagnostics
from miniclaw.llm.openai_compat import OpenAICompatProvider


def provider_for(create):
    provider = object.__new__(OpenAICompatProvider)
    provider.model = "test"
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return provider


async def collect(provider):
    return [chunk async for chunk in provider.chat_stream([])]


def test_http_diagnostics_counts_sdk_attempts_without_logging_secrets(monkeypatch):
    lines = []
    monkeypatch.setattr(diagnostics.logger, "info", lambda template, *args: lines.append(template % args))
    monitor = diagnostics.HTTPDiagnostics()
    async def run():
        for index in range(2):
            request = httpx.Request("POST", "https://example.test", headers={"Authorization": "secret-key", "x-stainless-retry-count": str(index)})
            await monitor.on_request(request)
            response = httpx.Response(429, request=request, headers={"retry-after": "30", "x-request-id": "upstream-123", "set-cookie": "secret-cookie"})
            await monitor.on_response(response)
    asyncio.run(run())
    assert any("requests_60s=2" in line and "sdk_retry=1" in line for line in lines)
    assert any("status=429" in line and "upstream-123" in line and "retry-after" in line for line in lines)
    assert "secret" not in "".join(lines)


def test_http_diagnostics_removes_old_attempts(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(diagnostics, "monotonic", lambda: now[0])
    monitor = diagnostics.HTTPDiagnostics()
    asyncio.run(monitor.on_request(httpx.Request("POST", "https://example.test")))
    now[0] = 61
    asyncio.run(monitor.on_request(httpx.Request("POST", "https://example.test")))
    assert list(monitor.recent) == [61]


def test_real_sdk_hidden_retry_is_recorded(monkeypatch):
    from miniclaw.llm import openai_compat
    lines = []
    attempts = []
    monkeypatch.setattr(diagnostics.logger, "info", lambda template, *args: lines.append(template % args))
    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after-ms": "1", "x-request-id": "limited"}, json={"error": {"message": "rate limited", "type": "rate_limit"}})
        return httpx.Response(200, headers={"x-request-id": "recovered"}, json={
            "id": "reply", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
    client_type = openai_compat.DefaultAsyncHttpxClient
    monkeypatch.setattr(openai_compat, "DefaultAsyncHttpxClient", lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    async def run():
        provider = OpenAICompatProvider("dummy-key", "https://example.test/v1", "test")
        try:
            return await provider.chat([])
        finally:
            await provider.client.close()
    assert asyncio.run(run()).content == "ok"
    assert len(attempts) == 2
    assert any("status=429" in line for line in lines)
    assert any("requests_60s=2" in line and "sdk_retry=1" in line for line in lines)
    assert "dummy-key" not in "".join(lines)


@pytest.mark.parametrize("status,body", [(429, {"error": "rate limited"}), (500, {"error": "stream_options failed"}), (400, {"error": "invalid model"})])
def test_stream_does_not_retry_unrelated_error_as_compatibility(status, body):
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
        raise APIStatusError("request failed", response=response, body=body)
    with pytest.raises(APIStatusError):
        asyncio.run(collect(provider_for(create)))
    assert len(calls) == 1


def test_stream_retries_explicit_stream_options_rejection():
    calls = []
    async def stream():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=None, finish_reason="stop")], usage=None)
    async def create(**kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            response = httpx.Response(400, request=httpx.Request("POST", "https://example.test"))
            raise APIStatusError("unsupported parameter", response=response, body={"error": "stream_options unsupported"})
        return stream()
    assert asyncio.run(collect(provider_for(create)))[0].finish_reason == "stop"
    assert "stream_options" in calls[0] and "stream_options" not in calls[1]


def test_reasoning_only_length_response_is_visible_in_diagnostics(monkeypatch):
    lines = []
    monkeypatch.setattr(diagnostics.logger, "info", lambda template, *args: lines.append(template % args))
    async def stream():
        delta = SimpleNamespace(content=None, reasoning_content="private reasoning", tool_calls=None)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="length")], usage=SimpleNamespace(prompt_tokens=100, completion_tokens=8192))
    async def create(**kwargs):
        return stream()
    chunks = asyncio.run(collect(provider_for(create)))
    assert chunks[0].finish_reason == "length"
    assert chunks[0].reasoning_content == "private reasoning"
    assert any("reasoning_length" in line and "length" in line and "8192" in line for line in lines)
    assert "private reasoning" not in "".join(lines)


def test_stream_without_terminal_reason_is_reported(monkeypatch):
    lines = []
    monkeypatch.setattr(diagnostics.logger, "info", lambda template, *args: lines.append(template % args))
    async def stream():
        if False:
            yield
    async def create(**kwargs):
        return stream()
    with pytest.raises(RuntimeError, match="without finish_reason"):
        asyncio.run(collect(provider_for(create)))
    assert any("stream_observed" in line and "RuntimeError" in line for line in lines)
