"""LLM 请求诊断：只记录计数、状态和结束原因，不记录密钥或对话正文。"""
from collections import deque
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from time import monotonic
from uuid import uuid4

from ..logger import get_logger

logger = get_logger(__name__)
_trace_id = ContextVar("llm_trace_id", default="unknown")


def diagnose_request(mode):
    """关联一次逻辑模型调用，包括 SDK 内部的 HTTP 重试。"""
    def decorate(function):
        parameters = signature(function)

        def begin(args, kwargs):
            bound = parameters.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            token = _trace_id.set(uuid4().hex[:12])
            logger.info(
                "LLM start trace=%s mode=%s model=%s messages=%s tools=%s max_tokens=%s",
                _trace_id.get(), mode, args[0].model, len(values["messages"]),
                len(values.get("tools") or []), values["max_tokens"],
            )
            return token, monotonic()

        def end(token, started, error):
            logger.info("LLM end trace=%s elapsed=%.2fs error=%s", _trace_id.get(), monotonic() - started, error)
            _trace_id.reset(token)

        if mode == "stream":
            @wraps(function)
            async def stream(*args, **kwargs):
                token, started = begin(args, kwargs)
                error = None
                chunks = content_chars = tool_calls = reasoning_chars = 0
                finish_reason = None
                last_chunk = None
                try:
                    async for chunk in function(*args, **kwargs):
                        chunks += 1
                        content_chars += len(chunk.delta_content or "")
                        tool_calls += len(chunk.delta_tool_calls or [])
                        reasoning_chars = max(reasoning_chars, len(chunk.reasoning_content or ""))
                        finish_reason = chunk.finish_reason or finish_reason
                        last_chunk = monotonic()
                        yield chunk
                except BaseException as exc:
                    error = type(exc).__name__
                    raise
                finally:
                    log_response(
                        "stream_observed", chunks=chunks, content_chars=content_chars,
                        reasoning_chars=reasoning_chars, tool_calls=tool_calls,
                        finish_reason=finish_reason,
                        last_chunk_elapsed=round(last_chunk - started, 2) if last_chunk else None,
                        error=error,
                    )
                    end(token, started, error)
            return stream

        @wraps(function)
        async def chat(*args, **kwargs):
            token, started = begin(args, kwargs)
            error = None
            try:
                return await function(*args, **kwargs)
            except BaseException as exc:
                error = type(exc).__name__
                raise
            finally:
                end(token, started, error)
        return chat
    return decorate


class HTTPDiagnostics:
    """统计同一 provider 实例的真实 HTTP 请求，包含 SDK 隐式重试。"""
    def __init__(self):
        self.recent = deque()

    async def on_request(self, request):
        now = monotonic()
        while self.recent and self.recent[0] <= now - 60:
            self.recent.popleft()
        self.recent.append(now)
        request.extensions["miniclaw_started"] = now
        request.extensions["miniclaw_trace"] = _trace_id.get()
        logger.info("LLM HTTP send trace=%s host=%s requests_60s=%s sdk_retry=%s", _trace_id.get(), request.url.host, len(self.recent), request.headers.get("x-stainless-retry-count", "unknown"))

    async def on_response(self, response):
        request = response.request
        headers = {
            key: response.headers[key] for key in (
                "x-request-id", "request-id", "retry-after", "retry-after-ms",
                "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
                "x-ratelimit-reset-requests", "x-ratelimit-limit-tokens",
                "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens",
            ) if key in response.headers
        }
        logger.info(
            "LLM HTTP response trace=%s status=%s headers_elapsed=%.2fs headers=%s",
            request.extensions.get("miniclaw_trace", "unknown"), response.status_code,
            monotonic() - request.extensions.get("miniclaw_started", monotonic()), headers,
        )


def log_response(mode, **summary):
    logger.info("LLM response trace=%s mode=%s summary=%s", _trace_id.get(), mode, summary)
