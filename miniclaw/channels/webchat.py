"""
channels/webchat.py — Web 聊天界面（可折叠历史 + 左对话右可视化）
基于 FastAPI + SSE；静态资源见 channels/web/static/
"""
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..agent import AgentContext
from ..config import WORKSPACE_DIR, workspace_scope
from ..llm.base import LLMMessage
from ..logger import get_logger
from ..projects import Project, ProjectManager
from ..viz.constants import SKIP_DIR_NAMES, VISUAL_EXT
from .base import BaseChannelAdapter
from .web.files import get_workspace_asset, list_workspace_files

if TYPE_CHECKING:
    from ..memory.session import SessionManager

logger = get_logger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web" / "static"


class WebChatAdapter(BaseChannelAdapter):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        session_manager: Optional["SessionManager"] = None,
    ):
        super().__init__("webchat")
        self.host = host
        self.port = port
        self.session_manager = session_manager
        self._uvicorn_server = None
        project_path = (
            session_manager.save_dir.parent / "projects.json"
            if session_manager is not None else WORKSPACE_DIR / "projects.json"
        )
        self.projects = ProjectManager(project_path)
        self._project_runs: Dict[str, asyncio.Task] = {}
        self._run_semaphore = asyncio.Semaphore(2)

    def _project_event(self, project: Project, kind: str, text: str) -> None:
        self.projects.add_event(project.project_id, kind, text)

    def _refresh_project_summary(self, project: Project, response: str) -> None:
        done = sum(1 for task in project.tasks if task.get("done"))
        total = len(project.tasks)
        excerpt = " ".join((response or "").split())[:900]
        parts = [f"任务进度：{done}/{total} 已完成。"]
        if excerpt:
            parts.append(f"最近结果：{excerpt}")
        else:
            parts.append("最近一轮没有返回文本结果。")
        self.projects.update_summary(project.project_id, "\n\n".join(parts))

    def _run_next_task(self, project: Project, task: Dict[str, Any]) -> None:
        """Queue one planned task without tying its lifetime to a browser request."""
        async def worker() -> None:
            acquired = False
            try:
                await self._run_semaphore.acquire()
                acquired = True
                self.projects.update(project.project_id, status="running", last_run=True)
                self._project_event(project, "running", f"开始执行计划：{task['title']}")
                if self._message_handler is None:
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
                        "workspace_dir": str(self._workspace_for_project(project)),
                        # 供 after_agent hook 自动勾选任务
                        "_plan_task_id": task.get("task_id", ""),
                        "_plan_project_id": project.project_id,
                    },
                )
                response = await self._message_handler(context)
                # ── 自动勾选已完成任务 ──
                self.projects.update_task(
                    project.project_id, task["task_id"], done=True
                )
                self.projects.update(project.project_id, status="active")
                self._project_event(project, "done", "计划任务执行完成，等待确认")
                self._refresh_project_summary(project, response)
            except asyncio.CancelledError:
                if project.status != "paused":
                    self.projects.update(project.project_id, status="active")
                self._project_event(project, "paused", "计划任务已停止")
            except Exception as error:
                logger.exception("Planned task failed for project %s", project.project_id)
                self.projects.update(project.project_id, status="failed")
                self._project_event(project, "failed", f"任务失败：{error}")
            finally:
                if acquired:
                    self._run_semaphore.release()
                if self._project_runs.get(project.project_id) is asyncio.current_task():
                    self._project_runs.pop(project.project_id, None)

        self.projects.update(project.project_id, status="queued", last_run=True)
        self._project_event(project, "queued", f"计划任务已排队：{task['title']}")
        self._project_runs[project.project_id] = asyncio.create_task(worker())

    def _workspace_for_project(self, project: Project) -> Path:
        return Path(project.workspace_dir or WORKSPACE_DIR).resolve()

    def _create_project_workspace(self, project: Project) -> Path:
        workspace = (WORKSPACE_DIR / "projects" / project.project_id.replace(":", "_")).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        self.projects.update(project.project_id, workspace_dir=str(workspace))
        return workspace

    async def _ensure_projects(self) -> None:
        """Expose pre-existing conversations as projects on first use."""
        if self.projects.list(include_archived=True) or self.session_manager is None:
            return
        rows = self.session_manager.list_conversations_by_channel("webchat")
        self.projects.bootstrap(rows)
        for project in self.projects.list(include_archived=True):
            if not project.workspace_dir:
                self.projects.update(project.project_id, workspace_dir=str(WORKSPACE_DIR.resolve()))
        if self.projects.list(include_archived=True):
            return
        chat_id = "webchat:default"
        session = self.session_manager.get_or_create(chat_id, "webchat", "local")
        await self.session_manager.save(session)
        self.projects.create("Quick start", "", chat_id, str(WORKSPACE_DIR.resolve()))

    async def _project_payload(self, project: Project) -> Dict[str, Any]:
        """Decorate persisted metadata with a lightweight live runtime view."""
        payload = project.to_dict()
        task = self._project_runs.get(project.project_id)
        payload["is_running"] = bool(task and not task.done())
        workspace = self._workspace_for_project(project)
        payload["workspace_dir"] = str(workspace)
        payload["workspace_isolated"] = workspace != WORKSPACE_DIR.resolve()
        payload["preview"] = ""
        payload["message_count"] = 0
        if self.session_manager is not None:
            session = await self.session_manager.resolve_session(project.chat_id)
            if session is not None:
                payload["message_count"] = len(session.messages)
                for message in reversed(session.messages):
                    if message.role in ("assistant", "user") and (message.content or "").strip():
                        payload["preview"] = (message.content or "")[:120].replace("\n", " ")
                        break
        return payload

    def _project_artifacts(self, project: Project, limit: int = 60) -> list[Dict[str, Any]]:
        """Return recent project files for the workbench artifact panel."""
        root = self._workspace_for_project(project)
        if not root.is_dir():
            return []
        rows: list[Dict[str, Any]] = []
        try:
            for path in root.rglob("*"):
                if not path.is_file() or any(part in SKIP_DIR_NAMES for part in path.relative_to(root).parts):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                rows.append({
                    "name": path.name,
                    "path": str(path.resolve()),
                    "rel_path": path.relative_to(root).as_posix(),
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "ext": path.suffix.lower(),
                })
        except OSError:
            return []
        return sorted(rows, key=lambda row: row["modified_at"], reverse=True)[:limit]

    async def start(self):
        try:
            from fastapi import FastAPI, Request
            from fastapi.responses import FileResponse, StreamingResponse
            from fastapi.staticfiles import StaticFiles
            import uvicorn
        except ImportError:
            logger.error("fastapi/uvicorn not installed")
            return

        app = FastAPI(title="MiniClaw")
        adapter = self

        if WEB_DIR.is_dir():
            app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

        @app.get("/")
        async def index():
            index_path = WEB_DIR / "index.html"
            if index_path.is_file():
                return FileResponse(index_path)
            return FileResponse(__file__)  # fallback，不应发生

        @app.get("/api/config")
        async def get_config():
            return {
                "workspace_root": str(WORKSPACE_DIR.resolve()),
                "visual_ext": sorted(VISUAL_EXT),
                "skip_dir_names": sorted(SKIP_DIR_NAMES),
            }

        @app.get("/api/stats")
        async def get_stats():
            """返回 Agent 运行统计"""
            from ..stats import agent_stats as stats_tracker
            from ..hooks import hook_system as hs
            return {
                "agent": stats_tracker.summary(),
                "hooks": hs.list_hooks(),
            }

        @app.get("/api/files")
        async def list_files(work_dir: str = "", project_id: str = ""):
            project = adapter.projects.get(project_id) if project_id else None
            if project_id and project is None:
                return {"ok": False, "error": "project not found"}
            with workspace_scope(adapter._workspace_for_project(project) if project else None):
                return list_workspace_files(work_dir)

        @app.get("/api/asset")
        async def get_asset(path: str, project_id: str = ""):
            project = adapter.projects.get(project_id) if project_id else None
            if project_id and project is None:
                return {"ok": False, "error": "project not found"}
            with workspace_scope(adapter._workspace_for_project(project) if project else None):
                return get_workspace_asset(path)

        @app.get("/api/projects")
        async def list_projects(include_archived: bool = False):
            await adapter._ensure_projects()
            projects = adapter.projects.list(include_archived=include_archived)
            return {"projects": [await adapter._project_payload(project) for project in projects]}

        @app.post("/api/projects")
        async def create_project(request: Request):
            if adapter.session_manager is None:
                return {"ok": False, "error": "session_manager not available"}
            body = await request.json()
            title = (body.get("title") or "").strip()
            objective = (body.get("objective") or "").strip()
            if not title:
                return {"ok": False, "error": "title is required"}

            chat_id = f"webchat:{uuid.uuid4().hex[:10]}"
            session = adapter.session_manager.get_or_create(chat_id, "webchat", "local")
            session.metadata["alias"] = title
            await adapter.session_manager.save(session)
            project = adapter.projects.create(title, objective, chat_id)
            adapter._create_project_workspace(project)
            return {"ok": True, "project": await adapter._project_payload(project)}

        @app.get("/api/projects/{project_id}/artifacts")
        async def project_artifacts(project_id: str):
            project = adapter.projects.get(project_id)
            if project is None:
                return {"ok": False, "error": "project not found"}
            return {
                "ok": True,
                "workspace_dir": str(adapter._workspace_for_project(project)),
                "artifacts": adapter._project_artifacts(project),
            }

        @app.get("/api/projects/{project_id}/summary")
        async def project_summary(project_id: str):
            project = adapter.projects.get(project_id)
            if project is None:
                return {"ok": False, "error": "project not found"}
            payload = await adapter._project_payload(project)
            artifacts = adapter._project_artifacts(project)
            return {
                "ok": True,
                "project": payload,
                "artifact_count": len(artifacts),
                "latest_artifact": artifacts[0] if artifacts else None,
            }

        @app.get("/api/projects/{project_id}/timeline")
        async def project_timeline(project_id: str):
            project = adapter.projects.get(project_id)
            if project is None:
                return {"ok": False, "error": "project not found"}
            return {"ok": True, "events": list(reversed(project.timeline or []))}

        @app.get("/api/projects/{project_id}/plan")
        async def project_plan(project_id: str):
            project = adapter.projects.get(project_id)
            if project is None:
                return {"ok": False, "error": "project not found"}
            return {"ok": True, "tasks": project.tasks, "summary": project.summary, "summary_updated_at": project.summary_updated_at}

        @app.post("/api/projects/{project_id}/tasks")
        async def add_project_task(project_id: str, request: Request):
            body = await request.json()
            project = adapter.projects.add_task(project_id, body.get("title") or "")
            if project is None:
                return {"ok": False, "error": "project or task not found"}
            return {"ok": True, "tasks": project.tasks}

        @app.patch("/api/projects/{project_id}/tasks/{task_id}")
        async def update_project_task(project_id: str, task_id: str, request: Request):
            body = await request.json()
            project = adapter.projects.update_task(project_id, task_id, done=body.get("done"), title=body.get("title"))
            if project is None:
                return {"ok": False, "error": "project or task not found"}
            adapter._refresh_project_summary(project, "")
            return {"ok": True, "tasks": project.tasks, "summary": project.summary}

        @app.post("/api/projects/{project_id}/run-next")
        async def run_next_task(project_id: str):
            project = adapter.projects.get(project_id)
            if project is None:
                return {"ok": False, "error": "project not found"}
            active = adapter._project_runs.get(project_id)
            if active and not active.done():
                return {"ok": False, "error": "project already has a running task"}
            task = next((item for item in project.tasks if not item.get("done")), None)
            if task is None:
                return {"ok": False, "error": "no unfinished task"}
            adapter._run_next_task(project, task)
            return {"ok": True, "task": task}

        @app.patch("/api/projects/{project_id}")
        async def update_project(project_id: str, request: Request):
            body = await request.json()
            project = adapter.projects.get(project_id)
            if project is None:
                return {"ok": False, "error": "project not found"}
            title = body.get("title") if "title" in body else None
            objective = body.get("objective") if "objective" in body else None
            try:
                project = adapter.projects.update(project_id, title=title, objective=objective)
            except ValueError as error:
                return {"ok": False, "error": str(error)}
            if project is None:
                return {"ok": False, "error": "project not found"}
            if title and adapter.session_manager is not None:
                session = await adapter.session_manager.resolve_session(project.chat_id)
                if session is not None:
                    session.metadata["alias"] = project.title
                    await adapter.session_manager.save(session)
            return {"ok": True, "project": await adapter._project_payload(project)}

        @app.post("/api/projects/{project_id}/action")
        async def project_action(project_id: str, request: Request):
            body = await request.json()
            action = body.get("action")
            project = adapter.projects.get(project_id)
            if project is None:
                return {"ok": False, "error": "project not found"}

            task = adapter._project_runs.get(project_id)
            is_running = bool(task and not task.done())
            if action == "pause":
                project = adapter.projects.update(project_id, status="paused")
                adapter._project_event(project, "paused", "项目已暂停")
                if is_running:
                    task.cancel()
            elif action == "resume":
                if project.status != "paused":
                    return {"ok": False, "error": "only paused projects can resume"}
                project = adapter.projects.update(project_id, status="active")
                adapter._project_event(project, "resumed", "项目已恢复，可继续发送任务")
            elif action == "complete":
                if is_running:
                    return {"ok": False, "error": "pause the running project before completing it"}
                project = adapter.projects.update(project_id, status="completed")
            elif action == "archive":
                if is_running:
                    return {"ok": False, "error": "pause the running project before archiving it"}
                project = adapter.projects.update(project_id, status="archived")
            else:
                return {"ok": False, "error": "unsupported project action"}
            return {"ok": True, "project": await adapter._project_payload(project)}

        @app.get("/api/conversations")
        async def list_conversations():
            if not adapter.session_manager:
                return {"conversations": []}
            rows = adapter.session_manager.list_conversations_by_channel("webchat")
            # 补充别名信息（从 session metadata）
            for row in rows:
                session = await adapter.session_manager.resolve_session(row["chat_id"])
                if session and session.metadata.get("alias"):
                    row["alias"] = session.metadata["alias"]
            return {"conversations": rows}

        @app.post("/api/conversations")
        async def new_conversation():
            if not adapter.session_manager:
                chat_id = f"webchat:{uuid.uuid4().hex[:10]}"
                return {"chat_id": chat_id}
            chat_id = f"webchat:{uuid.uuid4().hex[:10]}"
            session = adapter.session_manager.get_or_create(chat_id, "webchat", "local")
            await adapter.session_manager.save(session)
            return {"chat_id": chat_id}

        @app.post("/api/conversations/{chat_id}/fork")
        async def fork_conversation(chat_id: str, request: Request):
            """
            从指定消息索引处分叉对话。

            Body (JSON):
              - at_index (int, optional): 从此索引处截断并分叉。
                0 = fork 只保留 system prompt.
                -1 (默认) = fork 整个对话.

            返回新的 chat_id，新会话包含原会话到 at_index 为止的所有消息。
            """
            if not adapter.session_manager:
                return {"ok": False, "error": "session_manager not available"}

            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            at_index = body.get("at_index", -1)

            src = await adapter.session_manager.resolve_session(chat_id)
            if not src:
                return {"ok": False, "error": f"Source conversation not found: {chat_id}"}

            # 创建新会话
            new_chat_id = f"webchat:{uuid.uuid4().hex[:10]}"
            new_session = adapter.session_manager.get_or_create(new_chat_id, "webchat", "local")

            # 复制消息
            if at_index >= 0:
                new_session.messages = list(src.messages[:at_index])
            else:
                new_session.messages = list(src.messages)

            # 复制 metadata
            new_session.metadata = dict(src.metadata)
            new_session.metadata["forked_from"] = chat_id
            new_session.metadata["forked_at_index"] = at_index
            new_session.metadata["total_tokens"] = 0  # 重置 token 计数

            await adapter.session_manager.save(new_session)
            logger.info("Forked conversation %s → %s (at index %s)", chat_id, new_chat_id, at_index)
            return {
                "ok": True,
                "chat_id": new_chat_id,
                "forked_from": chat_id,
                "message_count": len(new_session.messages),
            }

        @app.patch("/api/conversations/{chat_id}")
        async def rename_conversation(chat_id: str, request: Request):
            """重命名会话（别名存入 session metadata，持久化到 JSON）。"""
            if not adapter.session_manager:
                return {"ok": False, "error": "session_manager not available"}
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            alias = (body.get("alias") or "").strip()
            if not alias:
                return {"ok": False, "error": "alias is required"}

            session = await adapter.session_manager.resolve_session(chat_id)
            if not session:
                return {"ok": False, "error": f"Conversation not found: {chat_id}"}
            session.metadata["alias"] = alias
            await adapter.session_manager.save(session)
            logger.info("Renamed conversation %s → '%s'", chat_id, alias)
            return {"ok": True, "chat_id": chat_id, "alias": alias}

        @app.delete("/api/conversations/{chat_id}")
        async def delete_conversation(chat_id: str):
            """删除指定会话（从内存和磁盘移除）。"""
            if not adapter.session_manager:
                return {"ok": False, "error": "session_manager not available"}
            try:
                await adapter.session_manager.delete_session(chat_id)
                return {"ok": True, "chat_id": chat_id}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        @app.get("/api/history")
        async def history(chat_id: str):
            if not adapter.session_manager:
                return {"messages": [], "chat_id": chat_id}
            session = await adapter.session_manager.resolve_session(chat_id)
            if not session:
                return {"messages": [], "chat_id": chat_id}
            out = []
            for m in session.messages:
                if m.role == "system":
                    continue
                if m.role == "tool":
                    name = m.name or "tool"
                    snippet = (m.content or "")[:600]
                    out.append({"role": "tool", "content": f"[{name}] {snippet}"})
                    continue
                out.append({"role": m.role, "content": m.content or ""})
            return {"messages": out, "chat_id": chat_id}

        @app.get("/api/export")
        async def export_conversation(chat_id: str, fmt: str = "markdown"):
            """导出对话为 Markdown 或 JSON。"""
            if not adapter.session_manager:
                return {"error": "session_manager not available"}
            session = await adapter.session_manager.resolve_session(chat_id)
            if not session:
                return {"error": "conversation not found", "chat_id": chat_id}

            if fmt == "json":
                return {
                    "chat_id": chat_id,
                    "exported_at": datetime.now().isoformat(),
                    "messages": [
                        {"role": m.role, "content": m.content, "name": m.name}
                        for m in session.messages
                    ],
                }

            # Markdown 导出
            lines = [
                f"# MiniClaw 对话导出",
                f"",
                f"- **Chat ID**: `{chat_id}`",
                f"- **导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"- **消息数**: {len(session.messages)}",
                f"",
                f"---",
                f"",
            ]
            for m in session.messages:
                if m.role == "system":
                    continue
                role_label = {"user": "👤 用户", "assistant": "🤖 Agent", "tool": "🔧 工具"}.get(m.role, m.role)
                lines.append(f"### {role_label}")
                if m.role == "tool" and m.name:
                    lines.append(f"*工具: `{m.name}`*")
                lines.append("")
                lines.append(m.content or "(空)")
                lines.append("")
                lines.append("---")
                lines.append("")

            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(
                "\n".join(lines),
                media_type="text/markdown; charset=utf-8",
                headers={
                    "Content-Disposition": f"attachment; filename=miniclaw-{chat_id.replace(':', '-')}.md"
                },
            )

        @app.post("/api/chat")
        async def chat(request: Request):
            body = await request.json()
            user_message = body.get("message", "")
            chat_id = body.get("chat_id") or "webchat:default"
            project_id = body.get("project_id") or ""
            plan_mode = bool(body.get("plan_mode", False))
            project: Optional[Project] = None
            if project_id:
                project = adapter.projects.get(project_id)
                if project is None:
                    return {"ok": False, "error": "project not found"}
                if project.status in ("paused", "completed", "archived"):
                    return {
                        "ok": False,
                        "error": f"project is {project.status}; resume it before sending a message",
                    }
                # Trust the server-side project mapping over a client chat id.
                chat_id = project.chat_id
                adapter.projects.update(project_id, status="queued", last_run=True)
                adapter._project_event(project, "queued", "任务已加入运行队列")
            # Agent 任务超时 (默认 5 分钟)
            agent_timeout = float(body.get("timeout", 300))

            async def event_stream():
                metadata: Dict[str, Any] = {}
                if project is not None:
                    metadata["workspace_dir"] = str(
                        adapter._workspace_for_project(project)
                    )
                    metadata["project_id"] = project.project_id
                    metadata["project_title"] = project.title
                    metadata["project_objective"] = project.objective
                if plan_mode:
                    metadata["plan_mode"] = True
                ctx = AgentContext(
                    chat_id=chat_id,
                    channel="webchat",
                    account_id="local",
                    user_message=user_message,
                    metadata=metadata,
                )
                out_q: asyncio.Queue = asyncio.Queue()
                result: Dict[str, Any] = {"response": None}
                subscribed = True

                async def collect_delta(chunk: str) -> None:
                    if subscribed:
                        await out_q.put({"kind": "delta", "text": chunk})

                async def on_viz(event: Dict[str, Any]) -> None:
                    payload = dict(event)
                    payload["viz"] = True
                    if subscribed:
                        await out_q.put(payload)

                async def on_progress(data: Dict[str, Any]) -> None:
                    if subscribed:
                        await out_q.put({"kind": "progress", "data": data})

                async def run_agent() -> None:
                    if project is not None:
                        try:
                            await adapter._run_semaphore.acquire()
                        except asyncio.CancelledError:
                            result["response"] = "[Project paused]"
                            if adapter._project_runs.get(project.project_id) is asyncio.current_task():
                                adapter._project_runs.pop(project.project_id, None)
                            await out_q.put(None)
                            return
                        adapter.projects.update(project.project_id, status="running")
                        adapter._project_event(project, "running", "Agent 开始处理项目任务")
                    try:
                        if adapter._message_handler:
                            result["response"] = await asyncio.wait_for(
                                adapter._message_handler(
                                    ctx,
                                    on_stream_chunk=collect_delta,
                                    on_viz_event=on_viz,
                                    on_progress=on_progress,
                                ),
                                timeout=agent_timeout,
                            )
                    except asyncio.TimeoutError:
                        result["response"] = "[Agent 任务超时，请简化需求后重试]"
                        logger.warning("Agent timed out for chat_id=%s after %.0fs", chat_id, agent_timeout)
                        if project is not None:
                            adapter.projects.update(project.project_id, status="failed")
                    except asyncio.CancelledError:
                        if project is not None and project.status == "paused":
                            result["response"] = "[Project paused]"
                        else:
                            result["response"] = "[Agent run cancelled]"
                    except Exception as error:
                        logger.exception("Agent failed for chat_id=%s", chat_id)
                        result["response"] = f"[Agent error: {error}]"
                        if project is not None:
                            adapter.projects.update(project.project_id, status="failed")
                    finally:
                        pending = ctx.metadata.get("viz_tasks") or []
                        if pending:
                            viz_results = await asyncio.gather(*pending, return_exceptions=True)
                            for vr in viz_results:
                                if isinstance(vr, Exception):
                                    logger.warning("Viz task failed: %s", vr)
                        if project is not None:
                            latest = adapter.projects.get(project.project_id)
                            if latest is not None and latest.status == "running":
                                adapter.projects.update(project.project_id, status="active")
                                adapter._project_event(project, "done", "本轮任务已完成，等待下一步")
                                adapter._refresh_project_summary(project, result["response"] or "")
                            if adapter._project_runs.get(project.project_id) is asyncio.current_task():
                                adapter._project_runs.pop(project.project_id, None)
                            adapter._run_semaphore.release()
                        await out_q.put(None)

                task = asyncio.create_task(run_agent())
                if project is not None:
                    adapter._project_runs[project.project_id] = task
                try:
                    while True:
                        try:
                            item = await asyncio.wait_for(out_q.get(), timeout=10.0)
                        except asyncio.TimeoutError:
                            # 检查客户端是否断开
                            if await request.is_disconnected():
                                subscribed = False
                                if project is not None:
                                    logger.info("Client disconnected; project %s keeps running", project.project_id)
                                else:
                                    logger.info("Client disconnected for chat_id=%s, cancelling agent task", chat_id)
                                    task.cancel()
                                    try:
                                        await task
                                    except (asyncio.CancelledError, Exception):
                                        pass
                                return
                            continue
                        if item is None:
                            break
                        if item.get("viz"):
                            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                        elif item.get("kind") == "delta":
                            yield f"data: {json.dumps({'delta': item['text']}, ensure_ascii=False)}\n\n"
                        elif item.get("kind") == "progress":
                            yield f"data: {json.dumps({'progress': item['data']}, ensure_ascii=False)}\n\n"
                finally:
                    # Project runs survive a browser tab switch; ordinary
                    # one-off chats keep the previous cancellation behaviour.
                    if project is None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass

                yield f"data: {json.dumps({'done': True, 'full': result['response'] or ''}, ensure_ascii=False)}\n\n"

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        self._uvicorn_server = uvicorn.Server(config)
        logger.info(f"WebChat at http://{self.host}:{self.port}")
        try:
            await self._uvicorn_server.serve()
        finally:
            self._uvicorn_server = None
            logger.info("WebChat stopped")

    async def stop(self):
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True

    async def send_message(self, chat_id: str, text: str, **kwargs):
        if not self.session_manager:
            raise RuntimeError("WebChat session manager is not available")
        target = chat_id or "default"
        if not target.startswith("webchat:"):
            target = f"webchat:{target}"
        session = await self.session_manager.resolve_session(target)
        if session is None:
            session = self.session_manager.get_or_create(target, "webchat", "local")
        session.add_message(LLMMessage(role="assistant", content=text))
        await self.session_manager.save(session)
