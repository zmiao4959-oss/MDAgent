import json
from typing import List, Dict, Optional, AsyncIterator
from openai import AsyncOpenAI, APIStatusError, DefaultAsyncHttpxClient
from .diagnostics import HTTPDiagnostics, diagnose_request, log_response

from .base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    merge_stream_fragment,
)

# AsyncOpenAI 是 OpenAI 官方 Python SDK（v1.0+）提供的异步 HTTP 客户端
# 使用 AsyncOpenAI 可以方便地与 OpenAI API 进行异步交互，支持非阻塞的请求和响应处理，适用于需要高并发或异步操作的场景。
# 但这也意味着，消息的格式和工具调用的处理方式需要与 OpenAI API 的规范保持一致，以确保正确的请求和响应解析。
class OpenAICompatProvider(BaseLLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str):
        self.model = model
        diagnostics = HTTPDiagnostics()
        self.client = AsyncOpenAI(
            api_key=api_key, base_url=base_url,
            http_client=DefaultAsyncHttpxClient(event_hooks={
                "request": [diagnostics.on_request], "response": [diagnostics.on_response],
            }),
        )
    
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
    
    @diagnose_request("chat")
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
        log_response(
            "chat", response_id=resp.id, choices=len(resp.choices),
            request_id=getattr(resp, "_request_id", None),
            message_fields=[sorted(choice.message.model_dump(exclude_none=True)) for choice in resp.choices],
            finish_reasons=[choice.finish_reason for choice in resp.choices],
            content_lengths=[len(choice.message.content or "") for choice in resp.choices],
            reasoning_lengths=[len(getattr(choice.message, "reasoning_content", None) or "") for choice in resp.choices],
            tool_counts=[len(choice.message.tool_calls or []) for choice in resp.choices],
            usage=resp.usage.model_dump() if resp.usage else None,
        )
        if not resp.choices:
            raise RuntimeError("LLM API returned no choices; see LLM response diagnostics")
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
    
    @diagnose_request("stream")
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
        except APIStatusError as exc:
            # 只在明确不支持该字段时重发，不能把限流/连接失败当作兼容性问题。
            if exc.status_code not in {400, 422} or "stream_options" not in str(exc.body).lower():
                raise
            log_response("stream_options_rejected", status=exc.status_code)
            kwargs_req.pop("stream_options", None)
            stream = await self.client.chat.completions.create(**kwargs_req)
        
        # 流式 tool_calls + reasoning_content 需要累积 delta
        accumulated_tool_calls = {}
        accumulated_reasoning = ""
        chunk_count = content_length = reasoning_length = tool_fragments = 0
        finish_reasons = []
        last_usage = None
        delta_fields = set()

        async for chunk in stream:
            chunk_count += 1
            usage = {}
            if getattr(chunk, "usage", None):
                last_usage = chunk.usage.model_dump() if hasattr(chunk.usage, "model_dump") else vars(chunk.usage)
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                }
            choice = chunk.choices[0] if chunk.choices else None
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason:
                finish_reasons.append(finish_reason)
            delta = choice.delta if choice else None
            if not delta:
                if usage or finish_reason:
                    yield LLMStreamChunk(usage=usage, finish_reason=finish_reason)
                continue
            fields = delta.model_dump(exclude_none=True) if hasattr(delta, "model_dump") else vars(delta)
            delta_fields.update(fields)
            content_length += len(delta.content or "")

            # 捕获 reasoning_content (DeepSeek thinking mode 流式)
            reasoning_delta = getattr(delta, "reasoning_content", None) or ""
            if reasoning_delta:
                reasoning_length += len(reasoning_delta)
                accumulated_reasoning += reasoning_delta

            tool_calls_delta = []
            if delta.tool_calls:
                tool_fragments += len(delta.tool_calls)
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

            yield LLMStreamChunk(
                delta_content=delta.content or "",
                finish_reason=chunk.choices[0].finish_reason,
                usage=usage,
                reasoning_content=accumulated_reasoning if accumulated_reasoning else None,
            )
        log_response(
            "stream", chunks=chunk_count, finish_reasons=finish_reasons,
            content_length=content_length, reasoning_length=reasoning_length,
            tool_fragments=tool_fragments, tool_calls=len(accumulated_tool_calls), usage=last_usage,
            delta_fields=sorted(delta_fields),
        )
        if not finish_reasons:
            raise RuntimeError("LLM stream ended without finish_reason; see LLM response diagnostics")
        # 流式结束后，若有工具调用，一次性抛出
        if accumulated_tool_calls:
            final_tool_calls = list(accumulated_tool_calls.values())
            yield LLMStreamChunk(
                delta_content="",
                delta_tool_calls=final_tool_calls,
                finish_reason=finish_reasons[-1],
                reasoning_content=accumulated_reasoning if accumulated_reasoning else None,
            )
    
    def supports_tools(self) -> bool:
        return True
