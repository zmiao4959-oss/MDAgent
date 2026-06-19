import asyncio
from typing import List, Dict, Optional, AsyncIterator
from .base import BaseLLMProvider, LLMMessage, LLMResponse, LLMStreamChunk
from .openai_compat import OpenAICompatProvider
from ..config import LLMConfig
from ..logger import get_logger

logger = get_logger(__name__)

# 重试配置
MAX_RETRIES_PER_PROVIDER = 3
BASE_RETRY_DELAY = 0.5  # 秒
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _is_retryable(error: Exception) -> bool:
    """判断异常是否可重试（速率限制 / 服务端临时错误）。"""
    msg = str(error).lower()
    retryable_keywords = [
        "rate limit", "too many requests", "429",
        "server error", "500", "502", "503", "504",
        "timeout", "connection reset", "service unavailable",
        "overloaded", "capacity", "temporarily",
    ]
    return any(kw in msg for kw in retryable_keywords)


class LLMRouter:
    """管理多个 LLM Provider，支持重试 + 降级"""

    def __init__(self, primary_config: LLMConfig, fallback_configs: List[LLMConfig] = None):
        self.providers: List[BaseLLMProvider] = []
        self.primary_config = primary_config
        # 主 provider
        self.providers.append(
            OpenAICompatProvider(
                api_key=primary_config.resolved_api_key,
                base_url=primary_config.base_url,
                model=primary_config.model,
            )
        )
        # 降级 providers
        for fb in (fallback_configs or []):
            self.providers.append(
                OpenAICompatProvider(
                    api_key=fb.api_key or "",
                    base_url=fb.base_url,
                    model=fb.model,
                )
            )

    async def _call_with_retry(self, provider, messages, tools, temperature, max_tokens):
        """对单个 provider 做指数退避重试。"""
        last_error = None
        for attempt in range(MAX_RETRIES_PER_PROVIDER):
            try:
                return await provider.chat(messages, tools, temperature, max_tokens)
            except Exception as e:
                last_error = e
                if not _is_retryable(e) or attempt == MAX_RETRIES_PER_PROVIDER - 1:
                    raise
                delay = BASE_RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    "Provider retry %s/%s after %.1fs: %s",
                    attempt + 1, MAX_RETRIES_PER_PROVIDER, delay, e,
                )
                await asyncio.sleep(delay)
        raise last_error  # type: ignore[misc]

    async def chat(
        self, messages, tools=None, temperature=0.7, max_tokens=4096
    ) -> LLMResponse:
        """依次尝试 providers（每个 provider 内部重试），失败则降级"""
        last_error = None
        for i, provider in enumerate(self.providers):
            try:
                return await self._call_with_retry(
                    provider, messages, tools, temperature, max_tokens
                )
            except Exception as e:
                label = "primary" if i == 0 else f"fallback-{i}"
                logger.warning("%s provider failed: %s", label, e)
                last_error = e
        raise RuntimeError(f"All providers failed. Last error: {last_error}")

    async def chat_stream(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        """流式调用（仅主 provider，降级自动 fallback 到非流式）"""
        try:
            async for chunk in self.providers[0].chat_stream(
                messages, tools, temperature, max_tokens
            ):
                yield chunk
        except Exception as e:
            logger.warning("Stream failed (%s), falling back to non-streaming", e)
            response = await self.chat(messages, tools, temperature, max_tokens)
            yield LLMStreamChunk(
                delta_content=response.content,
                finish_reason=response.finish_reason,
            )