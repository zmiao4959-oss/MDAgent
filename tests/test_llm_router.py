import asyncio
import httpx

from miniclaw.llm.base import LLMResponse, LLMStreamChunk, merge_stream_fragment
from miniclaw.llm.router import LLMRouter, _is_retryable


class _FailsBeforeOutput:
    async def chat_stream(self, *args, **kwargs):
        raise RuntimeError("connection failed")
        yield  # pragma: no cover


class _PartialThenFails:
    async def chat_stream(self, *args, **kwargs):
        yield LLMStreamChunk(delta_content="partial")
        raise RuntimeError("connection failed")


class _Fallback:
    async def chat(self, *args, **kwargs):
        return LLMResponse(content="fallback")


async def _collect(router):
    return [chunk async for chunk in router.chat_stream([])]


def test_stream_uses_fallback_when_primary_fails_before_output():
    router = object.__new__(LLMRouter)
    router.providers = [_FailsBeforeOutput(), _Fallback()]

    chunks = asyncio.run(_collect(router))

    assert [chunk.delta_content for chunk in chunks] == ["fallback"]


def test_stream_does_not_duplicate_after_partial_primary_output():
    router = object.__new__(LLMRouter)
    router.providers = [_PartialThenFails(), _Fallback()]

    try:
        asyncio.run(_collect(router))
    except RuntimeError as error:
        assert "connection failed" in str(error)
    else:  # pragma: no cover
        raise AssertionError("an interrupted partial stream must not fall back")


def test_merge_stream_fragment_accepts_cumulative_snapshots():
    merged = ""
    merged = merge_stream_fragment(merged, "{\"path\":")
    merged = merge_stream_fragment(merged, "{\"path\": \"C:/tmp")
    merged = merge_stream_fragment(merged, "{\"path\": \"C:/tmp/file.txt\"}")

    assert merged == "{\"path\": \"C:/tmp/file.txt\"}"


def test_merge_stream_fragment_accepts_true_deltas():
    merged = ""
    merged = merge_stream_fragment(merged, "{\"path\":")
    merged = merge_stream_fragment(merged, " \"C:/tmp")
    merged = merge_stream_fragment(merged, "/file.txt\"}")

    assert merged == "{\"path\": \"C:/tmp/file.txt\"}"


def test_stream_fallback_preserves_tool_calls_reasoning_and_usage():
    response = LLMResponse(
        tool_calls=[{"id": "recovered", "type": "function", "function": {"name": "execute", "arguments": '{"command":"echo recovered"}'}}],
        finish_reason="tool_calls", usage={"prompt_tokens": 100, "completion_tokens": 20},
        reasoning_content="fallback reasoning",
    )
    class Fallback:
        async def chat(self, *args, **kwargs):
            return response
    router = object.__new__(LLMRouter)
    router.providers = [_FailsBeforeOutput(), Fallback()]
    chunks = asyncio.run(_collect(router))
    assert len(chunks) == 1
    assert chunks[0].delta_tool_calls == response.tool_calls
    assert chunks[0].reasoning_content == response.reasoning_content
    assert chunks[0].usage == response.usage
    assert chunks[0].finish_reason == "tool_calls"


def test_incomplete_chunked_response_is_retryable():
    assert _is_retryable(httpx.RemoteProtocolError("peer closed connection without sending complete message body (incomplete chunked read)"))


def test_provider_keeps_usage_only_terminal_chunk():
    from types import SimpleNamespace
    from miniclaw.llm.openai_compat import OpenAICompatProvider

    async def stream():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=None, finish_reason="stop")], usage=None)
        yield SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45))

    async def create(**kwargs):
        return stream()

    provider = object.__new__(OpenAICompatProvider)
    provider.model = "test"
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    chunks = asyncio.run(_collect(provider))
    assert len(chunks) == 2
    assert chunks[0].finish_reason == "stop"
    assert chunks[1].usage == {"prompt_tokens": 123, "completion_tokens": 45}
