"""Lifecycle service for one WebChat Agent run."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from ...logger import get_logger
from .chat_events import ChatEventStream

logger = get_logger(__name__)


class ChatRunService:
    """Run an Agent request while owning timeout and project state transitions."""

    def __init__(
        self,
        *,
        adapter: Any,
        context: Any,
        project: Optional[Any],
        timeout: float,
        events: ChatEventStream,
    ):
        self.adapter = adapter
        self.context = context
        self.project = project
        self.timeout = timeout
        self.events = events
        self.result: Dict[str, Any] = {"response": None}
        self.context.metadata["_on_task_event"] = self.events.publish_task

    async def run(self) -> None:
        project = self.project
        if project is not None:
            try:
                await self.adapter._acquire_run_slot()
            except asyncio.CancelledError:
                self.result["response"] = "[Project paused]"
                if self.adapter._project_runs.get(project.project_id) is asyncio.current_task():
                    self.adapter._project_runs.pop(project.project_id, None)
                await self.events.close()
                return
            self.adapter.projects.update(project.project_id, status="running")
            self.adapter._project_event(project, "running", "Agent 开始处理项目任务")

        try:
            if self.adapter._message_handler:
                self.result["response"] = await asyncio.wait_for(
                    self.adapter._message_handler(
                        self.context,
                        on_stream_chunk=self.events.publish_delta,
                        on_viz_event=self.events.publish_visualization,
                        on_progress=self.events.publish_progress,
                    ),
                    timeout=self.timeout,
                )
        except asyncio.TimeoutError:
            self.result["response"] = "[Agent 任务超时，请简化需求后重试]"
            logger.warning(
                "Agent timed out for chat_id=%s after %.0fs",
                self.context.chat_id,
                self.timeout,
            )
            if project is not None:
                self.adapter.projects.update(project.project_id, status="failed")
        except asyncio.CancelledError:
            if project is not None and project.status == "paused":
                self.result["response"] = "[Project paused]"
            else:
                self.result["response"] = "[Agent run cancelled]"
        except Exception as error:
            logger.exception("Agent failed for chat_id=%s", self.context.chat_id)
            self.result["response"] = f"[Agent error: {error}]"
            if project is not None:
                self.adapter.projects.update(project.project_id, status="failed")
        finally:
            await self._finish_pending_visualizations()
            if project is not None:
                latest = self.adapter.projects.get(project.project_id)
                if latest is not None and latest.status == "running":
                    self.adapter.projects.update(project.project_id, status="active")
                    self.adapter._project_event(
                        project, "done", "本轮任务已完成，等待下一步"
                    )
                    self.adapter._refresh_project_summary(
                        project,
                        self.result["response"] or "",
                    )
                if self.adapter._project_runs.get(project.project_id) is asyncio.current_task():
                    self.adapter._project_runs.pop(project.project_id, None)
                await self.adapter._release_run_slot()
            await self.events.close()

    async def _finish_pending_visualizations(self) -> None:
        pending = self.context.metadata.get("viz_tasks") or []
        if not pending:
            return
        results = await asyncio.gather(*pending, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Viz task failed: %s", result)
