"""
memory/evolution.py — 进化记忆系统

借鉴 LoongFlow / Letta 的记忆模式：
- 分层整合：session → daily → permanent
- 矛盾检测：新事实与旧记忆冲突时自动标记
- 记忆衰减：长期未引用的记忆权重降低
- 混合检索：关键词 + 语义向量
"""
from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..settings import WORKSPACE_DIR, MEMORY_FILE
from ..logger import get_logger

logger = get_logger(__name__)

# ── 记忆条目 ────────────────────────────────────────────────────

class MemoryEntry:
    """单条记忆。"""
    __slots__ = (
        "text", "category", "timestamp", "weight",
        "references",  # 被引用次数
        "contradicts",  # 与此冲突的记忆 ID 列表
        "source",       # "session" | "daily" | "permanent"
        "key",          # 归一化后的关键词签名
        "last_decay_at",
    )

    def __init__(
        self,
        text: str,
        category: str = "",
        timestamp: Optional[float] = None,
        weight: float = 1.0,
        source: str = "session",
    ):
        self.text = text.strip()
        self.category = category
        self.timestamp = timestamp or time.time()
        self.weight = weight
        self.references = 0
        self.contradicts: List[str] = []
        self.source = source
        self.key = self._compute_key(text)
        self.last_decay_at = self.timestamp

    @staticmethod
    def _compute_key(text: str) -> str:
        """生成归一化关键词签名用于快速匹配。"""
        # 取前几个有意义的词根
        words = re.findall(r'[一-鿿]+|[a-zA-Z]{3,}', text.lower())
        return " ".join(sorted(set(words))[:8])

    def to_markdown(self) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))
        cat = f" *[{self.category}]*" if self.category else ""
        return f"## {ts}{cat}\n\n{self.text}\n"

    def age_days(self) -> float:
        return (time.time() - self.timestamp) / 86400.0


# ── 进化记忆管理器 ──────────────────────────────────────────────

