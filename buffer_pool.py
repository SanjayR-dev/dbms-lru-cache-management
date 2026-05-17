"""
LRU buffer pool simulation (DBMS-style).

Models a fixed set of memory frames holding disk pages. On a miss, a cold page
is loaded; if the pool is full, the least recently used frame is evicted.
Uses a hash map + doubly linked list for O(1) get/update per access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple


@dataclass
class _Node:
    page_id: int
    prev: Optional["_Node"] = None
    next: Optional["_Node"] = None


@dataclass
class AccessResult:
    """Outcome of one logical page reference."""

    page_id: int
    hit: bool
    evicted_page_id: Optional[int] = None


@dataclass
class BufferPoolStats:
    references: int = 0
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def hit_ratio(self) -> float:
        if self.references == 0:
            return 0.0
        return self.hits / self.references


class LRUBufferPool:
    """
    Fixed-size buffer pool with LRU replacement.

    Parameters
    ----------
    num_frames : int
        Number of page frames (pool capacity). Must be >= 1.
    """

    def __init__(self, num_frames: int) -> None:
        if num_frames < 1:
            raise ValueError("num_frames must be at least 1")
        self.num_frames = num_frames
        self._map: Dict[int, _Node] = {}
        self._head = _Node(page_id=-1)  # sentinel (most recent side)
        self._tail = _Node(page_id=-1)  # sentinel (LRU side)
        self._head.next = self._tail
        self._tail.prev = self._head
        self.stats = BufferPoolStats()

    def _unlink(self, node: _Node) -> None:
        assert node.prev and node.next
        node.prev.next = node.next
        node.next.prev = node.prev

    def _link_front(self, node: _Node) -> None:
        """Insert right after head (most recently used)."""
        assert self._head.next
        nxt = self._head.next
        node.prev = self._head
        node.next = nxt
        self._head.next = node
        nxt.prev = node

    def _lru_node(self) -> _Node:
        """Node just before tail is LRU."""
        assert self._tail.prev and self._tail.prev is not self._head
        return self._tail.prev

    def access(self, page_id: int) -> AccessResult:
        """
        Reference a page: buffer hit if already resident, else miss (load/evict).
        """
        self.stats.references += 1
        evicted: Optional[int] = None

        if page_id in self._map:
            self.stats.hits += 1
            node = self._map[page_id]
            self._unlink(node)
            self._link_front(node)
            return AccessResult(page_id=page_id, hit=True, evicted_page_id=None)

        self.stats.misses += 1
        if len(self._map) >= self.num_frames:
            lru = self._lru_node()
            evicted = lru.page_id
            self.stats.evictions += 1
            self._unlink(lru)
            del self._map[lru.page_id]

        node = _Node(page_id=page_id)
        self._map[page_id] = node
        self._link_front(node)
        return AccessResult(page_id=page_id, hit=False, evicted_page_id=evicted)

    def frame_contents_mru_to_lru(self) -> List[int]:
        """Pages from most recently used to LRU (excluding sentinels)."""
        out: List[int] = []
        cur = self._head.next
        while cur and cur is not self._tail:
            out.append(cur.page_id)
            cur = cur.next
        return out

    def reset(self) -> None:
        self._map.clear()
        self._head.next = self._tail
        self._tail.prev = self._head
        self.stats = BufferPoolStats()


def run_reference_string(
    num_frames: int, pages: List[int]
) -> Tuple[LRUBufferPool, List[AccessResult]]:
    """Apply a sequence of page references; return pool and per-step results."""
    pool = LRUBufferPool(num_frames)
    results: List[AccessResult] = []
    for p in pages:
        results.append(pool.access(p))
    return pool, results


def optimal_hit_ratio(num_frames: int, pages: List[int]) -> float:
    """Optimal policy hit ratio (same as running OptimalBufferPool)."""
    from page_replacement import OptimalBufferPool

    pool = OptimalBufferPool(num_frames, pages)
    for p in pages:
        pool.access(p)
    return pool.stats.hit_ratio
