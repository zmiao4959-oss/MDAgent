"""User-defined communication topologies over the existing Agent runtime.

Edges grant permission to send messages. Each node has its own inbox, and
independent nodes can run concurrently. Graph tools are bound to the active node.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import math
import time
import uuid
from typing import Any, Mapping

from ..agent import Agent, AgentContext
from ..llm.base import LLMMessage
from ..runtime.steering import SteeringInbox
from ..skills.project import BUILDER, project_store
from .catalog import AgentCatalog

MESSAGE_TOOL = "message_agent"
MAX_MESSAGE_CHARS = 32_000
DEFAULT_MAX_CONCURRENCY = 4


@dataclass(frozen=True)
class GraphInput:
    source: str
    message: str
    message_id: str

    def prompt(self) -> str:
        return f"[Graph message from {self.source}; id={self.message_id}]\n{self.message}"


@dataclass(frozen=True)
class AgentGraph:
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    entry: str
    max_messages: int = 24
    positions: tuple[tuple[float, float], ...] = ()
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY

    @classmethod
    def parse(cls, raw: Any, catalog: AgentCatalog) -> "AgentGraph":
        if not isinstance(raw, Mapping) or raw.get("version", 1) != 1:
            raise ValueError("画布必须是 version=1 的图配置")
        items, links = raw.get("nodes"), raw.get("edges", [])
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise ValueError("请选择 1–32 个智能体")
        nodes, positions = [], []
        for index, item in enumerate(items):
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                raise ValueError("节点需要有效的智能体名称 id")
            name = item["id"]
            catalog.require(name)
            if name in nodes:
                raise ValueError(f"智能体不能重复添加：{name}")
            nodes.append(name)
            xy = (item.get("x", 60 + index % 3 * 260), item.get("y", 60 + index // 3 * 140))
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 100_000 for v in xy):
                raise ValueError("节点坐标必须是有限数值")
            positions.append(tuple(float(v) for v in xy))
        if not isinstance(links, list) or len(links) > 992:
            raise ValueError("连线配置无效")
        edges = []
        for link in links:
            if not isinstance(link, Mapping):
                raise ValueError("连线需要 source 和 target")
            source, target = link.get("source"), link.get("target")
            if source not in nodes or target not in nodes or source == target:
                raise ValueError("连线必须连接画布中两个不同的智能体")
            if (source, target) in edges:
                raise ValueError(f"重复连线：{source} → {target}")
            edges.append((source, target))
        entry = raw.get("entry")
        if entry not in nodes:
            raise ValueError("请选择画布中的一个智能体作为起点")
        reached = {entry}
        while True:
            expanded = reached | {b for a, b in edges if a in reached}
            if expanded == reached:
                break
            reached = expanded
        if reached != set(nodes):
            raise ValueError("以下智能体无法从起点到达，请补充连线或移除：" + ", ".join(sorted(set(nodes) - reached)))
        limit = raw.get("max_messages", 24)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
            raise ValueError("单轮消息上限必须是 1–2000 的整数")
        concurrency = raw.get("max_concurrency", DEFAULT_MAX_CONCURRENCY)
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 16:
            raise ValueError("节点并发上限必须是 1–16 的整数")
        return cls(tuple(nodes), tuple(edges), entry, limit, tuple(positions), concurrency)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "nodes": [{"id": name, "x": xy[0], "y": xy[1]} for name, xy in zip(self.nodes, self.positions)],
            "edges": [{"source": a, "target": b} for a, b in self.edges],
            "entry": self.entry,
            "max_messages": self.max_messages,
            "max_concurrency": self.max_concurrency,
        }

    @property
    def fingerprint(self) -> str:
        # Layout changes do not discard conversations; topology changes do.
        value = [sorted(self.nodes), sorted(self.edges), self.entry]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()[:16]

    def targets(self, source: str) -> list[str]:
        return sorted(b for a, b in self.edges if a == source)


class GraphNodeBinding:
    """Request-scoped communication capability bound to the actual sender."""

    def __init__(self, run: "GraphRun", source: str):
        self.run, self.source = run, source

    def blocks(self, name: str, registry: Any) -> bool:
        tool = registry.get(name)
        return bool(tool and {"coordination", "communication"}.intersection(tool.tags))

    def tool_definitions(self) -> list[dict]:
        targets = self.run.graph.targets(self.source)
        definitions = [{
            "type": "function", "function": {
                "name": MESSAGE_TOOL,
                "description": (
                    "Send a question, update, or reply to a connected Agent. Available nodes can "
                    "run concurrently. The canvas delivers updates into the current turn when it can "
                    "accept them and queues the rest for a later turn. "
                    "The receipt reports how the message was routed. A reply travels along a permitted return edge."
                ),
                "parameters": {
                    "type": "object", "additionalProperties": False,
                    "required": ["target", "message"],
                    "properties": {
                        "target": {"type": "string", "enum": targets},
                        "message": {"type": "string", "minLength": 1, "maxLength": MAX_MESSAGE_CHARS},
                    },
                },
            },
        }] if targets else []
        if self.run.skill_store is not None:
            if self.source == BUILDER:
                definitions.extend([
                    self._schema("list_skill_requests", "Inspect project Skill requests. Supply request_id to read its objective, acceptance criteria, requester permissions and draft_dir.", {"request_id": {"type": "string"}}, []),
                    self._schema("publish_skill", "Validate the request's Skill package, run its Python unittest cases, and mount the frozen version only to the requester. Queues a resume message after success. Does not change tools or agent.yaml.", {"request_id": {"type": "string"}}, ["request_id"]),
                ])
            elif self._can_request():
                definitions.append(self._schema(
                    "request_skill", "Request a missing Skill within your current role and existing tools. The builder receives an isolated draft directory. End your turn after requesting; successful publication queues a resume message.",
                    {"objective": {"type": "string"}, "acceptance": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20}}, ["objective", "acceptance"],
                ))
            if self.run.skill_store.list_bindings().get(self.source):
                definitions.append(self._schema("revoke_skill", "Remove one temporary project Skill from your own role after a failure or when no longer needed. Does not delete its version or alter profile tools.", {"name": {"type": "string"}}, ["name"]))
        return definitions

    @staticmethod
    def _schema(name, description, properties, required):
        return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "additionalProperties": False, "properties": properties, "required": required}}}

    def _can_request(self) -> bool:
        return (
            self.source != BUILDER and BUILDER in self.run.profiles
            and BUILDER in self.run.graph.targets(self.source)
            and self.source in self.run.graph.targets(BUILDER)
            and self.run.profiles[self.source].allows_tool("read_skill")
        )

    def prompt(self) -> str:
        targets = self.run.graph.targets(self.source)
        reply_targets = [target for target in targets if self.source in self.run.graph.targets(target)]
        prompt = (
            "\n\n## Agent graph\n"
            f"Your node: {self.source}. Permitted recipients: {', '.join(targets) or '(none)'}.\n"
            f"Recipients with a direct reply edge to you: {', '.join(reply_targets) or '(none)'}.\n"
            f"Message budget: {self.run.graph.max_messages}.\n"
            "画布协作通过 message_agent 发送：任务协商 → 能力反馈 → 确认执行 → 执行结果。"
            "agent 间的新任务确认后执行；补交、状态更新及 Skill 请求沿用已有约定，结果无需回执。"
            "独立任务可以同时发给不同节点。依赖上游产物的任务等收到交付后再发送。"
        )
        if targets and not reply_targets:
            prompt += (
                "\n当前接收者均无回信边，跨节点协商需要补充反向连线。"
            )
        if self._can_request() and self.run.skill_store is not None:
            prompt += (
                "\n职责内缺少可复用能力时，可通过 request_skill 向 skill-builder 请求项目 Skill。"
            )
        return prompt

    @staticmethod
    def handles(name: str) -> bool:
        return name in {MESSAGE_TOOL, "request_skill", "list_skill_requests", "publish_skill", "revoke_skill"}

    async def execute_tool(self, name: str, arguments: Any) -> str:
        if name == MESSAGE_TOOL:
            return await self.execute(arguments)
        if not isinstance(arguments, dict):
            return "Error: arguments must be an object"
        store = self.run.skill_store
        if store is None:
            return "Error: project workspace is required for temporary Skills"
        try:
            if name == "request_skill":
                if not self._can_request():
                    raise ValueError("需要与 skill-builder 双向连线，且请求者已有 read_skill 权限")
                if set(arguments) != {"objective", "acceptance"}:
                    raise ValueError("request_skill 需要 objective 和 acceptance")
                if self.run.accepted + self.run.reserved_messages + 2 > self.run.graph.max_messages:
                    raise ValueError("消息预算不足，请为请求与恢复至少保留两条消息")
                request = store.create_request(self.run.profiles[self.source], **arguments)
                request_id = request["request_id"]
                if request_id not in self.run.sent_skill_requests:
                    self.run.sent_skill_requests.add(request_id)
                    await self.run.enqueue(self.source, BUILDER, f"[Skill request {request_id}]\nUse list_skill_requests(request_id='{request_id}') for the objective, acceptance criteria, requester restrictions and draft_dir. Develop and publish the Skill if those conditions can be met.")
                    await self.run.emit("skill_requested", node=self.source, request_id=request_id, target=BUILDER)
                return json.dumps({k: request[k] for k in ("request_id", "requester", "status", "draft_dir", "reused")}, ensure_ascii=False)
            if name == "list_skill_requests":
                if self.source != BUILDER or set(arguments) - {"request_id"}:
                    raise ValueError("只有 skill-builder 可以查看 Skill 开发请求")
                if arguments.get("request_id"):
                    result = store.require_request(arguments["request_id"])
                else:
                    result = [{k: r[k] for k in ("request_id", "requester", "status", "objective")} for r in store.requests()[-20:]]
                return json.dumps(result, ensure_ascii=False)
            if name == "publish_skill":
                if self.source != BUILDER or set(arguments) != {"request_id"}:
                    raise ValueError("只有 skill-builder 可以发布指定请求的 Skill")
                request = store.require_request(arguments["request_id"])
                requester = request["requester"]
                if requester not in self.run.profiles or requester not in self.run.graph.targets(BUILDER) or BUILDER not in self.run.graph.targets(requester):
                    raise ValueError("原请求者必须在当前画布中并与 skill-builder 双向连接")
                if self.run.accepted + self.run.reserved_messages >= self.run.graph.max_messages:
                    raise ValueError("没有恢复请求者的消息额度；本次不启用 Skill")
                self.run.reserved_messages += 1
                reserved = True
                try:
                    receipt = await store.publish(arguments["request_id"], self.run.profiles[requester])
                    if not receipt["reused"]:
                        reserved = False
                        await self.run.enqueue(BUILDER, requester, f"[Skill ready: {receipt['name']}; request={request['request_id']}]\nA validated project Skill is now mounted only to you. Load it with read_skill(name='{receipt['name']}'), then continue the original objective: {request['objective']}\nAcceptance criteria: {json.dumps(request['acceptance'], ensure_ascii=False)}\nTool permissions and explicit role prohibitions are unchanged.", reserved=True, next_turn=True)
                        await self.run.emit("skill_mounted", node=requester, name=receipt["name"], version=receipt["version"], request_id=request["request_id"])
                    return json.dumps(receipt, ensure_ascii=False)
                finally:
                    if reserved:
                        self.run.reserved_messages -= 1
            if name == "revoke_skill":
                if set(arguments) != {"name"}:
                    raise ValueError("revoke_skill 需要 name")
                receipt = store.revoke(self.source, arguments["name"])
                await self.run.emit("skill_revoked", node=self.source, name=arguments["name"])
                return json.dumps(receipt, ensure_ascii=False)
            return "Error: unknown graph capability"
        except (ValueError, TypeError, KeyError, OSError, SyntaxError, asyncio.TimeoutError) as exc:
            await self.run.emit("skill_failed", node=self.source, reason=str(exc)[:1000] or type(exc).__name__)
            return f"Error: {exc or type(exc).__name__}"

    async def execute(self, arguments: Any) -> str:
        if not isinstance(arguments, dict) or set(arguments) != {"target", "message"}:
            return "Error: message_agent requires only target and message"
        return await self.run.enqueue(self.source, arguments["target"], arguments["message"])


class GraphRun:
    def __init__(self, runtime: "AgentGraphRuntime", graph: AgentGraph, context: AgentContext, session: Any):
        self.runtime, self.graph, self.context, self.session = runtime, graph, context, session
        self.run_id = uuid.uuid4().hex
        self.profiles = {name: runtime.catalog.require(name) for name in graph.nodes}
        self.skill_store = project_store(context.metadata)
        self.sent_skill_requests: set[str] = set()
        self.reserved_messages = 0
        self.inboxes: dict[str, list[GraphInput]] = {
            graph.entry: [GraphInput("user", context.user_message, "entry")],
        }
        self.active: dict[str, tuple[asyncio.Task, SteeringInbox]] = {}
        self.wakeup = asyncio.Event()
        self.activation_order: dict[str, int] = {}
        self.failed_node: str | None = None
        self.accepted = 0
        self.limited = False
        self.closed = False
        self.event_sequence = 0
        self.record = {
            "run_id": self.run_id, "graph": graph.to_dict(), "status": "running",
            "started_at": time.time(), "events": [], "outputs": [],
        }

    async def emit(self, kind: str, **data: Any) -> None:
        self.event_sequence += 1
        event = {"run_id": self.run_id, "sequence": self.event_sequence, "kind": kind, "time": time.time(), **data}
        self.record["events"].append(event)
        self.record["events"] = self.record["events"][-400:]
        await self.runtime.sessions.save(self.session)
        callback = self.context.metadata.get("_on_graph_event")
        if callback:
            await callback(event)

    async def enqueue(
        self, source: str, target: Any, message: Any, *,
        reserved: bool = False, next_turn: bool = False,
    ) -> str:
        if reserved:
            self.reserved_messages -= 1
        if self.closed:
            return "Error: graph run has ended"
        if not isinstance(target, str) or target not in self.graph.targets(source):
            await self.emit("blocked", source=source, target=str(target)[:100], reason="没有此方向的通信连线")
            return f"Error: graph does not permit {source} → {target}"
        if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_CHARS:
            return f"Error: message must contain 1–{MAX_MESSAGE_CHARS} characters"
        if self.accepted + self.reserved_messages >= self.graph.max_messages:
            self.limited = True
            await self.emit("limit", source=source, target=target, reason="已达到单轮消息上限")
            return "Error: graph message budget exhausted; finish with the available results"
        self.accepted += 1
        message_id = f"{self.run_id}:{self.accepted}"
        item = GraphInput(source, message, message_id)
        active = self.active.get(target)
        delivery = "queued"
        if active is not None and not next_turn and target not in self.inboxes:
            task, steering = active
            if not task.done() and steering.offer(message_id, item.prompt()):
                delivery = "current_turn"
        if delivery == "queued":
            self.inboxes.setdefault(target, []).append(item)
        await self.emit(
            "message", source=source, target=target, message_id=message_id,
            preview=message[:600], delivery=delivery,
        )
        self.wakeup.set()
        return json.dumps({
            "status": delivery, "message_id": message_id, "target": target,
            "remaining_messages": self.graph.max_messages - self.accepted,
        }, ensure_ascii=False)

    async def _run_node(
        self, node: str, batch: list[GraphInput], steering: SteeringInbox, *,
        on_stream_chunk, on_viz_event, on_progress,
    ) -> None:
        first = batch[0]
        message_id = first.message_id
        await self.emit(
            "node_started", node=node, source=first.source, message_id=message_id,
            message_ids=[item.message_id for item in batch],
        )
        node_chat = "agentgraph:" + hashlib.sha256(f"{self.context.chat_id}:{self.graph.fingerprint}:{node}".encode()).hexdigest()
        metadata = {
            key: self.context.metadata[key]
            for key in ("workspace_dir", "project_id", "project_title", "project_objective", "approved", "_on_task_event")
            if key in self.context.metadata
        }
        metadata.update(
            parent_session_id=self.session.session_id,
            root_session_id=self.session.metadata.get("root_session_id", self.session.session_id),
            task_id=f"graph-{self.graph.fingerprint}-{node}",
            _graph_binding=GraphNodeBinding(self, node),
        )
        context = AgentContext(
            chat_id=node_chat, channel="agent_graph", account_id=self.context.account_id,
            user_message=first.prompt(), metadata=metadata, steering=steering,
        )
        profile = self.profiles[node]
        agent = Agent(self.runtime.default_agent.llm, self.runtime.sessions, profile=profile)
        identity = {"run_id": self.run_id, "node": node, "message_id": message_id}
        on_graph_output = self.context.metadata.get("_on_graph_output")

        async def stream_delta(text):
            await on_graph_output({**identity, "delta": text})

        async def stream_progress(data):
            await on_progress({**data, **identity})

        call = agent.process_message(
            context, on_stream_chunk=stream_delta if on_graph_output else None,
            on_viz_event=on_viz_event, on_progress=stream_progress if on_progress else None,
        )
        try:
            if profile.runtime.timeout_sec is not None:
                response = await asyncio.wait_for(call, timeout=profile.runtime.timeout_sec)
            else:
                response = await call
        finally:
            steering.close()
            self.context.metadata.setdefault("viz_tasks", []).extend(context.metadata.get("viz_tasks") or [])

        outputs = self.record["outputs"]
        outputs.append({"node": node, "source": first.source, "message_id": message_id, "response": response})
        if on_graph_output:
            await on_graph_output({**identity, "full": response})
        elif on_stream_chunk:
            separator = "\n\n---\n\n" if len(outputs) > 1 else ""
            await on_stream_chunk(f"{separator}**{node}**\n\n{response}")
        await self.emit("node_done", node=node, message_id=message_id)

    async def _schedule(self, *, on_stream_chunk, on_viz_event, on_progress) -> None:
        while True:
            self.wakeup.clear()
            for node, (task, _) in tuple(self.active.items()):
                if not task.done():
                    continue
                del self.active[node]
                try:
                    task.result()
                except Exception:
                    self.failed_node = node
                    raise

            for node in tuple(self.inboxes):
                if len(self.active) >= self.graph.max_concurrency:
                    break
                if node in self.active:
                    continue
                batch = self.inboxes.pop(node)
                steering = SteeringInbox()
                for item in batch[1:]:
                    steering.offer(item.message_id, item.prompt())
                self.activation_order[batch[0].message_id] = len(self.activation_order)
                task = asyncio.create_task(self._run_node(
                    node, batch, steering, on_stream_chunk=on_stream_chunk,
                    on_viz_event=on_viz_event, on_progress=on_progress,
                ), name=f"graph:{self.run_id}:{node}")
                self.active[node] = (task, steering)
                task.add_done_callback(lambda _: self.wakeup.set())
            if not self.active:
                return
            await self.wakeup.wait()

    async def execute(self, *, on_stream_chunk=None, on_viz_event=None, on_progress=None) -> str:
        outputs = self.record["outputs"]

        async def publish_viz(event):
            if event.get("event") == "media" and event.get("path"):
                done = set(self.session.metadata.get("viz_done") or [])
                done.add(event["path"])
                self.session.metadata["viz_done"] = sorted(done)
                await self.runtime.sessions.save(self.session)
            if on_viz_event:
                await on_viz_event(event)

        try:
            await self.emit("started", entry=self.graph.entry)
            await self._schedule(
                on_stream_chunk=on_stream_chunk, on_viz_event=publish_viz if on_viz_event else None,
                on_progress=on_progress,
            )
            self.record["status"] = "limit_reached" if self.limited else "done"
        except asyncio.CancelledError:
            self.record["status"] = "cancelled"
            raise
        except Exception as exc:
            self.record["status"] = "error"
            self.record["error"] = str(exc)
            raise
        finally:
            self.closed = True
            tasks = [task for task, _ in self.active.values()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.active.clear()
            self.inboxes.clear()
            self.record["ended_at"] = time.time()
            outputs.sort(key=lambda item: self.activation_order[item["message_id"]])
            result = "\n\n---\n\n".join(f"**{item['node']}**\n\n{item['response']}" for item in outputs)
            if self.record["status"] != "done":
                result += f"\n\n[画布运行状态：{self.record['status']}]"
                if self.record.get("error"):
                    result += f"\n\n{self.record['error']}"
            self.context.metadata["_graph_result"] = result.strip()
            if result.strip():
                self.session.add_message(LLMMessage(role="assistant", content=result.strip()))
            if self.record["status"] == "error":
                await self.emit("error", node=self.failed_node, reason=self.record["error"])
            else:
                await self.emit(self.record["status"])
        return result


class AgentGraphRuntime:
    """Optional graph routing; absent configuration preserves default behavior."""

    def __init__(self, default_agent: Agent, catalog: AgentCatalog):
        self.default_agent, self.catalog = default_agent, catalog
        self.sessions = default_agent.sessions
        self._active_chats: set[str] = set()

    def is_running(self, chat_id: str) -> bool:
        return chat_id in self._active_chats

    def validate(self, value: Any) -> AgentGraph | None:
        self.catalog.refresh()
        return None if value is None else AgentGraph.parse(value, self.catalog)

    async def process_message(self, context: AgentContext, **callbacks: Any) -> str:
        if self.is_running(context.chat_id):
            raise RuntimeError("当前会话已有正在运行的任务")
        self._active_chats.add(context.chat_id)
        try:
            session = await self.sessions.resolve_session(context.chat_id)
            if session is None:
                session = self.sessions.get_or_create(context.chat_id, context.channel, context.account_id)
            raw = context.metadata.get("agent_graph", session.metadata.get("agent_graph"))
            graph = self.validate(raw)
            if graph is None:
                return await self.default_agent.process_message(context, **callbacks)
            if context.metadata.get("plan_mode"):
                raise ValueError("画布模式暂不支持计划模式，请先关闭计划模式")
            if context.metadata.get("project_id"):
                session.metadata["project_id"] = context.metadata["project_id"]
            session.metadata.setdefault("root_session_id", session.session_id)
            session.add_message(LLMMessage(role="user", content=context.user_message))
            run = GraphRun(self, graph, context, session)
            session.metadata["last_graph_run"] = run.record
            return await run.execute(**callbacks)
        finally:
            self._active_chats.discard(context.chat_id)
