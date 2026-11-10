"""
subagent.py — Sub-agent 系统（带沙箱隔离）

每个子 Agent 可运行在独立工作区中，写操作不污染主工作区。
支持可选的独立 LLM 模型配置（更便宜的模型处理子任务）。
"""
import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from dataclasses import dataclass, field

from .agent import Agent, AgentContext
from .agents.catalog import AgentCatalog
from .settings import WORKSPACE_DIR, MEMORY_FILE, active_workspace_dir
from .memory.session import SessionManager
from .logger import get_logger
from .runtime.steering import SteeringInbox

logger = get_logger(__name__)
TaskEventCallback = Callable[[Dict], Awaitable[None]]

# 子 Agent 沙箱默认复制到隔离工作区的文件
_SANDBOX_SEED_FILES = [
    MEMORY_FILE,
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
]


@dataclass
class SubAgentTask:
    """一个子任务"""
    task_id: str
    task_prompt: str
    agent_name: str = "general-worker"
    status: str = "pending"          # pending | running | done | error
    result: Optional[str] = None
    model: str = "default"
    thinking: str = "off"
    parent_session_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    sandbox_dir: Optional[str] = None  # 隔离工作区路径
    workspace_dir: Optional[str] = None
    collected_files: List[str] = field(default_factory=list)
    _steering: Optional[SteeringInbox] = field(default=None, repr=False, compare=False)
    _followups: List[Tuple[str, str]] = field(default_factory=list, repr=False, compare=False)
    _message_deliveries: Dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "agent": self.agent_name,
            "objective": self.task_prompt,
            "status": self.status,
            "result": self.result,
            "idempotency_key": self.idempotency_key,
            "sandbox_dir": self.sandbox_dir,
            "workspace_dir": self.workspace_dir,
            "collected_files": list(self.collected_files),
        }


