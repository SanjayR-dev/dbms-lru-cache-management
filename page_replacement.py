"""
Page replacement policies: LRU, FIFO (FCFS), and Optimal (Belady).
"""

from __future__ import annotations

from collections import deque
from typing import List, Literal, Optional, Union

from buffer_pool import LRUBufferPool, AccessResult, BufferPoolStats

ReplacementPolicy = Literal["lru", "fifo", "optimal"]


class FIFOBufferPool:
    """First-In First-Out (FCFS) — evict the page loaded earliest."""

    def __init__(self, num_frames: int) -> None:
        if num_frames < 1:
            raise ValueError("num_frames must be at least 1")
        self.num_frames = num_frames
        self._queue: deque[int] = deque()
        self._resident: set[int] = set()
        self.stats = BufferPoolStats()

    def access(self, page_id: int) -> AccessResult:
        self.stats.references += 1
        if page_id in self._resident:
            self.stats.hits += 1
            return AccessResult(page_id=page_id, hit=True, evicted_page_id=None)

        self.stats.misses += 1
        evicted: Optional[int] = None
        if len(self._resident) >= self.num_frames:
            evicted = self._queue.popleft()
            self._resident.remove(evicted)
            self.stats.evictions += 1

        self._queue.append(page_id)
        self._resident.add(page_id)
        return AccessResult(page_id=page_id, hit=False, evicted_page_id=evicted)

    def frame_contents(self) -> List[int]:
        """Oldest (next to evict) → newest."""
        return list(self._queue)


class OptimalBufferPool:
    """Belady optimal — evict page whose next use is farthest in the future."""

    def __init__(self, num_frames: int, reference_string: List[int]) -> None:
        if num_frames < 1:
            raise ValueError("num_frames must be at least 1")
        self.num_frames = num_frames
        self._refs = reference_string
        self._step = 0
        self._frames: List[int] = []
        self.stats = BufferPoolStats()

    def _next_use_index(self, page_id: int, after: int) -> int:
        n = len(self._refs)
        try:
            return self._refs.index(page_id, after + 1)
        except ValueError:
            return n + 1

    def access(self, page_id: int) -> AccessResult:
        self.stats.references += 1
        i = self._step
        self._step += 1

        if page_id in self._frames:
            self.stats.hits += 1
            return AccessResult(page_id=page_id, hit=True, evicted_page_id=None)

        self.stats.misses += 1
        evicted: Optional[int] = None
        if len(self._frames) >= self.num_frames:
            victim = max(self._frames, key=lambda p: self._next_use_index(p, i))
            evicted = victim
            self._frames.remove(victim)
            self.stats.evictions += 1

        self._frames.append(page_id)
        return AccessResult(page_id=page_id, hit=False, evicted_page_id=evicted)

    def frame_contents(self) -> List[int]:
        return list(self._frames)


def create_buffer_pool(
    policy: ReplacementPolicy,
    num_frames: int,
    reference_string: Optional[List[int]] = None,
) -> Union[LRUBufferPool, FIFOBufferPool, OptimalBufferPool]:
    if policy == "lru":
        return LRUBufferPool(num_frames)
    if policy == "fifo":
        return FIFOBufferPool(num_frames)
    if policy == "optimal":
        if reference_string is None:
            raise ValueError("Optimal policy requires the full reference string")
        return OptimalBufferPool(num_frames, reference_string)
    raise ValueError(f"Unknown policy: {policy}")


def frame_contents(pool: object) -> List[int]:
    if hasattr(pool, "frame_contents_mru_to_lru"):
        return pool.frame_contents_mru_to_lru()  # type: ignore[attr-defined]
    return pool.frame_contents()  # type: ignore[attr-defined]
