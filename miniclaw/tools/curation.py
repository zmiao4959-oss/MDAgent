"""
tools/curation.py — 工具策展系统 (Tool Curation)

借鉴 Hermes 的「注册 vs 暴露」分离模式：
- 工具全局注册，但按场景选择性暴露给 LLM
- 减少 token 消耗（只暴露必需工具）
- 预定义场景工具集：coding, research, system, minimal
"""
from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Set

from .registry import tool_registry, ToolRegistry
from ..logger import get_logger

logger = get_logger(__name__)

# ── 场景工具集定义 ──────────────────────────────────────────────

TOOLSETS: Dict[str, FrozenSet[str]] = {
    "full": frozenset({}),  # 空集 = 全部可见
    "minimal": frozenset({
        "read", "write", "list", "execute", "send_message",
    }),
    "coding": frozenset({
        "read", "write", "list", "grep", "execute",
        "web_search", "remember", "recall",
    }),
    "research": frozenset({
        "read", "list", "grep", "web_search",
        "browse", "remember", "recall",
    }),
    "system": frozenset({
        "execute", "read", "list",
        "send_message", "remember",
    }),
    "file_ops": frozenset({
        "read", "write", "list", "grep",
    }),
    "browser": frozenset({
        "browse", "read", "web_search",
    }),
}


class ToolCurator:
    """管理工具的暴露/隐藏策略。"""

    def __init__(self, registry: Optional[ToolRegistry] = None):
        self.registry = registry or tool_registry
        self._hidden: Set[str] = set()
        self._active_set: Optional[str] = None

    # ── 工具集操作 ──

    def apply_toolset(self, name: str) -> List[str]:
        """应用预定义工具集，返回隐藏的工具名列表。"""
        toolset = TOOLSETS.get(name)
        if toolset is None:
            logger.warning("Unknown toolset '%s', available: %s", name, list(TOOLSETS.keys()))
            return []

        self._active_set = name
        if not toolset:  # "full" — 显示所有
            self._hidden.clear()
            return []

        all_tools = set(self.registry.tool_names())
        self._hidden = all_tools - toolset
        return sorted(self._hidden)

    def hide(self, tool_name: str) -> None:
        """隐藏单个工具。"""
        self._hidden.add(tool_name)

    def expose(self, tool_name: str) -> None:
        """取消隐藏单个工具。"""
        self._hidden.discard(tool_name)

    def hide_all(self) -> None:
        """隐藏所有工具（极简模式）。"""
        self._hidden = set(self.registry.tool_names())

    def reset(self) -> None:
        """恢复显示所有工具。"""
        self._hidden.clear()
        self._active_set = None

    # ── 查询 ──

    @property
    def visible_tools(self) -> List[str]:
        """返回当前可见的工具名列表。"""
        all_tools = set(self.registry.tool_names())
        return sorted(all_tools - self._hidden)

    @property
    def hidden_tools(self) -> List[str]:
        return sorted(self._hidden)

    @property
    def active_set(self) -> Optional[str]:
        return self._active_set

    def is_visible(self, tool_name: str) -> bool:
        return tool_name in self.registry.tool_names() and tool_name not in self._hidden

    def list_for_llm(self) -> List[Dict]:
        """生成仅包含可见工具的 LLM 工具定义。"""
        all_defs = self.registry.list_for_llm()
        if not self._hidden:
            return all_defs
        visible = self.visible_tools
        return [d for d in all_defs
                if (d.get("function", {}).get("name") or d.get("name", "")) in visible]

    @staticmethod
    def list_toolsets() -> Dict[str, List[str]]:
        """列出所有预定义工具集及其包含的工具。"""
        return {
            name: sorted(tools) if tools else ["*all*"]
            for name, tools in TOOLSETS.items()
        }


# ── 模块级便捷实例 ──
tool_curator = ToolCurator()