class SubAgentManager:
    """管理多个子 Agent 的并发执行，支持沙箱隔离。"""

    def __init__(
        self,
        agent_template: Agent,
        session_manager: SessionManager,
        catalog: Optional[AgentCatalog] = None,
    ):
        self.agent = agent_template
        self.sessions = session_manager
        self.catalog = catalog or AgentCatalog()
        self._tasks: Dict[str, SubAgentTask] = {}
        self._running: Dict[str, asyncio.Task] = {}
        self._event_callbacks: Dict[str, TaskEventCallback] = {}
        self._delegation_keys: Dict[Tuple[Any, ...], str] = {}

    async def spawn(
        self,
        task_prompt: str,
        parent_session_id: str = None,
        model: str = "default",
        thinking: str = "off",
        *,
        agent_name: str = "general-worker",
        sandbox: bool = True,
        seed_files: Optional[List[str]] = None,
        workspace_dir: Optional[str] = None,
        event_callback: Optional[TaskEventCallback] = None,
        idempotency_key: Optional[str] = None,
        force_new: bool = False,
    ) -> str:
        """Create a delegated task and return its opaque task ID."""
        receipt = await self.delegate(
            task_prompt,
            parent_session_id=parent_session_id,
            model=model,
            thinking=thinking,
            agent_name=agent_name,
            sandbox=sandbox,
            seed_files=seed_files,
            workspace_dir=workspace_dir,
            event_callback=event_callback,
            idempotency_key=idempotency_key,
            force_new=force_new,
        )
        return str(receipt["task_id"])

    async def delegate(
        self,
        task_prompt: str,
        parent_session_id: str = None,
        model: str = "default",
        thinking: str = "off",
        *,
        agent_name: str = "general-worker",
        sandbox: bool = True,
        seed_files: Optional[List[str]] = None,
        workspace_dir: Optional[str] = None,
        event_callback: Optional[TaskEventCallback] = None,
        idempotency_key: Optional[str] = None,
        force_new: bool = False,
    ) -> Dict[str, Any]:
        """Create or reuse a delegated task and return a structured receipt."""
        self.catalog.refresh()
        profile = self.catalog.require(agent_name)
        delegation_key = self._delegation_key(
            task_prompt=task_prompt,
            parent_session_id=parent_session_id,
            agent_name=profile.name,
            model=model,
            thinking=thinking,
            sandbox=sandbox,
            seed_files=seed_files,
            workspace_dir=workspace_dir,
            idempotency_key=idempotency_key,
        )
        existing_id = self._delegation_keys.get(delegation_key)
        existing = self._tasks.get(existing_id or "")
        if not force_new and existing is not None:
            if event_callback is not None and existing_id not in self._event_callbacks:
                self._event_callbacks[existing_id] = event_callback
            logger.info("Reusing delegated task: %s", existing.task_id)
            return self._receipt(existing, reused=True)

        task_id = f"subagent:{uuid.uuid4().hex[:12]}"
        task = SubAgentTask(
            task_id=task_id,
            task_prompt=task_prompt,
            agent_name=profile.name,
            model=model,
            thinking=thinking,
            parent_session_id=parent_session_id,
            workspace_dir=str(Path(workspace_dir or active_workspace_dir()).resolve()),
            idempotency_key=(
                idempotency_key.strip()
                if idempotency_key and idempotency_key.strip()
                else None
            ),
        )
        self._tasks[task_id] = task
        self._delegation_keys[delegation_key] = task_id
        if event_callback is not None:
            self._event_callbacks[task_id] = event_callback

        task._steering = SteeringInbox()
        self._running[task_id] = asyncio.create_task(
            self._run_subagent(task, parent_session_id, sandbox, seed_files, task.workspace_dir)
        )
        await self._emit_event(task, event_callback)

        logger.info("Sub-agent spawned: %s (sandbox=%s, model=%s)", task_id, sandbox, model)
        return self._receipt(task, reused=False)

    async def continue_task(
        self, task_id: str, instruction: str, *, parent_session_id: str,
        event_callback: Optional[TaskEventCallback] = None,
    ) -> Dict[str, Any]:
        """Append a turn to a finished task using its original session and workspace."""
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("instruction must be a non-empty string")
        task = self._require_owned_task(task_id, parent_session_id)
        running = self._running.get(task_id)
        if task.status not in {"done", "error"} or (running is not None and not running.done()):
            raise ValueError("Task is still active; send a message with delegate_task and the same task_id")
        self._start_continuation(task, instruction)
        callback = event_callback or self._event_callbacks.get(task_id)
        if callback is not None:
            self._event_callbacks[task_id] = callback
        await self._emit_event(task, callback)
        return {**self._receipt(task, reused=True), "continued": True}

    async def send_task_message(
        self, task_id: str, message: str, *, parent_session_id: str,
        event_callback: Optional[TaskEventCallback] = None,
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        """Deliver to the active loop, or continue the original task when it is idle."""
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        task = self._require_owned_task(task_id, parent_session_id)
        key = idempotency_key.strip()
        if key and key in task._message_deliveries:
            return {
                **self._receipt(task, reused=True),
                "delivery": task._message_deliveries[key],
                "deduplicated": True,
            }

        running = self._running.get(task_id)
        active = running is not None and not running.done()
        delivery = "next_turn"
        if active:
            if running.cancelling():
                raise ValueError("Task is being cancelled")
            message_id = uuid.uuid4().hex
            if task._steering is not None and task._steering.offer(message_id, message):
                delivery = "current_turn"
            else:
                # The loop sealed its input while the task was still finalizing.
                task._followups.append((message_id, message))
                if task.status in {"done", "error"}:
                    task.status = "pending"
                    task.result = None
        else:
            self._start_continuation(task, message)

        if event_callback is not None:
            self._event_callbacks[task_id] = event_callback
        if key:
            task._message_deliveries[key] = delivery
        if not active:
            await self._emit_event(task, self._event_callbacks.get(task_id))
        return {**self._receipt(task, reused=True), "delivery": delivery}

    def _require_owned_task(self, task_id: str, parent_session_id: str) -> SubAgentTask:
        task = self._tasks.get(task_id) if isinstance(task_id, str) else None
        if task is None:
            raise ValueError("Unknown task; use list_tasks to check the original task_id")
        if task.parent_session_id != parent_session_id:
            raise ValueError("Task does not belong to the current parent conversation")
        return task

    def _start_continuation(self, task: SubAgentTask, instruction: str) -> None:
        workspace = task.sandbox_dir or task.workspace_dir
        if not workspace or not Path(workspace).is_dir():
            raise ValueError("Original workspace no longer exists; this task cannot be continued")
        self.catalog.refresh()
        self.catalog.require(task.agent_name)
        task.status = "pending"
        task.result = None
        task._steering = SteeringInbox()
        self._running[task.task_id] = asyncio.create_task(self._run_subagent(
            task, task.parent_session_id, bool(task.sandbox_dir), None,
            task.workspace_dir, instruction=instruction,
        ))

    @staticmethod
    def _delegation_key(
        *,
        task_prompt: str,
        parent_session_id: Optional[str],
        agent_name: str,
        model: str,
        thinking: str,
        sandbox: bool,
        seed_files: Optional[List[str]],
        workspace_dir: Optional[str],
        idempotency_key: Optional[str],
    ) -> Tuple[Any, ...]:
        parent = parent_session_id or ""
        workspace = str(Path(workspace_dir).resolve()) if workspace_dir else ""
        if idempotency_key and idempotency_key.strip():
            return ("explicit", parent, workspace, idempotency_key.strip())
        return (
            "automatic",
            parent,
            workspace,
            agent_name,
            " ".join(task_prompt.split()),
            model,
            thinking,
            bool(sandbox),
            tuple(seed_files or ()),
        )

    @staticmethod
    def _receipt(task: SubAgentTask, *, reused: bool) -> Dict[str, Any]:
        return {
            "task_id": task.task_id,
            "status": task.status,
            "reused": reused,
            "instruction": (
                "Treat task_id as opaque. Copy it verbatim, including the "
                "'subagent:' prefix, for delegate_task messages, inspect_task, wait_task, "
                "continue_task, or cancel_task."
            ),
        }

    async def _prepare_sandbox(self, task: SubAgentTask, seed_files: Optional[List[str]]) -> Path:
        """为子 Agent 创建隔离工作区。"""
        sandbox_root = Path(tempfile.mkdtemp(prefix="miniclaw_sandbox_"))
        (sandbox_root / "sessions").mkdir(parents=True, exist_ok=True)

        # 复制种子文件
        files_to_copy = _SANDBOX_SEED_FILES + (seed_files or [])
        for filename in files_to_copy:
            src = WORKSPACE_DIR / filename
            if src.exists():
                dst = sandbox_root / filename
                shutil.copy2(src, dst)

        task.sandbox_dir = str(sandbox_root)
        logger.debug("Sandbox prepared at %s", sandbox_root)
        return sandbox_root

    async def _run_subagent(
        self,
        task: SubAgentTask,
        parent_session_id: Optional[str],
        sandbox: bool,
        seed_files: Optional[List[str]],
        workspace_dir: Optional[str],
        instruction: Optional[str] = None,
    ) -> Optional[str]:
        """Keep one worker responsible for the task's current and queued turns."""
        try:
            while True:
                await self._run_turn(
                    task, parent_session_id, sandbox, seed_files, workspace_dir, instruction,
                )
                if task._steering is not None:
                    task._steering.close()
                if not task._followups:
                    await self._emit_event(task, self._event_callbacks.get(task.task_id))
                if not task._followups:
                    return task.result

                followups, task._followups = task._followups, []
                instruction = followups[0][1]
                task._steering = SteeringInbox()
                for message_id, message in followups[1:]:
                    task._steering.offer(message_id, message)
                task.status = "pending"
                task.result = None
                await self._emit_event(task, self._event_callbacks.get(task.task_id))
        except asyncio.CancelledError:
            if task.status in {"pending", "running"}:
                task.status = "error"
                task.result = "Task was cancelled"
            raise
        finally:
            if task._steering is not None:
                task._steering.close()
                task._steering = None
            task._followups.clear()

    async def _run_turn(
        self,
        task: SubAgentTask,
        parent_session_id: Optional[str],
        sandbox: bool,
        seed_files: Optional[List[str]],
        workspace_dir: Optional[str],
        instruction: Optional[str],
    ) -> None:
        """在独立上下文中执行子 Agent。"""
        task.status = "running"
        await self._emit_event(task, self._event_callbacks.get(task.task_id))
        try:
            metadata: Dict = {
                "parent_session": parent_session_id,
                "is_subagent": True,
                "task_id": task.task_id,
            }
            if instruction is not None:
                original_workspace = task.sandbox_dir or task.workspace_dir
                if not original_workspace or not Path(original_workspace).is_dir():
                    raise ValueError("Original workspace no longer exists; this task cannot be continued")
                previous = await self.sessions.resolve_session(f"subagent:{task.task_id}")
                if previous is None:
                    raise ValueError("Original task session is unavailable; refusing to continue without its history")
                for key in ("project_id", "parent_session_id", "root_session_id"):
                    if previous.metadata.get(key):
                        metadata[key] = previous.metadata[key]
            elif parent_session_id:
                # Delegation passes the parent's chat ID; persist its actual session ID.
                parent = self.sessions.get(parent_session_id) or await self.sessions.resolve_session(parent_session_id)
                metadata["parent_session_id"] = parent.session_id if parent else parent_session_id
                metadata["root_session_id"] = (
                    parent.metadata.get("root_session_id") or parent.session_id
                    if parent else parent_session_id
                )
                if parent and parent.metadata.get("project_id"):
                    metadata["project_id"] = parent.metadata["project_id"]

            if sandbox:
                sandbox_dir = Path(task.sandbox_dir) if task.sandbox_dir else await self._prepare_sandbox(task, seed_files)
                if not sandbox_dir.is_dir():
                    raise ValueError("Original sandbox no longer exists")
                metadata["sandbox_dir"] = str(sandbox_dir)
                metadata["original_workspace"] = str(WORKSPACE_DIR)
                metadata["workspace_dir"] = str(sandbox_dir)
            elif workspace_dir:
                metadata["workspace_dir"] = str(Path(workspace_dir).resolve())

            ctx = AgentContext(
                chat_id=f"subagent:{task.task_id}",
                channel="subagent",
                account_id="system",
                user_message=instruction if instruction is not None else task.task_prompt,
                metadata=metadata,
                steering=task._steering,
            )

            # 子 Agent 可使用不同的 LLM 配置（通过 metadata 传递）
            if task.model and task.model != "default":
                ctx.metadata["model_override"] = task.model
                ctx.metadata["thinking"] = task.thinking

            profile = self.catalog.require(task.agent_name)
            child_agent = Agent(self.agent.llm, self.sessions, profile=profile)
            task.result = await child_agent.process_message(ctx)
            task.status = "done"

            # 收集沙箱生成的文件
            if sandbox and task.sandbox_dir:
                self._collect_sandbox_files(task)

            logger.info("Sub-agent done: %s", task.task_id)
        except Exception as e:
            task.result = f"Error: {e}"
            task.status = "error"
            logger.error("Sub-agent error: %s: %s", task.task_id, e)

    def _collect_sandbox_files(self, task: SubAgentTask) -> None:
        """收集沙箱中生成的新文件路径。"""
        sandbox = Path(task.sandbox_dir)
        if not sandbox or not sandbox.is_dir():
            return
        task.collected_files.clear()
        for p in sandbox.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(sandbox))
                # 跳过种子文件
                if rel not in _SANDBOX_SEED_FILES:
                    task.collected_files.append(rel)

    def get(self, task_id: str) -> Optional[SubAgentTask]:
        return self._tasks.get(task_id)

    @staticmethod
    async def _emit_event(
        task: SubAgentTask,
        callback: Optional[TaskEventCallback],
    ) -> None:
        if callback is None:
            return
        try:
            await callback(task.to_dict())
        except Exception:
            logger.exception("Sub-agent event callback failed for %s", task.task_id)

    def list_running(self) -> list:
        return [t for t in self._tasks.values() if t.status in ("pending", "running")]

    def list_all(self) -> list:
        return list(self._tasks.values())

    def list_for_parent(self, parent_session_id: str) -> List[SubAgentTask]:
        """Return only tasks owned by one parent conversation."""
        return [
            task
            for task in self._tasks.values()
            if task.parent_session_id == parent_session_id
        ]

    async def wait(self, task_id: str, timeout: float = 300) -> Optional[str]:
        """等待一个子任务完成。"""
        running = self._running.get(task_id)
        if running is not None:
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(running),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                task = self._tasks.get(task_id)
                return (
                    f"Task {task_id} is still {task.status}"
                    if task is not None
                    else None
                )
            if self._running.get(task_id) is not running:
                return result
        task = self._tasks.get(task_id)
        return task.result if task else None

    async def kill(self, task_id: str) -> None:
        """取消一个子任务并清理沙箱。"""
        if task_id in self._running:
            self._running[task_id].cancel()
        task = self._tasks.get(task_id)
        if task:
            if task._steering is not None:
                task._steering.close()
            task._followups.clear()
            task.status = "error"
            task.result = "Task was cancelled"
            await self._cleanup_sandbox(task)
            await self._emit_event(task, self._event_callbacks.get(task_id))

    async def _cleanup_sandbox(self, task: SubAgentTask) -> None:
        """清理子 Agent 的沙箱目录。"""
        if task.sandbox_dir:
            sandbox = Path(task.sandbox_dir)
            if sandbox.is_dir():
                try:
                    shutil.rmtree(sandbox, ignore_errors=True)
                    logger.debug("Sandbox cleaned up: %s", sandbox)
                except Exception:
                    pass
