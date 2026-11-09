"""Broadcast collaboration: private Agent sessions and a persistent public log."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import partial
import hashlib
import json
import time
import uuid

from ..agent import Agent, AgentContext
from ..llm.base import LLMMessage
from ..runtime.room_context import MAX_PUBLIC_MESSAGE_CHARS, PUBLISH_CONTEXT_KEY


@dataclass(frozen=True)
class RoomConfig:
    members: tuple[str, ...]
    room_id: str = "public"
    max_concurrency: int = 4
    max_messages: int = 24
    max_activations: int = 100
    timeout_sec: int = 600

    @classmethod
    def parse(cls, raw, catalog):
        if not isinstance(raw, dict) or raw.get("version", 1) != 1:
            raise ValueError("公共频道需要 version=1 的配置")
        names = raw.get("members")
        if not isinstance(names, list) or not 1 <= len(names) <= 32 or any(not isinstance(n, str) for n in names):
            raise ValueError("请选择 1–32 个频道成员")
        if len(set(names)) != len(names):
            raise ValueError("频道成员不能重复")
        if "user" in names:
            raise ValueError("user 是公共频道保留的消息身份")
        for name in names:
            catalog.require(name)
        room_id = raw.get("room_id", "public")
        if not isinstance(room_id, str) or not room_id.strip() or len(room_id) > 80:
            raise ValueError("频道编号需要 1–80 个字符")
        values = {}
        for key, default, maximum in (("max_concurrency", 4, 16), ("max_messages", 24, 2000), ("max_activations", 100, 4000), ("timeout_sec", 600, 86400)):
            value = raw.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{key} 必须是 1–{maximum} 的整数")
            values[key] = value
        return cls(tuple(names), room_id.strip(), **values)

    def to_dict(self):
        return {"version": 1, "members": list(self.members), "room_id": self.room_id,
                "max_concurrency": self.max_concurrency, "max_messages": self.max_messages,
                "max_activations": self.max_activations, "timeout_sec": self.timeout_sec}

    @property
    def fingerprint(self):
        value = json.dumps([self.room_id, sorted(self.members)], ensure_ascii=False)
        return hashlib.sha256(value.encode()).hexdigest()[:20]


@dataclass(eq=False)
class RoomActor:
    name: str
    agent: Agent
    chat_id: str
    inbox: list[dict] = field(default_factory=list)
    token: object | None = None


class RoomRun:
    def __init__(self, runtime, settings, context, session, callbacks):
        self.runtime, self.settings, self.context, self.session = runtime, settings, context, session
        scope = json.dumps([context.chat_id, context.metadata.get("project_id", ""), settings.fingerprint])
        self.namespace = hashlib.sha256(scope.encode()).hexdigest()[:24]
        self.actors = [RoomActor(name, Agent(runtime.default_agent.llm, runtime.sessions, profile=runtime.catalog.require(name)), f"agentroom:{self.namespace}:{name}") for name in settings.members]
        self.channel = session.metadata.setdefault("room_channels", {}).setdefault(settings.fingerprint, {"sequence": 0, "messages": []})
        self.record = {"run_id": uuid.uuid4().hex, "status": "running", "config": settings.to_dict(),
                       "started_at": time.time(), "events": [], "messages": [], "activations": 0, "published": 0}
        self.callbacks = callbacks
        self.running: dict[asyncio.Task, RoomActor] = {}
        self.wakeup = asyncio.Event()
        self.publish_lock = asyncio.Lock()
        self.closed = False
        self.limited = False
        self.activation_limited = False
        self.cursor = 0

    async def emit(self, kind, **data):
        event = {"run_id": self.record["run_id"], "event_id": len(self.record["events"]) + 1, "kind": kind, "time": time.time(), **data}
        self.record["events"].append(event)
        callback = self.context.metadata.get("_on_room_event")
        if callback:
            await callback(event)

    def _append_message(self, author, content, causes=()):
        self.channel["sequence"] += 1
        seq = self.channel["sequence"]
        item = {"message_id": f"{self.namespace}:{seq}", "sequence": seq, "author": author, "content": content,
                "time": time.time(), "causes": list(causes), "run_id": self.record["run_id"]}
        self.channel["messages"].append(item)
        # Keep a bounded recent transcript; monotonically increasing IDs survive pruning.
        del self.channel["messages"][:-200]
        self.record["messages"].append(item)
        for actor in self.actors:
            if actor.name != author:
                actor.inbox.append(item)
        self.wakeup.set()
        return item

    async def publish(self, actor, token, causes, message):
        async with self.publish_lock:
            if self.closed or actor.token is not token:
                return "Error: this room turn has ended"
            if not isinstance(message, str) or not message.strip() or len(message) > MAX_PUBLIC_MESSAGE_CHARS:
                return "Error: invalid public message"
            if self.record["published"] >= self.settings.max_messages:
                if not self.limited:
                    await self.emit("limit", reason="公开消息预算已耗尽")
                self.limited = True
                return "Error: public message budget exhausted; finish this turn"
            self.record["published"] += 1
            item = self._append_message(actor.name, message, causes)
            await self.runtime.sessions.save(self.session)
            await self.emit("message", message=item)
            stream = self.callbacks.get("on_stream_chunk")
            if stream:
                await stream(f"**{actor.name}**\n\n{message}\n\n---\n\n")
            return {"status": "published", "message_id": item["message_id"], "remaining": self.settings.max_messages - self.record["published"]}

    async def activate(self, actor, token, batch):
        await self.emit("member_started", member=actor.name, messages=[item["message_id"] for item in batch])
        metadata = {key: self.context.metadata[key] for key in ("workspace_dir", "project_id", "project_title", "project_objective", "approved", "_on_task_event") if key in self.context.metadata}
        metadata.update(parent_session_id=self.session.session_id,
                        root_session_id=self.session.metadata.get("root_session_id", self.session.session_id),
                        task_id=f"room-{self.namespace}-{actor.name}",
                        **{PUBLISH_CONTEXT_KEY: partial(self.publish, actor, token, tuple(item["message_id"] for item in batch))})
        content = "\n\n".join(f"[Public message #{item['sequence']} from {item['author']}; id={item['message_id']}]\n{item['content']}" for item in batch)
        ctx = AgentContext(actor.chat_id, "agent_room", self.context.account_id, content, metadata)
        try:
            call = actor.agent.process_message(ctx)
            timeout = actor.agent.profile.runtime.timeout_sec
            if timeout is not None:
                await asyncio.wait_for(call, timeout)
            else:
                await call
            await self.emit("member_finished", member=actor.name)
        except Exception as exc:
            await self.emit("member_error", member=actor.name, reason=str(exc))
            raise
        finally:
            actor.token = None

    async def schedule(self):
        while True:
            self.wakeup.clear()
            count = len(self.actors)
            start = self.cursor
            for offset in range(count):
                index = (start + offset) % count
                actor = self.actors[index]
                if actor.token is not None or not actor.inbox or len(self.running) >= self.settings.max_concurrency:
                    continue
                if self.record["activations"] >= self.settings.max_activations:
                    self.limited = True
                    if not self.activation_limited:
                        self.activation_limited = True
                        await self.emit("limit", reason="成员激活预算已耗尽")
                    continue
                batch, actor.inbox = actor.inbox, []
                token = object()
                actor.token = token
                self.record["activations"] += 1
                task = asyncio.create_task(self.activate(actor, token, batch), name=f"room:{self.namespace}:{actor.name}")
                self.running[task] = actor
                self.cursor = (index + 1) % count
            if not self.running:
                if any(actor.inbox for actor in self.actors) and not self.activation_limited:
                    continue
                return
            wake = asyncio.create_task(self.wakeup.wait())
            try:
                done, _ = await asyncio.wait([*self.running, wake], return_when=asyncio.FIRST_COMPLETED)
            finally:
                wake.cancel()
                await asyncio.gather(wake, return_exceptions=True)
            for task in done:
                if task in self.running:
                    self.running.pop(task)
                    task.result()

    def public_result(self):
        posts = [item for item in self.record["messages"] if item["author"] != "user"]
        text = "\n\n---\n\n".join(f"**{item['author']}**\n\n{item['content']}" for item in posts)
        label = {"idle": "频道暂时空闲，不代表任务已完成", "limit_reached": "已达到运行预算", "cancelled": "频道已停止", "timed_out": "频道运行超时", "error": "频道运行失败"}.get(self.record["status"], self.record["status"])
        return (text + f"\n\n[公共频道：{label}]").strip()

    async def execute(self):
        try:
            self._append_message("user", self.context.user_message)
            await self.runtime.sessions.save(self.session)
            await self.emit("started", members=list(self.settings.members))
            await asyncio.wait_for(self.schedule(), self.settings.timeout_sec)
            self.record["status"] = "limit_reached" if self.limited else "idle"
        except asyncio.TimeoutError:
            self.record["status"] = "timed_out"
            raise
        except asyncio.CancelledError:
            self.record["status"] = "cancelled"
            raise
        except Exception as exc:
            self.record.update(status="error", error=str(exc))
            raise
        finally:
            self.closed = True
            for task in self.running:
                task.cancel()
            await asyncio.gather(*self.running, return_exceptions=True)
            self.running.clear()
            self.record["discarded_messages"] = sum(len(actor.inbox) for actor in self.actors)
            for actor in self.actors:
                actor.inbox.clear()
                actor.token = None
            self.record["ended_at"] = time.time()
            await self.emit(self.record["status"])
            self.context.metadata["_room_result"] = self.public_result()
            self.session.add_message(LLMMessage(role="assistant", content=self.public_result()))
            await self.runtime.sessions.save(self.session)
        return self.public_result()


class RoomRuntime:
    def __init__(self, default_agent, catalog):
        self.default_agent, self.catalog = default_agent, catalog
        self.sessions = default_agent.sessions
        self._active_chats: dict[str, RoomRun] = {}

    def is_running(self, chat_id):
        return chat_id in self._active_chats

    def validate(self, value):
        self.catalog.refresh()
        return RoomConfig.parse(value, self.catalog)

    async def process_message(self, context, **callbacks):
        if self.is_running(context.chat_id):
            raise RuntimeError("当前会话已有正在运行的任务")
        # Reserve before the first await, including session restoration.
        self._active_chats[context.chat_id] = None
        try:
            session = await self.sessions.resolve_session(context.chat_id)
            if session is None:
                session = self.sessions.get_or_create(context.chat_id, context.channel, context.account_id)
            settings = self.validate(context.metadata.get("agent_room", session.metadata.get("agent_room")))
            if context.metadata.get("plan_mode"):
                raise ValueError("公共频道暂不支持计划模式")
            if context.metadata.get("project_id"):
                session.metadata["project_id"] = context.metadata["project_id"]
            session.metadata.setdefault("root_session_id", session.session_id)
            session.add_message(LLMMessage(role="user", content=context.user_message))
            run = RoomRun(self, settings, context, session, callbacks)
            self._active_chats[context.chat_id] = run
            session.metadata["last_room_run"] = run.record
            return await run.execute()
        finally:
            self._active_chats.pop(context.chat_id, None)
