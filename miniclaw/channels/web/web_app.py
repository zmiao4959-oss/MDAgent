"""FastAPI application assembly for WebChat."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .capsule_routes import register_capsule_routes
from .chat_routes import register_chat_routes
from .conversation_routes import register_conversation_routes
from .evolution_routes import register_evolution_routes
from .file_routes import register_file_routes
from .knowledge_routes import register_knowledge_routes
from .project_routes import register_project_routes
from .project_task_routes import register_project_task_routes
from .system_routes import register_system_routes

WEB_DIR = Path(__file__).resolve().parent / "static"


def create_web_app(adapter: Any) -> FastAPI:
    """Build the complete WebChat HTTP application."""
    app = FastAPI(title="MiniClaw")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    async def index():
        index_path = WEB_DIR / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        return FileResponse(__file__)

    register_system_routes(app, adapter)
    register_knowledge_routes(app, adapter)
    register_file_routes(app, adapter)
    register_evolution_routes(app, adapter)
    register_capsule_routes(app, adapter)
    register_project_routes(app, adapter)
    register_project_task_routes(app, adapter)
    register_conversation_routes(app, adapter)
    register_chat_routes(app, adapter)
    return app
