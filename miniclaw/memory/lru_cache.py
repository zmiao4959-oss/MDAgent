"""
memory/lru_cache.py — 带 TTL 的 LRU 缓存实现

相比 memory/search.py 中简单 dict 缓存的改进：
  - 真正 LRU 淘汰（O(1) 双向链表 + 哈希表）
  - 可配置 TTL（默认 30s）
  - 最大容量限制（默认 256）
  - 指纹感知失效（文件变更自动淘汰）
  - 线程安全
  - 缓存命中率统计
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, Optional, Tuple, TypeVar

T = TypeVar("T")


@dataclass
class _CacheNode(Generic[T]):
    key: str
    value: T
    expiry: float
    fingerprint: Tuple[Any, ...] = ()
    prev: Optional["_CacheNode"] = None
    next: Optional["_CacheNode"] = None


class LRUCache(Generic[T]):
    """线程安全的 LRU + TTL 缓存"""

    def __init__(self, max_size: int = 256, ttl_sec: float = 30.0):
        self.max_size = max_size
        self.ttl_sec = ttl_sec
        self._lock = threading.Lock()
        self._map: Dict[str, _CacheNode[T]] = {}
        # 双向链表哨兵
        self._head: _CacheNode = _CacheNode(key="__head__", value=None, expiry=0)  # type: ignore
        self._tail: _CacheNode = _CacheNode(key="__tail__", value=None, expiry=0)  # type: ignore
        self._head.next = self._tail
        self._tail.prev = self._head
        # 统计
        self.hits: int = 0
        self.misses: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._map)

    def _remove_node(self, node: _CacheNode) -> None:
        """从链表中移除节点"""
        if node.prev:
            node.prev.next = node.next
        if node.next:
            node.next.prev = node.prev

    def _add_to_head(self, node: _CacheNode) -> None:
        """将节点插入链表头部（最近使用）"""
        node.next = self._head.next
        node.prev = self._head
        if self._head.next:
            self._head.next.prev = node
        self._head.next = node

    def _evict_lru(self) -> None:
        """淘汰最久未使用的节点"""
        lru = self._tail.prev
        if lru and lru is not self._head:
            self._remove_node(lru)
            self._map.pop(lru.key, None)

    def _evict_expired(self) -> int:
        """清理过期节点，返回清理数量"""
        now = time.time()
        evicted = 0
        node = self._tail.prev
        while node and node is not self._head:
            if node.expiry < now:
                next_node = node.prev
                self._remove_node(node)
                self._map.pop(node.key, None)
                evicted += 1
                node = next_node
            else:
                break
        return evicted

    def get(self, key: str, fingerprint: Tuple[Any, ...] = ()) -> Optional[T]:
        """获取缓存值。fingerprint 不匹配时视为 miss。"""
        with self._lock:
            node = self._map.get(key)
            if node is None:
                self.misses += 1
                return None
            # TTL 检查
            if time.time() > node.expiry:
                self._remove_node(node)
                self._map.pop(key, None)
                self.misses += 1
                return None
            # 指纹检查
            if fingerprint and node.fingerprint and node.fingerprint != fingerprint:
                self._remove_node(node)
                self._map.pop(key, None)
                self.misses += 1
                return None
            # 移到头部 (标记为最近使用)
            self._remove_node(node)
            self._add_to_head(node)
            self.hits += 1
            return node.value

    def set(self, key: str, value: T, fingerprint: Tuple[Any, ...] = ()) -> None:
        """设置缓存值"""
        with self._lock:
            # 键已存在 → 更新
            if key in self._map:
                node = self._map[key]
                self._remove_node(node)

            node = _CacheNode(
                key=key,
                value=value,
                expiry=time.time() + self.ttl_sec,
                fingerprint=fingerprint,
            )
            self._map[key] = node
            self._add_to_head(node)

            # 容量控制
            while len(self._map) > self.max_size:
                self._evict_lru()

            # 顺便清理过期节点
            self._evict_expired()

    def invalidate(self, key: str) -> bool:
        """手动失效某个缓存项"""
        with self._lock:
            node = self._map.pop(key, None)
            if node:
                self._remove_node(node)
                return True
            return False

    def clear(self) -> None:
        """清空缓存"""
        with self._lock:
            self._map.clear()
            self._head.next = self._tail
            self._tail.prev = self._head
            self.hits = 0
            self.misses = 0

    def stats(self) -> Dict[str, Any]:
        """返回缓存统计信息"""
        with self._lock:
            return {
                "size": len(self._map),
                "max_size": self.max_size,
                "ttl_sec": self.ttl_sec,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hit_rate, 3),
            }
