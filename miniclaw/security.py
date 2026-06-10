"""
security.py — 安全系统
"""
from typing import List, Dict, Optional
from pathlib import Path
from .config import WORKSPACE_DIR
from .tools.registry import ToolDefinition
from .logger import get_logger

logger = get_logger(__name__)


class SecurityPolicy:
    """
    安全策略：
    1. 危险工具需要审批（exec, write 等）
    2. 可配置允许的目录（防止读写系统文件）
    3. 网络访问白名单
    """
    
    def __init__(self):
        self.allowed_dirs: List[Path] = [
            WORKSPACE_DIR.resolve(),
            Path.home() / ".miniclaw" / "workspace",
            Path.home() / "Desktop",
            Path.home() / "Documents",
        ]
        self.blocked_dirs: List[Path] = [
            Path("/etc"),
            Path("/sys"),
            Path("C:\\Windows"),
            Path("C:\\Program Files"),
        ]
        self.allowed_domains: List[str] = ["*"]  # 改为具体域名列表可限制浏览器访问
        self.require_approval_risk_levels: List[str] = ["high"]
    
    def is_path_allowed(self, path: Path) -> bool:
        """检查路径是否在允许范围内"""
        resolved = path.resolve()
        # 先检查黑名单
        for blocked in self.blocked_dirs:
            try:
                resolved.relative_to(blocked)
                return False
            except ValueError:
                pass
        # 再检查白名单
        for allowed in self.allowed_dirs:
            try:
                resolved.relative_to(allowed)
                return True
            except ValueError:
                pass
        return False
    
    def requires_approval(self, tool: ToolDefinition, arguments: Dict) -> bool:
        """判断是否需要用户审批"""
        if tool.require_approval:
            return True
        if tool.risk_level in self.require_approval_risk_levels:
            return True
        return False


# 全局单例
security_policy = SecurityPolicy()