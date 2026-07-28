"""Conversation lifecycle and export operations for WebChat."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from ...llm.base import LLMMessage
from ...logger import get_logger

if TYPE_CHECKING:
    from ...memory.session import SessionManager

logger = get_logger(__name__)


class ConversationService:
    """Provide a transport-independent API over WebChat sessions."""

    def __init__(self, session_manager: Optional["SessionManager"]) -> None:
        self.sessions = session_manager

    async def list(self) -> list[Dict[str, Any]]:
        if self.sessions is None:
            return []
        rows = self.sessions.list_conversations_by_channel("webchat")
        for row in rows:
            session = await self.sessions.resolve_session(row["chat_id"])
            if session and session.metadata.get("alias"):
                row["alias"] = session.metadata["alias"]
        return rows

    async def create(self) -> str:
        chat_id = _new_chat_id()
        if self.sessions is not None:
            session = self.sessions.get_or_create(chat_id, "webchat", "local")
            await self.sessions.save(session)
        return chat_id

    async def fork(self, chat_id: str, at_index: int = -1) -> Dict[str, Any]:
        if self.sessions is None:
            return _unavailable()
        source = await self.sessions.resolve_session(chat_id)
        if source is None:
            return {
                "ok": False,
                "error": f"Source conversation not found: {chat_id}",
            }

        new_chat_id = _new_chat_id()
        target = self.sessions.get_or_create(
            new_chat_id,
            "webchat",
            "local",
        )
        target.messages = list(
            source.messages[:at_index] if at_index >= 0 else source.messages
        )
        target.metadata = {
            **source.metadata,
            "forked_from": chat_id,
            "forked_at_index": at_index,
            "total_tokens": 0,
        }
        await self.sessions.save(target)
        logger.info(
            "Forked conversation %s → %s (at index %s)",
            chat_id,
            new_chat_id,
            at_index,
        )
        return {
            "ok": True,
            "chat_id": new_chat_id,
            "forked_from": chat_id,
            "message_count": len(target.messages),
        }

    async def rename(self, chat_id: str, alias: str) -> Dict[str, Any]:
        if self.sessions is None:
            return _unavailable()
        alias = alias.strip()
        if not alias:
            return {"ok": False, "error": "alias is required"}
        session = await self.sessions.resolve_session(chat_id)
        if session is None:
            return {
                "ok": False,
                "error": f"Conversation not found: {chat_id}",
            }
        session.metadata["alias"] = alias
        await self.sessions.save(session)
        logger.info("Renamed conversation %s → '%s'", chat_id, alias)
        return {"ok": True, "chat_id": chat_id, "alias": alias}

    async def delete(self, chat_id: str) -> Dict[str, Any]:
        if self.sessions is None:
            return _unavailable()
        try:
            await self.sessions.delete_session(chat_id)
            return {"ok": True, "chat_id": chat_id}
        except Exception as exc:
            logger.exception("Failed to delete conversation %s", chat_id)
            return {"ok": False, "error": str(exc)}

    async def history(self, chat_id: str) -> Dict[str, Any]:
        empty = {"messages": [], "chat_id": chat_id}
        if self.sessions is None:
            return empty
        session = await self.sessions.resolve_session(chat_id)
        if session is None:
            return empty

        messages = []
        for message in session.messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                name = message.name or "tool"
                snippet = (message.content or "")[:600]
                messages.append(
                    {
                        "role": "tool",
                        "content": f"[{name}] {snippet}",
                    }
                )
            else:
                messages.append(
                    {
                        "role": message.role,
                        "content": message.content or "",
                    }
                )
        viz_done = session.metadata.get("viz_done")
        return {
            "messages": messages,
            "chat_id": chat_id,
            "viz_done": viz_done if isinstance(viz_done, list) else [],
        }

    async def export_json(self, chat_id: str) -> Dict[str, Any]:
        session = await self._resolve_for_export(chat_id)
        if isinstance(session, dict):
            return session
        return {
            "chat_id": chat_id,
            "exported_at": datetime.now().isoformat(),
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "name": message.name,
                }
                for message in session.messages
            ],
        }

    async def export_markdown(self, chat_id: str) -> str | Dict[str, Any]:
        session = await self._resolve_for_export(chat_id)
        if isinstance(session, dict):
            return session

        lines = [
            "# MiniClaw 对话导出",
            "",
            f"- **Chat ID**: `{chat_id}`",
            f"- **导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **消息数**: {len(session.messages)}",
            "",
            "---",
            "",
        ]
        labels = {
            "user": "👤 用户",
            "assistant": "🤖 Agent",
            "tool": "🔧 工具",
        }
        for message in session.messages:
            if message.role == "system":
                continue
            lines.append(f"### {labels.get(message.role, message.role)}")
            if message.role == "tool" and message.name:
                lines.append(f"*工具: `{message.name}`*")
            lines.extend(["", message.content or "(空)", "", "---", ""])
        return "\n".join(lines)

    async def deliver(self, chat_id: str, text: str) -> None:
        if self.sessions is None:
            raise RuntimeError("WebChat session manager is not available")
        target = chat_id or "default"
        if not target.startswith("webchat:"):
            target = f"webchat:{target}"
        session = await self.sessions.resolve_session(target)
        if session is None:
            session = self.sessions.get_or_create(target, "webchat", "local")
        session.add_message(LLMMessage(role="assistant", content=text))
        await self.sessions.save(session)

    async def _resolve_for_export(self, chat_id: str):
        if self.sessions is None:
            return {"error": "session_manager not available"}
        session = await self.sessions.resolve_session(chat_id)
        if session is None:
            return {"error": "conversation not found", "chat_id": chat_id}
        return session


def _new_chat_id() -> str:
    return f"webchat:{uuid.uuid4().hex[:10]}"


def _unavailable() -> Dict[str, Any]:
    return {"ok": False, "error": "session_manager not available"}
