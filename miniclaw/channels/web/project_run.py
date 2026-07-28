"""Background execution service for planned WebChat project tasks."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from ...agent import AgentContext
from ...logger import get_logger
from ...projects import Project, ProjectManager
from .project_workspace import ProjectWorkspaceService

logger = get_logger(__name__)

TASK_BEHAVIOR_DEFAULTS = {
    "max_concurrency": 2,
    "retry_count": 0,
    "notify_on_completion": False,
}

MessageHandler = Callable[[AgentContext], Awaitable[str]]
HandlerProvider = Callable[[], Optional[MessageHandler]]


def normalize_task_behavior(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = raw or {}
    try:
        max_concurrency = int(
            raw.get("max_concurrency", TASK_BEHAVIOR_DEFAULTS["max_concurrency"])
        )
    except (TypeError, ValueError):
        max_concurrency = TASK_BEHAVIOR_DEFAULTS["max_concurrency"]
    try:
        retry_count = int(
            raw.get("retry_count", TASK_BEHAVIOR_DEFAULTS["retry_count"])
        )
    except (TypeError, ValueError):
        retry_count = TASK_BEHAVIOR_DEFAULTS["retry_count"]
    notify = raw.get(
        "notify_on_completion",
        TASK_BEHAVIOR_DEFAULTS["notify_on_completion"],
    )
    return {
        "max_concurrency": min(4, max(1, max_concurrency)),
        "retry_count": min(2, max(0, retry_count)),
        "notify_on_completion": notify if isinstance(notify, bool) else False,
    }


def load_task_behavior(path: Path) -> Dict[str, Any]:
    try:
        return normalize_task_behavior(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return dict(TASK_BEHAVIOR_DEFAULTS)


def save_task_behavior(path: Path, settings: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


class ProjectRunService:
    """Queue, limit, retry, and report planned project task executions."""

    def __init__(
        self,
        projects: ProjectManager,
        workspaces: ProjectWorkspaceService,
        handler_provider: HandlerProvider,
        behavior_path: Path,
    ) -> None:
        self.projects = projects
        self.workspaces = workspaces
        self._handler_provider = handler_provider
        self.behavior_path = behavior_path
        self.behavior = load_task_behavior(behavior_path)
        self.runs: Dict[str, asyncio.Task[Any]] = {}
        self._condition = asyncio.Condition()
        self.active_count = 0

    async def acquire_slot(self) -> None:
        async with self._condition:
            await self._condition.wait_for(
                lambda: self.active_count < self.behavior["max_concurrency"]
            )
            self.active_count += 1

    async def release_slot(self) -> None:
        async with self._condition:
            self.active_count = max(0, self.active_count - 1)
            self._condition.notify_all()

    async def update_behavior(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        settings = normalize_task_behavior(payload)
        save_task_behavior(self.behavior_path, settings)
        async with self._condition:
            self.behavior = settings
            self._condition.notify_all()
        return dict(settings)

    def runtime_payload(self) -> Dict[str, Any]:
        running_or_queued = sum(1 for task in self.runs.values() if not task.done())
        return {
            "active": self.active_count,
            "queued": max(0, running_or_queued - self.active_count),
            **self.behavior,
        }

    def add_event(self, project: Project, kind: str, text: str) -> None:
        self.projects.add_event(project.project_id, kind, text)

    def refresh_summary(self, project: Project, response: str) -> None:
        done = sum(1 for task in project.tasks if task.get("done"))
        total = len(project.tasks)
        excerpt = " ".join((response or "").split())[:900]
        result = (
            f"最近结果：{excerpt}"
            if excerpt
            else "最近一轮没有返回文本结果。"
        )
        self.projects.update_summary(
            project.project_id,
            f"任务进度：{done}/{total} 已完成。\n\n{result}",
        )

    def queue(self, project: Project, task: Dict[str, Any]) -> None:
        """Queue one planned task independently from the browser request."""
        self.projects.update(project.project_id, status="queued", last_run=True)
        self.add_event(project, "queued", f"计划任务已排队：{task['title']}")
        self.runs[project.project_id] = asyncio.create_task(
            self._run(project, task)
        )

    async def _run(self, project: Project, task: Dict[str, Any]) -> None:
        acquired = False
        try:
            await self.acquire_slot()
            acquired = True
            self.projects.update(project.project_id, status="running", last_run=True)
            self.add_event(project, "running", f"开始执行计划：{task['title']}")
            response = await self._invoke_with_retries(project, task)
            self.projects.update_task(
                project.project_id,
                task["task_id"],
                done=True,
            )
            self.projects.update(project.project_id, status="active")
            self.add_event(project, "done", "计划任务执行完成，等待确认")
            self.refresh_summary(project, response)
        except asyncio.CancelledError:
            if project.status != "paused":
                self.projects.update(project.project_id, status="active")
            self.add_event(project, "paused", "计划任务已停止")
        except Exception as error:
            logger.exception("Planned task failed for project %s", project.project_id)
            self.projects.update(project.project_id, status="failed")
            self.add_event(project, "failed", f"任务失败：{error}")
        finally:
            if acquired:
                await self.release_slot()
            if self.runs.get(project.project_id) is asyncio.current_task():
                self.runs.pop(project.project_id, None)

    async def _invoke_with_retries(
        self,
        project: Project,
        task: Dict[str, Any],
    ) -> str:
        handler = self._handler_provider()
        if handler is None:
            raise RuntimeError("Agent is not available")
        context = AgentContext(
            chat_id=project.chat_id,
            channel="webchat",
            account_id="local",
            user_message=(
                f"[Project next task]\n{task['title']}\n\n"
                "完成后简洁汇报已做的事、产物和需要我确认的事项。"
            ),
            metadata={
                "workspace_dir": str(self.workspaces.resolve(project)),
                "_plan_task_id": task.get("task_id", ""),
                "_plan_project_id": project.project_id,
            },
        )
        retry_count = self.behavior["retry_count"]
        for attempt in range(retry_count + 1):
            try:
                return await handler(context)
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt >= retry_count:
                    raise
                self.add_event(
                    project,
                    "retry",
                    f"任务失败，正在进行第 {attempt + 1} 次自动重试",
                )
                logger.warning(
                    "Retrying planned task for project %s (%s/%s)",
                    project.project_id,
                    attempt + 1,
                    retry_count,
                )
        raise RuntimeError("unreachable")
