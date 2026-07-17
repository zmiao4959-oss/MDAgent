"""Persistent, structured traces used by the self-evolution pipeline."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ToolTrace:
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    result_preview: str = ""
    error: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class EvolutionTrace:
    chat_id: str
    objective: str
    channel: str = ""
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0
    success: Optional[bool] = None
    score: float = 0.0
    rounds: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    hit_max_rounds: bool = False
    final_response: str = ""
    tool_calls: List[ToolTrace] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]],
        result: Any,
    ) -> None:
        result_text = str(result)
        failed = result_text.lstrip().lower().startswith(("error", "[error"))
        self.tool_calls.append(
            ToolTrace(
                name=name,
                arguments=_json_safe(arguments or {}),
                success=not failed,
                result_preview=result_text[:500],
                error=result_text[:500] if failed else "",
            )
        )
        if failed:
            self.errors.append(result_text[:500])

    def finish(
        self,
        *,
        success: bool,
        final_response: str = "",
        rounds: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        hit_max_rounds: bool = False,
        error: str = "",
    ) -> None:
        self.ended_at = time.time()
        self.success = success
        self.final_response = final_response[:8000]
        self.rounds = rounds
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.hit_max_rounds = hit_max_rounds
        if error:
            self.errors.append(error[:1000])
        self.score = self._quality_score()

    def _quality_score(self) -> float:
        if self.success is None:
            return 0.0
        score = 1.0 if self.success else 0.0
        if self.hit_max_rounds:
            score -= 0.2
        score -= min(0.4, 0.1 * len(self.errors))
        if not self.final_response.strip():
            score -= 0.2
        return round(max(0.0, min(1.0, score)), 3)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvolutionTrace":
        payload = dict(data)
        payload["tool_calls"] = [ToolTrace(**item) for item in payload.get("tool_calls", [])]
        return cls(**payload)


class TraceStore:
    """Append-only JSONL trace store; corrupt lines are skipped on reads."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, trace: EvolutionTrace) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(trace.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def recent(self, limit: int = 100) -> List[EvolutionTrace]:
        if limit <= 0 or not self.path.exists():
            return []
        traces: List[EvolutionTrace] = []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        for line in lines[-limit:]:
            try:
                traces.append(EvolutionTrace.from_dict(json.loads(line)))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return traces

    def prune_before(self, cutoff_timestamp: float) -> int:
        if not self.path.exists():
            return 0
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            kept = []
            removed = 0
            for line in lines:
                try:
                    data = json.loads(line)
                    timestamp = float(data.get("ended_at") or data.get("started_at") or 0)
                except (ValueError, TypeError, json.JSONDecodeError):
                    kept.append(line)
                    continue
                if timestamp and timestamp < cutoff_timestamp:
                    removed += 1
                else:
                    kept.append(line)
            if removed:
                temporary = self.path.with_suffix(self.path.suffix + ".tmp")
                temporary.write_text(
                    "\n".join(kept) + ("\n" if kept else ""), encoding="utf-8"
                )
                os.replace(temporary, self.path)
            return removed


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return repr(value)
