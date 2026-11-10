"""Select collaboration per conversation, without changing shared tool state."""
from __future__ import annotations

from copy import deepcopy

from ..runtime.steering import SteeringInbox
from .graph import AgentGraphRuntime
from .room_runtime import RoomRuntime

MODES = {"default", "graph", "room"}


class CollaborationRuntime:
    def __init__(self, default_agent, catalog, *, graph_runtime=None):
        self.default_agent, self.catalog = default_agent, catalog
        self.sessions = default_agent.sessions
        self.graph_runtime = graph_runtime or AgentGraphRuntime(default_agent, catalog)
        self.room_runtime = RoomRuntime(default_agent, catalog)
        self._active_chats: dict[str, SteeringInbox | None] = {}

    def is_running(self, chat_id):
        return chat_id in self._active_chats or self.graph_runtime.is_running(chat_id) or self.room_runtime.is_running(chat_id)

    def steering_run_id(self, chat_id: str) -> str | None:
        inbox = self._active_chats.get(chat_id)
        if inbox is not None and inbox.started and not inbox.closed:
            return inbox.run_id
        return self.room_runtime.steering_run_id(chat_id)

    async def steer(self, chat_id: str, run_id: str, message_id: str, text: str) -> bool:
        inbox = self._active_chats.get(chat_id)
        if inbox is not None:
            return inbox.run_id == run_id and inbox.started and inbox.offer(message_id, text)
        return await self.room_runtime.steer(chat_id, run_id, message_id, text)

    def snapshot(self, metadata):
        mode = metadata.get("collaboration_mode")
        if mode is None:
            mode = "graph" if metadata.get("agent_graph") is not None else "default"
        if mode not in MODES:
            raise ValueError("协作模式必须是 default、graph 或 room")
        result = {"collaboration_mode": mode}
        if mode == "graph":
            graph = self.graph_runtime.validate(metadata.get("agent_graph"))
            if graph is None:
                raise ValueError("请先配置智能体画布")
            result["agent_graph"] = graph.to_dict()
        elif mode == "room":
            result["agent_room"] = self.room_runtime.validate(metadata.get("agent_room")).to_dict()
        return deepcopy(result)

    async def snapshot_for_chat(self, chat_id):
        session = await self.sessions.resolve_session(chat_id)
        return self.snapshot(session.metadata if session else {})

    async def process_message(self, context, **callbacks):
        if self.is_running(context.chat_id):
            raise RuntimeError("当前会话已有正在运行的任务")
        self._active_chats[context.chat_id] = None
        try:
            if "collaboration_mode" in context.metadata:
                snapshot = self.snapshot(context.metadata)
            else:
                session = await self.sessions.resolve_session(context.chat_id)
                values = dict(session.metadata if session else {})
                values.update({key: context.metadata[key] for key in ("agent_graph", "agent_room") if key in context.metadata})
                snapshot = self.snapshot(values)
            context.metadata.update(snapshot)
            mode = snapshot["collaboration_mode"]
            if mode != "default" and context.metadata.get("plan_mode"):
                raise ValueError("画布与公共频道暂不支持计划模式")
            if mode == "default":
                context.steering = SteeringInbox()
                self._active_chats[context.chat_id] = context.steering
            target = {"default": self.default_agent, "graph": self.graph_runtime, "room": self.room_runtime}[mode]
            return await target.process_message(context, **callbacks)
        finally:
            self._active_chats.pop(context.chat_id, None)
