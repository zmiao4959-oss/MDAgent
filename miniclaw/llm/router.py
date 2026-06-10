from typing import List, Dict, Optional, AsyncIterator
from .base import BaseLLMProvider, LLMMessage, LLMResponse, LLMStreamChunk
from .openai_compat import OpenAICompatProvider
from ..config import LLMConfig
from ..logger import get_logger

logger = get_logger(__name__)


class LLMRouter:
    """管理多个 LLM Provider，支持降级"""
    
    def __init__(self, primary_config: LLMConfig, fallback_configs: List[LLMConfig] = None):
        self.providers: List[BaseLLMProvider] = []
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
    
    async def chat(
        self, messages, tools=None, temperature=0.7, max_tokens=4096
    ) -> LLMResponse:
        """依次尝试 providers，失败则降级"""
        last_error = None
        for i, provider in enumerate(self.providers):
            try:
                return await provider.chat(messages, tools, temperature, max_tokens)
            except Exception as e:
                logger.warning(f"Provider {i} failed: {e}")
                last_error = e
        raise RuntimeError(f"All providers failed. Last error: {last_error}")
    
    async def chat_stream(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        """流式调用（仅主 provider，降级不流式）"""
        try:
            async for chunk in self.providers[0].chat_stream(messages, tools, temperature, max_tokens):
                yield chunk
        except Exception:
            response = await self.chat(messages, tools, temperature, max_tokens)
            # 将非流式结果作为单个 chunk 返回
            yield LLMStreamChunk(delta_content=response.content, finish_reason=response.finish_reason)