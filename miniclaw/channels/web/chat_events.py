"""Typed event transport between an Agent run and WebChat SSE."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional


class ChatEventStream:
    """Own subscription state and normalize all live chat event envelopes."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self.subscribed = True

    async def publish_delta(self, text: str) -> None:
        await self._publish({"kind": "delta", "text": text})

    async def publish_visualization(self, event: Dict[str, Any]) -> None:
        payload = dict(event)
        payload["kind"] = "visualization"
        await self._publish(payload)

    async def publish_progress(self, data: Dict[str, Any]) -> None:
        await self._publish({"kind": "progress", "data": dict(data)})

    async def publish_task(self, data: Dict[str, Any]) -> None:
        await self._publish({"kind": "task_event", "data": dict(data)})

    async def _publish(self, event: Dict[str, Any]) -> None:
        if self.subscribed:
            await self._queue.put(event)

    async def close(self) -> None:
        await self._queue.put(None)

    async def next(self, timeout: float) -> Optional[Dict[str, Any]]:
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    def unsubscribe(self) -> None:
        self.subscribed = False

    @staticmethod
    def sse_payload(event: Dict[str, Any]) -> Dict[str, Any]:
        kind = event.get("kind")
        if kind == "delta":
            return {"delta": event["text"]}
        if kind == "progress":
            return {"progress": event["data"]}
        if kind == "task_event":
            return {"task_event": event["data"]}
        if kind == "visualization":
            payload = dict(event)
            payload.pop("kind", None)
            payload["viz"] = True
            return payload
        raise ValueError(f"Unknown chat event kind: {kind}")
