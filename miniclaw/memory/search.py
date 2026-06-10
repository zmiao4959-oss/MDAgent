"""
memory/search.py — 记忆检索（关键词 + 语义向量）
"""
import re
from pathlib import Path
from typing import List, Tuple
from ..config import WORKSPACE_DIR
from ..logger import get_logger

logger = get_logger(__name__)


def keyword_search(query: str, max_results: int = 10) -> List[Tuple[Path, float, str]]:
    """
    简单关键词搜索 MEMORY.md + memory/*.md
    返回: [(文件路径, 相关度分数, 匹配内容), ...]
    """
    memory_dir = WORKSPACE_DIR / "memory"
    files = [WORKSPACE_DIR / "MEMORY.md"]
    if memory_dir.exists():
        files.extend(memory_dir.glob("*.md"))
    
    results = []
    # lower 小写
    query_lower = query.lower()
    # r'\w+'：正则表达式，\w 匹配字母、数字、下划线，+ 表示至少一个 
    # 因此可以用于英文分词 ， 中文分词需要后期再改
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
            # 提取相关段落
            lines = content.split("\n")
            matched_lines = []
            for line in lines:
                if any(term in line.lower() for term in query_terms):
                    matched_lines.append(line)
            snippet = "\n".join(matched_lines[:10])
            results.append((filepath, score, snippet))
    
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:max_results]


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