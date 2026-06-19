"""
tools/message_tool.py — 跨渠道消息发送工具

允许 Agent 主动向指定 channel/chat 发送消息。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .registry import tool_registry
from ..logger import get_logger

logger = get_logger(__name__)


# 全局消息回调注册表（由各 channel adapter 在启动时注册）
_send_callbacks: Dict[str, Any] = {}


def register_send_callback(channel: str, callback):
    """注册某渠道的消息发送回调。"""
    _send_callbacks[channel] = callback
    logger.debug("Registered send callback for channel: %s", channel)


def unregister_send_callback(channel: str):
    _send_callbacks.pop(channel, None)


@tool_registry.register(
    name="send_message",
    description="Send a message to a specific channel or chat.",
    schema={
        "type": "function",
        "function": {
            "name": "send_message",
            "description": (
                "Send a text message to a target channel/chat. "
                "Useful for proactive notifications, cron job results, "
                "or cross-channel communication."
            ),
            "parameters": {
                "type": "object",
                "required": ["message"],
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message text to send",
                    },
                    "channel": {
                        "type": "string",
                        "description": "Target channel: webchat, telegram, etc. (default: webchat)",
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "Target chat ID (default: uses the current context)",
                    },
                },
            },
        },
    },
    require_approval=False,
    risk_level="medium",
    tags=["communication"],
)
async def send_message_tool(
    message: str,
    channel: str = "webchat",
    chat_id: Optional[str] = None,
    **kwargs,
) -> str:
    if not message or not message.strip():
        return "Error: message is required"

    callback = _send_callbacks.get(channel)
    if callback is None:
        available = sorted(_send_callbacks.keys()) or ["(none registered)"]
        return (
            f"Error: no send callback registered for channel '{channel}'. "
            f"Available: {', '.join(available)}"
        )

    try:
        target = chat_id or "default"
        await callback(target, message)
        return f"Message sent to {channel}:{target} ({len(message)} chars)"
    except Exception as e:
        logger.exception("send_message failed for %s:%s", channel, chat_id)
        return f"Error sending message: {e}"
