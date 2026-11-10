"""Input queued for a single Agent invocation."""
from __future__ import annotations

from uuid import uuid4

from ..llm.base import LLMMessage


class SteeringInbox:
    """Synchronous enqueue, drain, and close operations on the run's event loop."""

    def __init__(self):
        self.run_id = uuid4().hex
        self.started = False
        self.closed = False
        self._pending: list[LLMMessage] = []
        self._received: set[str] = set()

    def offer(self, message_id: str, content: str) -> bool:
        if message_id in self._received:
            return True
        if self.closed:
            return False
        self._received.add(message_id)
        self._pending.append(LLMMessage(role="user", content=content))
        return True

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def drain(self) -> list[LLMMessage]:
        pending, self._pending = self._pending, []
        return pending

    def close(self) -> None:
        self.closed = True
