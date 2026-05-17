"""
Buffer pool integrated with on-disk pages (MongoDB via DiskManager).

Write-back: dirty pages are flushed to disk only when evicted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from buffer_pool import LRUBufferPool, _Node
from disk_manager import DiskManager


@dataclass
class DbAccessResult:
    page_id: int
    operation: str
    hit: bool
    evicted_page_id: Optional[int] = None
    disk_read: bool = False
    disk_write: bool = False
    dirty_after: bool = False


@dataclass
class IoStats:
    disk_reads: int = 0
    disk_writes: int = 0


class DatabaseBackedBufferPool(LRUBufferPool):
    """LRU pool whose misses read from SQLite and evictions may write back."""

    def __init__(self, num_frames: int, disk: DiskManager) -> None:
        super().__init__(num_frames)
        self._disk = disk
        self._dirty: set[int] = set()
        self._payload: Dict[int, str] = {}
        self._version: Dict[int, int] = {}
        self.io = IoStats()

    def access(self, page_id: int, operation: str = "read") -> DbAccessResult:
        is_write = operation == "write"
        disk_read = False
        disk_write = False
        evicted: Optional[int] = None

        if page_id in self._map:
            self.stats.references += 1
            self.stats.hits += 1
            node = self._map[page_id]
            self._unlink(node)
            self._link_front(node)
            if is_write:
                self._dirty.add(page_id)
            return DbAccessResult(
                page_id=page_id,
                operation=operation,
                hit=True,
                disk_read=False,
                disk_write=False,
                dirty_after=page_id in self._dirty,
            )

        self.stats.references += 1
        self.stats.misses += 1

        if len(self._map) >= self.num_frames:
            lru = self._lru_node()
            evicted = lru.page_id
            if evicted in self._dirty:
                self._flush_page(evicted)
                disk_write = True
            self.stats.evictions += 1
            self._unlink(lru)
            del self._map[lru.page_id]
            self._payload.pop(evicted, None)
            self._version.pop(evicted, None)
            self._dirty.discard(evicted)

        row = self._disk.read_page(page_id)
        self.io.disk_reads += 1
        disk_read = True
        self._payload[page_id] = row.payload
        self._version[page_id] = row.version

        if is_write:
            self._dirty.add(page_id)
            self._touch_payload(page_id)

        node = _Node(page_id=page_id)
        self._map[page_id] = node
        self._link_front(node)

        return DbAccessResult(
            page_id=page_id,
            operation=operation,
            hit=False,
            evicted_page_id=evicted,
            disk_read=disk_read,
            disk_write=disk_write,
            dirty_after=page_id in self._dirty,
        )

    def _touch_payload(self, page_id: int) -> None:
        try:
            data = json.loads(self._payload.get(page_id, "{}"))
        except json.JSONDecodeError:
            data = {"page_id": page_id}
        if not isinstance(data, dict):
            data = {"page_id": page_id, "value": data}
        data["writes"] = int(data.get("writes", 0)) + 1
        self._payload[page_id] = json.dumps(data)
        self._version[page_id] = self._version.get(page_id, 1) + 1

    def _flush_page(self, page_id: int) -> None:
        payload = self._payload.get(page_id, json.dumps({"page_id": page_id}))
        version = self._version.get(page_id, 1)
        self._disk.write_page(page_id, payload, version)
        self.io.disk_writes += 1
        self._dirty.discard(page_id)

    def flush_all_dirty(self) -> int:
        count = 0
        for pid in list(self._dirty):
            self._flush_page(pid)
            count += 1
        return count
