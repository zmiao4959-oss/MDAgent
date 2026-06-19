"""
channels/adapter_manager.py — Channel Adapter 生命周期管理器
"""
import asyncio
from typing import Dict, List, Optional
from .base import BaseChannelAdapter
from ..logger import get_logger

logger = get_logger(__name__)


class AdapterManager:
    """管理所有 Channel Adapter 的启动、停止和消息路由"""

    def __init__(self):
        self._adapters: Dict[str, BaseChannelAdapter] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    def register(self, adapter: BaseChannelAdapter):
        """注册一个 channel adapter"""
        self._adapters[adapter.channel_name] = adapter
        logger.info("Registered channel adapter: %s", adapter.channel_name)

    def get(self, name: str) -> Optional[BaseChannelAdapter]:
        return self._adapters.get(name)

    def list_channels(self) -> List[str]:
        return sorted(self._adapters.keys())

    async def start_all(self, enabled_channels: List[str] = None):
        """启动所有（或指定的）channel adapter"""
        for name, adapter in self._adapters.items():
            if enabled_channels and name not in enabled_channels:
                logger.info("Skipping disabled channel: %s", name)
                continue
            logger.info("Starting channel: %s", name)
            task = asyncio.create_task(adapter.start(), name=f"channel-{name}")
            self._tasks[name] = task

    async def stop_all(self):
        """停止所有 channel adapter"""
        for name, adapter in self._adapters.items():
            logger.info("Stopping channel: %s", name)
            await adapter.stop()
        # 等待所有任务结束
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            self._tasks.clear()

    def set_message_handler(self, handler):
        """为所有 adapter 设置统一的消息处理器"""
        for adapter in self._adapters.values():
            adapter.set_message_handler(handler)
