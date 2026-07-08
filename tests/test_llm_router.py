import asyncio

from miniclaw.llm.base import LLMResponse, LLMStreamChunk, merge_stream_fragment
from miniclaw.llm.router import LLMRouter


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
