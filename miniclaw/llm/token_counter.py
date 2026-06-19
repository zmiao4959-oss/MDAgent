"""
llm/token_counter.py — 精确 Token 计数（tiktoken + fallback）

优先级：tiktoken > 字符估算
"""
from __future__ import annotations

import functools
from typing import List, Optional

from .base import LLMMessage
from ..logger import get_logger

logger = get_logger(__name__)

# 各模型的 tiktoken encoding 映射
_MODEL_ENCODING = {
    "gpt-4": "cl100k_base",
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "deepseek-chat": "cl100k_base",        # DeepSeek 兼容
    "deepseek-v4-pro": "cl100k_base",
    "deepseek-reasoner": "cl100k_base",
}

_FALLBACK_CHARS_PER_TOKEN = 3.2  # 比原来的 4.0 更精确


@functools.lru_cache(maxsize=8)
def _get_encoder(encoding_name: str):
    """缓存 tiktoken encoder 实例。"""
    try:
        import tiktoken
        return tiktoken.get_encoding(encoding_name)
    except (ImportError, Exception):
        return None


def _encoding_for_model(model: str) -> str:
    """根据模型名选择 encoding。"""
    for prefix, enc in _MODEL_ENCODING.items():
        if model.startswith(prefix):
            return enc
    return "cl100k_base"


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """统计单段文本的 token 数。"""
    if not text:
        return 0
    encoding_name = _encoding_for_model(model)
    encoder = _get_encoder(encoding_name)
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass
    # fallback：更精确的字符估算（考虑了中文等宽字符）
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    wide_chars = len(text) - ascii_chars
    return int(ascii_chars / 4 + wide_chars / 1.5)


def count_message_tokens(msg: LLMMessage, model: str = "gpt-4") -> int:
    """统计一条消息的 token 数（含 role 开销）。"""
    total = 4  # 每条消息固定 role 开销
    total += count_tokens(msg.content or "", model)
    if msg.tool_calls:
        for tc in msg.tool_calls:
            fn = tc.get("function", {})
            total += count_tokens(fn.get("name", ""), model)
            total += count_tokens(fn.get("arguments", ""), model)
            total += 3  # tool_call 结构开销
    if msg.tool_call_id:
        total += count_tokens(msg.tool_call_id, model) + 2
    if msg.name:
        total += count_tokens(msg.name, model) + 1
    return total


def count_messages_tokens(messages: List[LLMMessage], model: str = "gpt-4") -> int:
    """统计消息列表的总 token 数。"""
    total = 3  # 每次请求的固定开销
    for m in messages:
        total += count_message_tokens(m, model)
    return total


def estimate_tokens_fast(text: str) -> int:
    """无 tiktoken 时的快速估算，比 len/4 更精确。"""
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    wide_chars = len(text) - ascii_chars
    return int(ascii_chars / _FALLBACK_CHARS_PER_TOKEN + wide_chars / 1.5)
