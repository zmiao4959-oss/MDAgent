from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class LLMMessage:
    """一条对话消息"""
    role: str       # system | user | assistant | tool
    content: str    # 文本内容
    tool_call_id: Optional[str] = None       # tool 消息的 call_id
    tool_calls: Optional[List[Dict]] = None  # assistant 消息中的 tool_calls
    name: Optional[str] = None               # 工具名


@dataclass
class LLMResponse:
    """统一的 LLM 响应"""
    content: str = ""                   # 文本内容（流式输出时逐 chunk 累积）
    tool_calls: List[Dict] = field(default_factory=list)  # 工具调用
    finish_reason: str = "stop"         # stop | tool_calls | length
    usage: Dict[str, int] = field(default_factory=dict)  # {"prompt_tokens": N, "completion_tokens": M}
    raw_response: Any = None            # 原始 API 响应（调试用）


@dataclass
class LLMStreamChunk:
    """流式输出的一个 chunk"""
    delta_content: str = ""
    delta_tool_calls: List[Dict] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类"""
    # abstractmethod：是一个装饰器，标记某个方法为“抽象方法”，意思是：子类必须重写这个方法，否则子类也无法实例化
    @abstractmethod
    async def chat(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        """非流式对话"""
        ...
    
    @abstractmethod
    async def chat_stream(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncIterator[LLMStreamChunk]:
        """流式对话（生成器）"""
        ...
    
    @abstractmethod
    def supports_tools(self) -> bool:
        """是否支持 function calling"""
        ...