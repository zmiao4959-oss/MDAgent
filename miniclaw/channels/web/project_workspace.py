"""Project workspace, artifact, and presentation services."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional

from ...projects import Project, ProjectManager
from ...viz.constants import SKIP_DIR_NAMES

if TYPE_CHECKING:
    from ...memory.session import SessionManager


class ProjectWorkspaceService:
    """Own filesystem and session-derived views for WebChat projects."""

    def __init__(
        self,
        projects: ProjectManager,
        session_manager: Optional["SessionManager"],
        workspace_root: Path,
    ) -> None:
        self.projects = projects
        self.session_manager = session_manager
        self.workspace_root = workspace_root.resolve()

    def resolve(self, project: Project) -> Path:
        return Path(project.workspace_dir or self.workspace_root).resolve()

    def create(self, project: Project) -> Path:
        workspace = (
            self.workspace_root
            / "projects"
            / project.project_id.replace(":", "_")
        ).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        self.projects.update(project.project_id, workspace_dir=str(workspace))
        return workspace

    async def ensure_projects(self) -> None:
        """Expose pre-existing WebChat conversations as projects on first use."""
        if self.projects.list(include_archived=True) or self.session_manager is None:
            return

        rows = self.session_manager.list_conversations_by_channel("webchat")
        self.projects.bootstrap(rows)
        for project in self.projects.list(include_archived=True):
            if not project.workspace_dir:
                self.projects.update(
                    project.project_id,
                    workspace_dir=str(self.workspace_root),
                )
        if self.projects.list(include_archived=True):
            return

        chat_id = "webchat:default"
        session = self.session_manager.get_or_create(chat_id, "webchat", "local")
        await self.session_manager.save(session)
        self.projects.create(
            "Quick start",
            "",
            chat_id,
            str(self.workspace_root),
        )

    async def payload(
        self,
        project: Project,
        runs: Mapping[str, asyncio.Task[Any]],
    ) -> Dict[str, Any]:
        """Decorate persisted metadata with lightweight live runtime state."""
        payload = project.to_dict()
        task = runs.get(project.project_id)
        payload["is_running"] = bool(task and not task.done())
        workspace = self.resolve(project)
        payload["workspace_dir"] = str(workspace)
        payload["workspace_isolated"] = workspace != self.workspace_root
        payload["preview"] = ""
        payload["message_count"] = 0

        if self.session_manager is None:
            return payload
        session = await self.session_manager.resolve_session(project.chat_id)
        if session is None:
            return payload

        payload["message_count"] = len(session.messages)
        for message in reversed(session.messages):
            content = (message.content or "").strip()
            if message.role in ("assistant", "user") and content:
                payload["preview"] = content[:120].replace("\n", " ")
                break
        return payload

    def artifacts(self, project: Project, limit: int = 60) -> list[Dict[str, Any]]:
        """Return recent project files for the workbench artifact panel."""
        root = self.resolve(project)
        if not root.is_dir():
            return []

        rows: list[Dict[str, Any]] = []
        try:
            for path in root.rglob("*"):
                relative = path.relative_to(root)
                if (
                    not path.is_file()
                    or any(part in SKIP_DIR_NAMES for part in relative.parts)
                ):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                rows.append(
                    {
                        "name": path.name,
                        "path": str(path.resolve()),
                        "rel_path": relative.as_posix(),
                        "size": stat.st_size,
                        "modified_at": stat.st_mtime,
                        "ext": path.suffix.lower(),
                    }
                )
        except OSError:
            return []
        return sorted(
            rows,
            key=lambda row: row["modified_at"],
            reverse=True,
        )[:limit]
