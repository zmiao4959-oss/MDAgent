"""
canvas.py — Agent-to-UI 画布系统
"""
from pathlib import Path
from .config import WORKSPACE_DIR
from .logger import get_logger

logger = get_logger(__name__)


class CanvasManager:
    """
    Agent 可以通过写 HTML/CSS/JS 到 canvas 目录来动态展示内容，
    然后通过 Gateway HTTP 服务提供给浏览器访问。
    """
    
    def __init__(self, canvas_dir: Path = None):
        self.canvas_dir = canvas_dir or WORKSPACE_DIR / "canvas"
        self.canvas_dir.mkdir(parents=True, exist_ok=True)
    
    def present(self, html_content: str):
        """Agent 写入 HTML 内容到 canvas"""
        index_path = self.canvas_dir / "index.html"
        index_path.write_text(html_content, encoding="utf-8")
        logger.info(f"Canvas updated: {len(html_content)} bytes")
        return f"Canvas presented at /canvas/"
    
    def read_current(self) -> str:
        index_path = self.canvas_dir / "index.html"
        if index_path.exists():
            return index_path.read_text(encoding="utf-8")
        return ""