class EvolutionaryMemory:
    """管理记忆的生命周期：创建 → 巩固 → 衰减 → 淘汰。"""

    HALF_LIFE_DAYS = 30.0       # 记忆半衰期（未引用时权重减半的时间）
    MIN_WEIGHT = 0.1            # 低于此权重自动淘汰
    CONSOLIDATION_DAYS = 1.0    # session 记忆在此时间后升级到 daily
    PERMANENT_THRESHOLD = 3     # 被引用 >= N 次的记忆升级为 permanent
    SIMILARITY_THRESHOLD = 0.6  # 关键词重叠率视为"相似"

    def __init__(self):
        self._entries: List[MemoryEntry] = []
        self._by_key: Dict[str, List[int]] = defaultdict(list)  # key → [index, ...]
        self._loaded = False

    # ── CRUD ──

    def add(self, text: str, category: str = "", source: str = "session") -> MemoryEntry:
        """添加新记忆，检测与已有记忆的矛盾。"""
        # 去重：完全相同的文本
        for e in self._entries:
            if e.text == text.strip():
                e.references += 1
                e.timestamp = time.time()
                return e

        entry = MemoryEntry(text, category=category, source=source)

        # 矛盾检测
        similar = self._find_similar(entry)
        for idx in similar:
            existing = self._entries[idx]
            if self._is_contradiction(entry.text, existing.text):
                entry.contradicts.append(existing.text[:60])
                existing.weight *= 0.5  # 旧记忆降权
                logger.info("Memory contradiction detected: new='%s...' vs old='%s...'",
                            entry.text[:40], existing.text[:40])

        self._entries.append(entry)
        self._by_key[entry.key].append(len(self._entries) - 1)

        logger.debug("Memory added: '%s...' (source=%s, category=%s)",
                      entry.text[:60], source, category)
        return entry

    def recall(self, query: str, max_results: int = 5) -> List[MemoryEntry]:
        """混合检索：关键词匹配 + 时间衰减排序。"""
        self._apply_decay()
        query_key = MemoryEntry._compute_key(query)
        query_words = set(query_key.split())

        scored: List[Tuple[float, MemoryEntry]] = []
        seen = set()

        for entry in self._entries:
            if id(entry) in seen:
                continue
            seen.add(id(entry))

            entry_words = set(entry.key.split())
            if not query_words or not entry_words:
                continue

            # Jaccard 相似度 × 权重
            overlap = len(query_words & entry_words)
            union = len(query_words | entry_words)
            similarity = overlap / union if union > 0 else 0

            if similarity > 0:
                score = similarity * entry.weight
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        recalled = [e for _, e in scored[:max_results]]
        for entry in recalled:
            entry.references += 1
        return recalled

    def consolidate(self) -> int:
        """分层整合：session → daily → permanent。返回升级的条目数。"""
        self._apply_decay()
        upgraded = 0

        for entry in self._entries:
            if entry.source == "session" and entry.age_days() >= self.CONSOLIDATION_DAYS:
                entry.source = "daily"
                upgraded += 1
            elif entry.source == "daily" and entry.references >= self.PERMANENT_THRESHOLD:
                entry.source = "permanent"
                entry.weight = max(entry.weight, 2.0)  # 永久记忆保底权重
                upgraded += 1

        if upgraded:
            logger.info("Memory consolidation: %s entries upgraded", upgraded)
        return upgraded

    def prune(self) -> int:
        """淘汰低于阈值的记忆。返回删除数。"""
        self._apply_decay()
        before = len(self._entries)
        # 永久记忆永不淘汰
        self._entries = [
            e for e in self._entries
            if e.weight >= self.MIN_WEIGHT or e.source == "permanent"
        ]
        # 重建索引
        self._by_key.clear()
        for i, e in enumerate(self._entries):
            self._by_key[e.key].append(i)
        removed = before - len(self._entries)
        if removed:
            logger.info("Memory pruned: %s entries removed", removed)
        return removed

    def stats(self) -> Dict:
        sources = defaultdict(int)
        total_weight = 0.0
        for e in self._entries:
            sources[e.source] += 1
            total_weight += e.weight
        return {
            "total": len(self._entries),
            "by_source": dict(sources),
            "total_weight": round(total_weight, 2),
            "avg_age_days": round(sum(e.age_days() for e in self._entries) / max(len(self._entries), 1), 1),
        }

    # ── 内部方法 ──

    def _apply_decay(self) -> None:
        """应用时间衰减到所有非 permanent 记忆。"""
        now = time.time()
        for e in self._entries:
            if e.source == "permanent":
                continue
            days = max(0.0, (now - e.last_decay_at) / 86400.0)
            e.weight = max(
                self.MIN_WEIGHT,
                e.weight * math.exp(-math.log(2) * days / self.HALF_LIFE_DAYS),
            )
            e.last_decay_at = now

    def _find_similar(self, entry: MemoryEntry) -> List[int]:
        """找到与给定记忆相似（可能冲突）的已有记忆索引。"""
        similar = []
        query_words = set(entry.key.split())
        for key, indices in self._by_key.items():
            key_words = set(key.split())
            if not query_words or not key_words:
                continue
            overlap = len(query_words & key_words)
            union = len(query_words | key_words)
            if union > 0 and overlap / union >= self.SIMILARITY_THRESHOLD:
                similar.extend(indices)
        return similar

    @staticmethod
    def _is_contradiction(text_a: str, text_b: str) -> bool:
        """简单规则检测：如果两段文本以否定/相反形式描述同一概念，判定为矛盾。"""
        neg_patterns = [
            r'\b(?:no|not|never|don\'t|doesn\'t|won\'t|cannot|can\'t|stop|quit|abandon)\b',
            r'(?:错误|不对|取消|放弃|不再|不要|停止)',
        ]
        has_neg_a = any(re.search(p, text_a, re.IGNORECASE) for p in neg_patterns)
        has_neg_b = any(re.search(p, text_b, re.IGNORECASE) for p in neg_patterns)
        # 一方有否定词，另一方没有 → 可能矛盾
        return has_neg_a != has_neg_b

    # ── 持久化 ──

    def save_to_memory_file(self) -> None:
        """将 permanent 记忆写回 MEMORY.md。"""
        permanent = [e for e in self._entries if e.source == "permanent"]
        if not permanent:
            return

        path = WORKSPACE_DIR / MEMORY_FILE
        header = (
            "# MiniClaw Memory\n\n"
            "This file stores facts the agent chooses to remember across sessions.\n"
            "Entries are managed by the evolutionary memory system.\n\n"
        )
        lines = [header]
        for e in sorted(permanent, key=lambda x: -x.weight):
            lines.append(e.to_markdown())

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Saved %s permanent memories to %s", len(permanent), MEMORY_FILE)

    def load_from_memory_file(self) -> int:
        """从 MEMORY.md 加载已有记忆。返回加载的条目数。"""
        path = WORKSPACE_DIR / MEMORY_FILE
        if not path.exists():
            return 0

        text = path.read_text(encoding="utf-8")
        # 解析 ## timestamp 分隔的记忆条目
        entries = re.split(r'\n## \d{4}-\d{2}-\d{2} \d{2}:\d{2}', text)
        loaded = 0
        for entry_text in entries:
            entry_text = entry_text.strip()
            if not entry_text or entry_text.startswith("#"):
                continue
            # 移除 category 标记
            entry_text = re.sub(r'^\s*\*\[.*?\]\*\s*', '', entry_text)
            if len(entry_text) > 10:
                m = MemoryEntry(entry_text, source="permanent")
                self._entries.append(m)
                self._by_key[m.key].append(len(self._entries) - 1)
                loaded += 1

        self._loaded = True
        logger.info("Loaded %s memories from %s", loaded, MEMORY_FILE)
        return loaded


# ── 模块级实例 ──
evo_memory = EvolutionaryMemory()
