"""
subagent.py — Sub-agent 系统
"""
import asyncio
import uuid
from typing import Dict, Optional
from dataclasses import dataclass, field

from .agent import Agent, AgentContext
from .memory.session import SessionManager
from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class SubAgentTask:
    """一个子任务"""
    task_id: str
    task_prompt: str
    status: str = "pending"     # pending | running | done | error
    result: Optional[str] = None
    model: str = "default"
    thinking: str = "off"


class SubAgentManager:
    """管理多个子 Agent 的并发执行"""
    
    def __init__(self, agent_template: Agent, session_manager: SessionManager):
        self.agent = agent_template
        self.sessions = session_manager
        self._tasks: Dict[str, SubAgentTask] = {}
        self._running: Dict[str, asyncio.Task] = {}
    
    async def spawn(self, task_prompt: str, parent_session_id: str = None,
                    model: str = "default", thinking: str = "off") -> str:
        """
        创建一个子 Agent 任务，立即开始异步执行
        
        返回 task_id
        """
        task_id = f"subagent:{uuid.uuid4().hex[:12]}"
        task = SubAgentTask(task_id=task_id, task_prompt=task_prompt, 
                           model=model, thinking=thinking)
        self._tasks[task_id] = task
        
        # 启动异步执行
        coro = self._run_subagent(task, parent_session_id)
        self._running[task_id] = asyncio.create_task(coro)
        
        logger.info(f"Sub-agent spawned: {task_id}")
        return task_id
    
    async def _run_subagent(self, task: SubAgentTask, parent_session_id: str):
        """在独立上下文中执行子 Agent"""
        task.status = "running"
        try:
            # 子 Agent 使用独立的会话
            ctx = AgentContext(
                chat_id=f"subagent:{task.task_id}",
                channel="subagent",
                account_id="system",
                user_message=task.task_prompt,
                metadata={"parent_session": parent_session_id},
            )
            task.result = await self.agent.process_message(ctx)
            task.status = "done"
            logger.info(f"Sub-agent done: {task.task_id}")
        except Exception as e:
            task.result = f"Error: {e}"
            task.status = "error"
            logger.error(f"Sub-agent error: {task.task_id}: {e}")
    
    def get(self, task_id: str) -> Optional[SubAgentTask]:
        return self._tasks.get(task_id)
    
    def list_running(self) -> list:
        return [t for t in self._tasks.values() if t.status in ("pending", "running")]
    
    async def wait(self, task_id: str, timeout: float = 300) -> Optional[str]:
        """等待一个子任务完成"""
        if task_id in self._running:
            try:
                await asyncio.wait_for(self._running[task_id], timeout=timeout)
            except asyncio.TimeoutError:
                return "Error: Sub-agent timed out"
        task = self._tasks.get(task_id)
        return task.result if task else None
    
    async def kill(self, task_id: str):
        """取消一个子任务"""
        if task_id in self._running:
            self._running[task_id].cancel()
            self._tasks[task_id].status = "error"
            self._tasks[task_id].result = "Task was cancelled"