"""
channels 包 — 多渠道接入层
"""
from .base import BaseChannelAdapter
from .adapter_manager import AdapterManager

__all__ = [
    "BaseChannelAdapter",
    "AdapterManager",
]
