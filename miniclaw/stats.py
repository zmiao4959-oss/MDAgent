"""
stats.py — Agent 运行统计追踪器

通过 hooks 自动收集性能数据：
  - 总运行次数、平均耗时
  - Token 用量趋势
  - 工具调用频率
  - 错误率
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class RunStats:
    """单次运行统计"""
    chat_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    rounds: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tools_called: List[str] = field(default_factory=list)
    error: Optional[str] = None
    success: Optional[bool] = None
    quality_score: float = 0.0
    tool_errors: int = 0
    hit_max_rounds: bool = False

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000 if self.end_time > 0 else 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class AgentStatsTracker:
    """全局 Agent 运行统计"""

    _instance: Optional["AgentStatsTracker"] = None

    def __init__(self):
        self._lock = threading.Lock()
        self.total_runs: int = 0
        self.total_errors: int = 0
        self.total_tokens: int = 0
        self.total_tool_calls: int = 0
        self.recent_runs: List[RunStats] = []  # 最近 100 次
        self._tool_freq: Dict[str, int] = {}

    @classmethod
    def get_instance(cls) -> "AgentStatsTracker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_run(self, chat_id: str) -> RunStats:
        run = RunStats(chat_id=chat_id, start_time=time.time())
        return run

    def end_run(self, run: RunStats):
        run.end_time = time.time()
        with self._lock:
            self.total_runs += 1
            self.total_tokens += run.total_tokens
            self.total_tool_calls += len(run.tools_called)
            if run.error:
                self.total_errors += 1
            for tool in run.tools_called:
                self._tool_freq[tool] = self._tool_freq.get(tool, 0) + 1
            self.recent_runs.append(run)
            # 只保留最近 100 次
            if len(self.recent_runs) > 100:
                self.recent_runs = self.recent_runs[-100:]

    def summary(self) -> Dict[str, Any]:
        """返回统计摘要"""
        with self._lock:
            avg_duration_ms = 0.0
            if self.recent_runs:
                avg_duration_ms = sum(r.duration_ms for r in self.recent_runs) / len(self.recent_runs)

            # 最常用的 10 个工具
            top_tools = sorted(self._tool_freq.items(), key=lambda x: x[1], reverse=True)[:10]

            return {
                "total_runs": self.total_runs,
                "total_errors": self.total_errors,
                "error_rate": round(self.total_errors / max(1, self.total_runs), 3),
                "total_tokens": self.total_tokens,
                "total_tool_calls": self.total_tool_calls,
                "avg_duration_ms": round(avg_duration_ms, 1),
                "recent_run_count": len(self.recent_runs),
                "top_tools": [{"name": n, "count": c} for n, c in top_tools],
                "success_rate": round(
                    sum(1 for r in self.recent_runs if r.success)
                    / max(1, sum(1 for r in self.recent_runs if r.success is not None)),
                    3,
                ),
                "avg_quality_score": round(
                    sum(r.quality_score for r in self.recent_runs) / max(1, len(self.recent_runs)),
                    3,
                ),
            }

    def reset(self):
        """重置统计"""
        with self._lock:
            self.total_runs = 0
            self.total_errors = 0
            self.total_tokens = 0
            self.total_tool_calls = 0
            self.recent_runs.clear()
            self._tool_freq.clear()


# 全局单例
agent_stats = AgentStatsTracker.get_instance()
