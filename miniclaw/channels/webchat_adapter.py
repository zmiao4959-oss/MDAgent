"""WebChat channel adapter and service composition root."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..logger import get_logger
from ..projects import Project, ProjectManager
from ..settings import WORKSPACE_DIR
from .base import BaseChannelAdapter
from .web.conversation_service import ConversationService
from .web.diagnostics import collect_runtime_diagnostics
from .web.knowledge_service import KnowledgeService
from .web.project_run import ProjectRunService
from .web.project_workspace import ProjectWorkspaceService

if TYPE_CHECKING:
    from ..memory.session import SessionManager

logger = get_logger(__name__)


class WebChatAdapter(BaseChannelAdapter):
    """Compose WebChat services and own the HTTP server lifecycle."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        session_manager: Optional["SessionManager"] = None,
        source_patch_llm=None,
    ) -> None:
        super().__init__("webchat")
        self.host = host
        self.port = port
        self.session_manager = session_manager
        self.source_patch_llm = source_patch_llm
        self._uvicorn_server = None

        project_path = (
            session_manager.save_dir.parent / "projects.json"
            if session_manager is not None
            else WORKSPACE_DIR / "projects.json"
        )
        self.projects = ProjectManager(project_path)
        self.project_workspaces = ProjectWorkspaceService(
            self.projects,
            session_manager,
            WORKSPACE_DIR,
        )
        self.project_runner = ProjectRunService(
            self.projects,
            self.project_workspaces,
            lambda: self._message_handler,
            WORKSPACE_DIR / "webchat-task-behavior.json",
        )
        self.conversations = ConversationService(session_manager)
        self.knowledge = KnowledgeService()

    async def start(self) -> None:
        try:
            import uvicorn

            from .web.web_app import create_web_app
        except ImportError:
            logger.error("fastapi/uvicorn not installed")
            return

        app = create_web_app(self)
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        self._uvicorn_server = uvicorn.Server(config)
        logger.info("WebChat at http://%s:%s", self.host, self.port)
        try:
            await self._uvicorn_server.serve()
        finally:
            self._uvicorn_server = None
            logger.info("WebChat stopped")

    async def stop(self) -> None:
        await self.knowledge.stop()
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        await self.conversations.deliver(chat_id, text)

    async def _runtime_diagnostics(self) -> Dict[str, Any]:
        payload = await asyncio.to_thread(collect_runtime_diagnostics)
        payload["items"].insert(
            0,
            {
                "id": "agent",
                "label": "Agent",
                "state": (
                    "ready"
                    if self._message_handler is not None
                    else "unavailable"
                ),
                "detail": (
                    "消息处理器已连接"
                    if self._message_handler is not None
                    else "消息处理器未连接"
                ),
            },
        )
        payload["task_runtime"] = self.project_runner.runtime_payload()
        return payload

    # Compatibility shims for extensions written against the former monolith.
    async def _acquire_run_slot(self) -> None:
        await self.project_runner.acquire_slot()

    async def _release_run_slot(self) -> None:
        await self.project_runner.release_slot()

    async def _update_task_behavior(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await self.project_runner.update_behavior(payload)

    def _knowledge_payload(self) -> Dict[str, Any]:
        return self.knowledge.payload()

    def _start_knowledge_job(self, action: str) -> None:
        self.knowledge.start(action)

    def _project_event(self, project: Project, kind: str, text: str) -> None:
        self.project_runner.add_event(project, kind, text)

    def _refresh_project_summary(self, project: Project, response: str) -> None:
        self.project_runner.refresh_summary(project, response)

    def _run_next_task(self, project: Project, task: Dict[str, Any]) -> None:
        self.project_runner.queue(project, task)

    def _workspace_for_project(self, project: Project) -> Path:
        return self.project_workspaces.resolve(project)

    def _create_project_workspace(self, project: Project) -> Path:
        return self.project_workspaces.create(project)

    async def _ensure_projects(self) -> None:
        await self.project_workspaces.ensure_projects()

    async def _project_payload(self, project: Project) -> Dict[str, Any]:
        return await self.project_workspaces.payload(project, self._project_runs)

    def _project_artifacts(
        self,
        project: Project,
        limit: int = 60,
    ) -> list[Dict[str, Any]]:
        return self.project_workspaces.artifacts(project, limit)

    @property
    def _project_runs(self) -> Dict[str, asyncio.Task[Any]]:
        return self.project_runner.runs

    @property
    def _task_behavior(self) -> Dict[str, Any]:
        return self.project_runner.behavior

    @property
    def _knowledge_job(self) -> Optional[asyncio.Task[Any]]:
        return self.knowledge.task

    @property
    def _knowledge_job_state(self) -> Dict[str, Any]:
        return self.knowledge.state
