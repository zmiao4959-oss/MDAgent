import json
from typing import List, Dict, Optional, AsyncIterator
from openai import AsyncOpenAI

from .base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    merge_stream_fragment,
)


class OpenAICompatProvider(BaseLLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str):
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    
    def _to_openai_messages(self, messages: List[LLMMessage]) -> List[Dict]:
        """将内部的 LLMMessage 转为 OpenAI API 格式（包含 reasoning_content 回传）"""
        result = []
        for m in messages:
            msg = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = m.tool_calls
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.name:
                msg["name"] = m.name
            # DeepSeek thinking mode: 必须把 reasoning_content 回传给 API
            if m.reasoning_content:
                msg["reasoning_content"] = m.reasoning_content
            result.append(msg)
        return result
    
    async def chat(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        kwargs_req = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs_req["tools"] = tools
        
        resp = await self.client.chat.completions.create(**kwargs_req)
        choice = resp.choices[0]

        # 捕获 DeepSeek thinking mode 的 reasoning_content
        reasoning = getattr(choice.message, "reasoning_content", None) or None

        return LLMResponse(
            content=choice.message.content or "",
            tool_calls=[
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in (choice.message.tool_calls or [])
            ],
            finish_reason=choice.finish_reason or "stop",
            usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
            raw_response=resp,
            reasoning_content=reasoning,
        )
    
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncIterator[LLMStreamChunk]:
        kwargs_req = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs_req["tools"] = tools
        
        try:
            stream = await self.client.chat.completions.create(**kwargs_req)
        except Exception:
            # 部分 OpenAI 兼容端点不支持 stream_options
            kwargs_req.pop("stream_options", None)
            stream = await self.client.chat.completions.create(**kwargs_req)
        
        # 流式 tool_calls + reasoning_content 需要累积 delta
        accumulated_tool_calls = {}
        accumulated_reasoning = ""

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            # 捕获 reasoning_content (DeepSeek thinking mode 流式)
            reasoning_delta = getattr(delta, "reasoning_content", None) or ""
            if reasoning_delta:
                accumulated_reasoning += reasoning_delta

            tool_calls_delta = []
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in accumulated_tool_calls:
                        accumulated_tool_calls[idx] = {
                            "index": idx,
                            "id": tc_delta.id or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc_delta.id:
                        accumulated_tool_calls[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        # name 是原子字段（非流式），用赋值避免重复拼接
                        if tc_delta.function.name:
                            accumulated_tool_calls[idx]["function"]["name"] = tc_delta.function.name
                        # arguments 是流式字段，需要拼接
                        if tc_delta.function.arguments:
                            accumulated_tool_calls[idx]["function"]["arguments"] = merge_stream_fragment(
                                accumulated_tool_calls[idx]["function"]["arguments"],
                                tc_delta.function.arguments,
                            )

            usage = {}
            if getattr(chunk, "usage", None):
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                }
            yield LLMStreamChunk(
                delta_content=delta.content or "",
                finish_reason=chunk.choices[0].finish_reason,
                usage=usage,
                reasoning_content=accumulated_reasoning if accumulated_reasoning else None,
            )
        # 流式结束后，若有工具调用，一次性抛出
        if accumulated_tool_calls:
            final_tool_calls = list(accumulated_tool_calls.values())
            yield LLMStreamChunk(
                delta_content="",
                delta_tool_calls=final_tool_calls,
                finish_reason="tool_calls",
                reasoning_content=accumulated_reasoning if accumulated_reasoning else None,
            )
    
    def supports_tools(self) -> bool:
        return True
