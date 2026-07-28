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
from typing import Awaitable, Callable, Dict, List, Optional

from dataclasses import dataclass, field

from .agent import Agent, AgentContext
from .agents.catalog import AgentCatalog
from .settings import WORKSPACE_DIR, MEMORY_FILE
from .memory.session import SessionManager
from .logger import get_logger

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
    sandbox_dir: Optional[str] = None  # 隔离工作区路径
    collected_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "agent": self.agent_name,
            "objective": self.task_prompt,
            "status": self.status,
            "result": self.result,
            "sandbox_dir": self.sandbox_dir,
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
        event_callback: Optional[TaskEventCallback] = None,
    ) -> str:
        """
        创建一个子 Agent 任务，立即开始异步执行。

        Args:
            task_prompt: 子任务的提示词
            parent_session_id: 父会话 ID
            model: 使用的模型（default = 跟随主 Agent）
            thinking: thinking 模式
            agent_name: AgentCatalog 中的 Profile 名称
            sandbox: 是否使用沙箱隔离工作区
            seed_files: 额外复制到沙箱的文件列表

        Returns:
            task_id
        """
        self.catalog.refresh()
        profile = self.catalog.require(agent_name)
        task_id = f"subagent:{uuid.uuid4().hex[:12]}"
        task = SubAgentTask(
            task_id=task_id,
            task_prompt=task_prompt,
            agent_name=profile.name,
            model=model,
            thinking=thinking,
        )
        self._tasks[task_id] = task
        if event_callback is not None:
            self._event_callbacks[task_id] = event_callback

        await self._emit_event(task, event_callback)
        coro = self._run_subagent(
            task, parent_session_id, sandbox, seed_files, event_callback
        )
        self._running[task_id] = asyncio.create_task(coro)

        logger.info("Sub-agent spawned: %s (sandbox=%s, model=%s)", task_id, sandbox, model)
        return task_id

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
        event_callback: Optional[TaskEventCallback],
    ) -> None:
        """在独立上下文中执行子 Agent。"""
        task.status = "running"
        await self._emit_event(task, event_callback)
        try:
            metadata: Dict = {
                "parent_session": parent_session_id,
                "is_subagent": True,
            }

            if sandbox:
                sandbox_dir = await self._prepare_sandbox(task, seed_files)
                metadata["sandbox_dir"] = str(sandbox_dir)
                metadata["original_workspace"] = str(WORKSPACE_DIR)
                metadata["workspace_dir"] = str(sandbox_dir)

            ctx = AgentContext(
                chat_id=f"subagent:{task.task_id}",
                channel="subagent",
                account_id="system",
                user_message=task.task_prompt,
                metadata=metadata,
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
            await self._emit_event(task, event_callback)
        except Exception as e:
            task.result = f"Error: {e}"
            task.status = "error"
            logger.error("Sub-agent error: %s: %s", task.task_id, e)
            await self._emit_event(task, event_callback)
        finally:
            # 清理沙箱（保留结果文件的情况下可配置）
            pass

    def _collect_sandbox_files(self, task: SubAgentTask) -> None:
        """收集沙箱中生成的新文件路径。"""
        sandbox = Path(task.sandbox_dir)
        if not sandbox or not sandbox.is_dir():
            return
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

    async def wait(self, task_id: str, timeout: float = 300) -> Optional[str]:
        """等待一个子任务完成。"""
        if task_id in self._running:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._running[task_id]),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                task = self._tasks.get(task_id)
                return (
                    f"Task {task_id} is still {task.status}"
                    if task is not None
                    else None
                )
        task = self._tasks.get(task_id)
        return task.result if task else None

    async def kill(self, task_id: str) -> None:
        """取消一个子任务并清理沙箱。"""
        if task_id in self._running:
            self._running[task_id].cancel()
        task = self._tasks.get(task_id)
        if task:
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
