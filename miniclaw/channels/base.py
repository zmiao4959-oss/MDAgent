"""
channels/base.py — Channel Adapter 抽象基类
"""
from abc import ABC, abstractmethod
from typing import Optional, Callable


class BaseChannelAdapter(ABC):
    """所有 Channel Adapter 的基类"""
    
    def __init__(self, channel_name: str):
        self.channel_name = channel_name
        self._message_handler: Optional[Callable] = None
    
    def set_message_handler(self, handler: callable):
        """设置消息处理器: async def handler(context: AgentContext) -> str"""
        self._message_handler = handler
    
    @abstractmethod
    async def start(self):
        """启动此 channel 的监听"""
        ...
    
    @abstractmethod
    async def stop(self):
        """停止此 channel"""
        ...
    
    @abstractmethod
    async def send_message(self, chat_id: str, text: str, **kwargs):
        """向指定 chat_id 发送消息"""
        ...