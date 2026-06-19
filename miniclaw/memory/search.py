"""
memory/search.py — 记忆检索（关键词 + 语义向量 + TTL 缓存）
"""
import re
import time
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Optional
from ..config import WORKSPACE_DIR
from ..logger import get_logger

logger = get_logger(__name__)

# ── TTL 缓存 ────────────────────────────────────────────
_CACHE_TTL_SEC = 30.0  # 30 秒缓存

@dataclass
class _CacheEntry:
    results: List[Tuple[Path, float, str]]
    timestamp: float = field(default_factory=time.time)
    fingerprint: Tuple[Any, ...] = ()

    def is_fresh(self) -> bool:
        return (time.time() - self.timestamp) < _CACHE_TTL_SEC

_cache: Dict[str, _CacheEntry] = {}
_cache_lock = threading.Lock()


def _memory_fingerprint() -> Tuple[Any, ...]:
    """基于记忆文件 mtime/size 生成指纹，变更时缓存失效。"""
    memory_dir = WORKSPACE_DIR / "memory"
    parts: List[Any] = []
    mem = WORKSPACE_DIR / "MEMORY.md"
    if mem.exists():
        st = mem.stat()
        parts.append(("MEMORY.md", st.st_mtime_ns, st.st_size))
    if memory_dir.exists():
        for f in sorted(memory_dir.glob("*.md")):
            st = f.stat()
            parts.append((f.name, st.st_mtime_ns, st.st_size))
    return tuple(parts)


def _get_cached(query: str) -> Optional[List[Tuple[Path, float, str]]]:
    fp = _memory_fingerprint()
    with _cache_lock:
        entry = _cache.get(query)
        if entry and entry.is_fresh() and entry.fingerprint == fp:
            return entry.results
    return None


def _set_cache(query: str, results: List[Tuple[Path, float, str]]) -> None:
    fp = _memory_fingerprint()
    with _cache_lock:
        _cache[query] = _CacheEntry(results=results, fingerprint=fp)
        # 防止缓存无限增长：保留最近 256 条
        while len(_cache) > 256:
            oldest = min(_cache, key=lambda k: _cache[k].timestamp)
            del _cache[oldest]


def keyword_search(query: str, max_results: int = 10) -> List[Tuple[Path, float, str]]:
    """
    简单关键词搜索 MEMORY.md + memory/*.md（带 TTL 缓存）
    返回: [(文件路径, 相关度分数, 匹配内容), ...]
    """
    cached = _get_cached(query)
    if cached is not None:
        return cached[:max_results]

    memory_dir = WORKSPACE_DIR / "memory"
    files = [WORKSPACE_DIR / "MEMORY.md"]
    if memory_dir.exists():
        files.extend(memory_dir.glob("*.md"))

    results = []
    query_lower = query.lower()
    query_terms = re.findall(r'\w+', query_lower)

    for filepath in files:
        if not filepath.exists():
            continue
        content = filepath.read_text(encoding="utf-8")

        # 计算简单相关度分数
        score = 0
        content_lower = content.lower()
        for term in query_terms:
            score += content_lower.count(term) * len(term)

        if score > 0:
            lines = content.split("\n")
            matched_lines = []
            for line in lines:
                if any(term in line.lower() for term in query_terms):
                    matched_lines.append(line)
            snippet = "\n".join(matched_lines[:10])
            results.append((filepath, score, snippet))

    results.sort(key=lambda x: x[1], reverse=True)
    final = results[:max_results]
    _set_cache(query, final)
    return final


# 可选的语义搜索（需要 sentence-transformers）
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    
    _embedder = None
    
    def _get_embedder():
        global _embedder
        if _embedder is None:
            # all-MiniLM-L6-v2 是 sentence-transformers 库中的一个模型，用于将文本转换为向量
            # 这是一个轻量级的模型，适用于小规模文本处理，但是主要是针对英文的，中文需要后期再改
            _embedder = SentenceTransformer("all-MiniLM-L6-v2")
        return _embedder
    
    def semantic_search(query: str, max_results: int = 5) -> List[Tuple[Path, float, str]]:
        """语义向量搜索"""
        model = _get_embedder()
        query_vec = model.encode([query])[0]
        
        # 遍历所有记忆文件
        memory_dir = WORKSPACE_DIR / "memory"
        files = [WORKSPACE_DIR / "MEMORY.md"]
        if memory_dir.exists():
            files.extend(memory_dir.glob("*.md"))
        
        results = []
        for filepath in files:
            if not filepath.exists():
                continue
            content = filepath.read_text(encoding="utf-8")
            # 将文件分段
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            for para in paragraphs:
                if len(para) < 20:
                    continue
                para_vec = model.encode([para])[0]
                similarity = float(np.dot(query_vec, para_vec) / 
                                  (np.linalg.norm(query_vec) * np.linalg.norm(para_vec)))
                if similarity > 0.3:
                    results.append((filepath, similarity, para))
        
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:max_results]
except ImportError:
    semantic_search = None
    logger.warning("sentence-transformers not installed; semantic search disabled")