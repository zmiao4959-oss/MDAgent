"""Public channel members over the existing Agent and registered tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial

from ..agent import Agent, AgentContext
from ..llm.router import LLMRouter
from ..memory.session import SessionManager
from ..runtime.room_context import MAX_PUBLIC_MESSAGE_CHARS, PUBLISH_CONTEXT_KEY
from ..settings import config
from .catalog import AgentCatalog


@dataclass(eq=False)
class Member:
    single_agent: Agent
    chat_id: str
    inbox: list[str] = field(default_factory=list)
    is_running: bool = False
    loop_times: int = 0


class Room:
    def __init__(self, profiles: list[str], room_id: str, *, llm=None, sessions=None, catalog=None, workspace_dir=None):
        if not room_id or not profiles or len(set(profiles)) != len(profiles):
            raise ValueError("A room needs an ID and unique members")
        router = llm if llm is not None else LLMRouter(config.llm, config.llm.fallback_providers)
        self.sessions = sessions if sessions is not None else SessionManager(config.paths.sessions)
        catalog = catalog if catalog is not None else AgentCatalog()
        self.members = [Member(Agent(router, self.sessions, catalog.require(name)), f"room:{room_id}:{name}") for name in profiles]
        self.public_message: list[str] = []
        self.workspace_dir = workspace_dir

    def broadcast(self, member: Member, message: str):
        for recipient in self.members:
            if recipient is not member:
                recipient.inbox.append(message)

    def publish_message(self, member: Member, message: str):
        if member not in self.members:
            raise ValueError("Sender is not a room member")
        if not isinstance(message, str) or not message.strip() or len(message) > MAX_PUBLIC_MESSAGE_CHARS:
            raise ValueError("Message must be a non-blank string of at most 32000 characters")
        self.public_message.append(message)
        self.broadcast(member, message)
        return {"status": "published"}

    async def member_loop(self):
        for member in self.members:
            if member.is_running or not member.inbox:
                continue
            message = "\n".join(member.inbox)
            member.inbox.clear()
            member.is_running = True
            try:
                await member.single_agent.process_message(AgentContext(
                    chat_id=member.chat_id, channel="agent_room", account_id="system", user_message=message,
                    metadata={PUBLISH_CONTEXT_KEY: partial(self.publish_message, member), "workspace_dir": self.workspace_dir},
                ))
                member.loop_times += 1
            finally:
                member.is_running = False


room = Room
