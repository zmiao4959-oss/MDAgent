"""Persistent project metadata for the Web workbench.

A project is deliberately lighter than a separate Agent instance: it groups a
goal, one primary conversation, and its lifecycle state.  This lets users run
and pause multiple pieces of work without pretending that each project has an
isolated filesystem (that is a later, separate feature).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


PROJECT_STATUSES = frozenset({
    "active", "queued", "running", "paused", "completed", "failed", "archived",
})


@dataclass
class Project:
    project_id: str
    title: str
    objective: str
    chat_id: str
    workspace_dir: str = ""
    status: str = "active"
    created_at: float = 0.0
    updated_at: float = 0.0
    last_run_at: float = 0.0
    timeline: list[dict] = None
    tasks: list[dict] = None
    summary: str = ""
    summary_updated_at: float = 0.0

    def __post_init__(self) -> None:
        if self.timeline is None:
            self.timeline = []
        if self.tasks is None:
            self.tasks = []

    def to_dict(self) -> dict:
        return asdict(self)


class ProjectManager:
    """Small JSON-backed project store.

    Session transcripts already live in JSON + SQLite.  Keeping project
    metadata in a dedicated, human-readable file makes this feature portable
    while avoiding a migration of existing sessions.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._projects: Dict[str, Project] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for row in raw.get("projects", []):
            try:
                project = Project(**row)
            except (TypeError, ValueError):
                continue
            if project.status in PROJECT_STATUSES:
                self._projects[project.project_id] = project

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "projects": [p.to_dict() for p in self._projects.values()]}
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    def list(self, *, include_archived: bool = False) -> List[Project]:
        rows = [
            project for project in self._projects.values()
            if include_archived or project.status != "archived"
        ]
        return sorted(rows, key=lambda project: project.updated_at, reverse=True)

    def get(self, project_id: str) -> Optional[Project]:
        return self._projects.get(project_id)

    def create(self, title: str, objective: str, chat_id: str, workspace_dir: str = "") -> Project:
        now = time.time()
        project = Project(
            project_id=f"project:{uuid.uuid4().hex[:12]}",
            title=(title or "Untitled project").strip()[:120] or "Untitled project",
            objective=(objective or "").strip()[:2_000],
            chat_id=chat_id,
            workspace_dir=str(workspace_dir or ""),
            created_at=now,
            updated_at=now,
        )
        self._projects[project.project_id] = project
        self._save()
        return project

    def add_event(self, project_id: str, kind: str, text: str) -> Optional[Project]:
        project = self.get(project_id)
        if project is None:
            return None
        project.timeline.append({"at": time.time(), "kind": kind, "text": text[:300]})
        project.timeline = project.timeline[-80:]
        project.updated_at = time.time()
        self._save()
        return project

    def add_task(self, project_id: str, title: str) -> Optional[Project]:
        project = self.get(project_id)
        if project is None:
            return None
        value = title.strip()[:300]
        if not value:
            return project
        project.tasks.append({"task_id": uuid.uuid4().hex[:10], "title": value, "done": False})
        project.updated_at = time.time()
        self._save()
        return project

    def update_task(self, project_id: str, task_id: str, *, done: Optional[bool] = None, title: Optional[str] = None) -> Optional[Project]:
        project = self.get(project_id)
        if project is None:
            return None
        for task in project.tasks:
            if task.get("task_id") == task_id:
                if done is not None:
                    task["done"] = bool(done)
                if title is not None and title.strip():
                    task["title"] = title.strip()[:300]
                project.updated_at = time.time()
                self._save()
                return project
        return None

    def update_summary(self, project_id: str, summary: str) -> Optional[Project]:
        project = self.get(project_id)
        if project is None:
            return None
        project.summary = summary.strip()[:3_000]
        project.summary_updated_at = time.time()
        project.updated_at = time.time()
        self._save()
        return project

    def update(
        self,
        project_id: str,
        *,
        title: Optional[str] = None,
        objective: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        status: Optional[str] = None,
        last_run: bool = False,
    ) -> Optional[Project]:
        project = self.get(project_id)
        if project is None:
            return None
        if title is not None:
            project.title = title.strip()[:120] or project.title
        if objective is not None:
            project.objective = objective.strip()[:2_000]
        if workspace_dir is not None:
            project.workspace_dir = str(workspace_dir)
        if status is not None:
            if status not in PROJECT_STATUSES:
                raise ValueError(f"Unsupported project status: {status}")
            project.status = status
        now = time.time()
        project.updated_at = now
        if last_run:
            project.last_run_at = now
        self._save()
        return project

    def bootstrap(self, conversations: Iterable[dict]) -> None:
        """Turn existing WebChat conversations into projects once, without data loss."""
        known_chats = {project.chat_id for project in self._projects.values()}
        created = False
        for row in conversations:
            chat_id = row.get("chat_id")
            if not chat_id or chat_id in known_chats:
                continue
            title = row.get("alias") or row.get("preview") or chat_id.replace("webchat:", "")
            project = self.create(title, "", chat_id)
            project.updated_at = float(row.get("last_active") or project.updated_at)
            self._projects[project.project_id] = project
            known_chats.add(chat_id)
            created = True
        if created:
            self._save()
