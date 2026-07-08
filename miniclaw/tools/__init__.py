"""
tools 包 — 统一加载所有内置工具模块（副作用：注册到 tool_registry）
"""
from .registry import tool_registry, ToolDefinition, ToolRegistry

_LOADED = False


def ensure_tools_loaded() -> None:
    """导入各工具模块以完成 @tool_registry.register 注册。"""
    global _LOADED
    if _LOADED:
        return
    from . import file_tools  # noqa: F401
    from . import exec_tool  # noqa: F401
    from . import message_tool  # noqa: F401
    from . import web_search  # noqa: F401
    from . import memory_tool  # noqa: F401
    from . import system_tool  # noqa: F401
    from . import skill_tool  # noqa: F401
    try:
        from . import browser_tool  # noqa: F401
    except ImportError:
        pass
    _LOADED = True


__all__ = [
    "tool_registry",
    "ToolDefinition",
    "ToolRegistry",
    "ensure_tools_loaded",
]
