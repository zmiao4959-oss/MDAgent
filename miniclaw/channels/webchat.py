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
from ..config import WORKSPACE_DIR
from ..llm.base import LLMMessage
from ..logger import get_logger
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

        @app.get("/api/files")
        async def list_files(work_dir: str = ""):
            return list_workspace_files(work_dir)

        @app.get("/api/asset")
        async def get_asset(path: str):
            return get_workspace_asset(path)

        @app.get("/api/conversations")
        async def list_conversations():
            if not adapter.session_manager:
                return {"conversations": []}
            rows = adapter.session_manager.list_conversations_by_channel("webchat")
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

            async def event_stream():
                ctx = AgentContext(
                    chat_id=chat_id,
                    channel="webchat",
                    account_id="local",
                    user_message=user_message,
                )
                out_q: asyncio.Queue = asyncio.Queue()
                result: Dict[str, Any] = {"response": None}

                async def collect_delta(chunk: str) -> None:
                    await out_q.put({"kind": "delta", "text": chunk})

                async def on_viz(event: Dict[str, Any]) -> None:
                    payload = dict(event)
                    payload["viz"] = True
                    await out_q.put(payload)

                async def on_progress(data: Dict[str, Any]) -> None:
                    await out_q.put({"kind": "progress", "data": data})

                async def run_agent() -> None:
                    try:
                        if adapter._message_handler:
                            result["response"] = await adapter._message_handler(
                                ctx,
                                on_stream_chunk=collect_delta,
                                on_viz_event=on_viz,
                                on_progress=on_progress,
                            )
                    finally:
                        pending = ctx.metadata.get("viz_tasks") or []
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        await out_q.put(None)

                task = asyncio.create_task(run_agent())
                try:
                    while True:
                        item = await out_q.get()
                        if item is None:
                            break
                        if item.get("viz"):
                            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                        elif item.get("kind") == "delta":
                            yield f"data: {json.dumps({'delta': item['text']}, ensure_ascii=False)}\n\n"
                        elif item.get("kind") == "progress":
                            yield f"data: {json.dumps({'progress': item['data']}, ensure_ascii=False)}\n\n"
                finally:
                    await task

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
