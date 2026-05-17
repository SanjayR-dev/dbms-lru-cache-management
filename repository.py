"""Persistence helpers: workloads, runs (MongoDB)."""

from __future__ import annotations

from typing import Any, List, Optional

from pymongo.database import Database

from buffer_pool import AccessResult, optimal_hit_ratio
from db.mongo_connection import (
    COL_SIMULATION_RUNS,
    COL_WORKLOADS,
    collection_counts,
    next_sequence,
    utcnow,
)
from db_backed_pool import DatabaseBackedBufferPool, DbAccessResult
from disk_manager import DiskManager
from page_replacement import ReplacementPolicy, create_buffer_pool, frame_contents


class SimulationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save_workload(
        self,
        name: str,
        frame_count: int,
        references: List[dict],
        description: Optional[str] = None,
    ) -> dict:
        col = self._db[COL_WORKLOADS]
        existing = col.find_one({"name": name})
        if existing:
            col.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "frame_count": frame_count,
                        "references": references,
                        "description": description,
                    }
                },
            )
            existing.update(
                {
                    "frame_count": frame_count,
                    "references": references,
                    "description": description,
                }
            )
            return existing
        wl_id = next_sequence(self._db, "workload_id")
        doc = {
            "_id": wl_id,
            "name": name,
            "frame_count": frame_count,
            "references": references,
            "description": description,
            "created_at": utcnow(),
        }
        col.insert_one(doc)
        return doc

    def list_workloads(self) -> List[dict]:
        out = []
        for w in self._db[COL_WORKLOADS].find().sort("name", 1):
            refs = w.get("references", [])
            created = w.get("created_at")
            out.append(
                {
                    "id": w["_id"],
                    "name": w["name"],
                    "frame_count": w["frame_count"],
                    "reference_count": len(refs),
                    "description": w.get("description"),
                    "created_at": created.isoformat() if hasattr(created, "isoformat") else None,
                }
            )
        return out

    def get_workload(self, workload_id: int) -> Optional[dict]:
        w = self._db[COL_WORKLOADS].find_one({"_id": workload_id})
        if not w:
            return None
        return {
            "id": w["_id"],
            "name": w["name"],
            "frame_count": w["frame_count"],
            "references": w.get("references", []),
            "description": w.get("description"),
        }

    def run_memory_simulation(
        self,
        frames: int,
        references: List[dict],
        policy: ReplacementPolicy = "lru",
        workload_id: Optional[int] = None,
        persist: bool = True,
    ) -> dict:
        page_ids = [r["page_id"] for r in references]
        pool = create_buffer_pool(policy, frames, page_ids)
        steps: List[dict] = []
        for ref in references:
            r = pool.access(ref["page_id"])
            steps.append(
                _step_from_memory(r, ref.get("operation", "read"), pool, policy)
            )
        opt = optimal_hit_ratio(frames, page_ids)
        run_id = None
        if persist:
            run_id = self._persist_run(
                mode=f"memory:{policy}",
                frames=frames,
                workload_id=workload_id,
                steps=steps,
                stats=pool.stats,
                opt=opt,
                policy=policy,
            )
        return _result_payload(steps, pool.stats, opt, None, run_id, policy)

    def run_db_backed_simulation(
        self,
        frames: int,
        references: List[dict],
        policy: ReplacementPolicy = "lru",
        workload_id: Optional[int] = None,
        persist: bool = True,
        auto_seed: bool = True,
    ) -> dict:
        if policy != "lru":
            raise ValueError("DB-backed mode supports LRU replacement only")
        disk = DiskManager(self._db)
        page_ids = [r["page_id"] for r in references]
        if auto_seed:
            disk.seed_pages(page_ids)
        pool = DatabaseBackedBufferPool(frames, disk)
        steps: List[dict] = []
        for ref in references:
            r = pool.access(ref["page_id"], ref.get("operation", "read"))
            steps.append(_step_from_db(r, pool))
        pool.flush_all_dirty()
        opt = optimal_hit_ratio(frames, page_ids)
        run_id = None
        if persist:
            run_id = self._persist_run(
                mode=f"db_backed:{policy}",
                frames=frames,
                workload_id=workload_id,
                steps=steps,
                stats=pool.stats,
                opt=opt,
                disk_reads=pool.io.disk_reads,
                disk_writes=pool.io.disk_writes,
                policy=policy,
            )
        return _result_payload(steps, pool.stats, opt, pool.io, run_id, policy)

    def delete_all_runs(self) -> int:
        result = self._db[COL_SIMULATION_RUNS].delete_many({})
        return int(result.deleted_count)

    def list_runs(self, limit: int = 25) -> List[dict]:
        cursor = (
            self._db[COL_SIMULATION_RUNS]
            .find()
            .sort("_id", -1)
            .limit(limit)
        )
        return [_run_summary(doc) for doc in cursor]

    def get_run(self, run_id: int) -> Optional[dict]:
        doc = self._db[COL_SIMULATION_RUNS].find_one({"_id": run_id})
        if not doc:
            return None
        out = _run_summary(doc)
        out["steps"] = [_normalize_step(s) for s in doc.get("steps", [])]
        return out

    def db_stats(self) -> dict:
        return collection_counts(self._db)

    def _persist_run(
        self,
        mode: str,
        frames: int,
        workload_id: Optional[int],
        steps: List[dict],
        stats: Any,
        opt: float,
        disk_reads: int = 0,
        disk_writes: int = 0,
        policy: str = "lru",
    ) -> int:
        run_id = next_sequence(self._db, "run_id")
        step_docs = []
        for i, s in enumerate(steps, start=1):
            step_docs.append(
                {
                    "step_no": i,
                    "page_id": s["page"],
                    "operation": s.get("operation", "read"),
                    "hit": s["hit"],
                    "evicted_page_id": s.get("evicted"),
                    "disk_read": s.get("disk_read", False),
                    "disk_write": s.get("disk_write", False),
                    "dirty_after": s.get("dirty_after", False),
                    "frames_mru_to_lru": s["frames_mru_to_lru"],
                }
            )
        self._db[COL_SIMULATION_RUNS].insert_one(
            {
                "_id": run_id,
                "mode": mode,
                "frame_count": frames,
                "reference_count": stats.references,
                "hits": stats.hits,
                "misses": stats.misses,
                "evictions": stats.evictions,
                "hit_ratio": stats.hit_ratio,
                "disk_reads": disk_reads,
                "disk_writes": disk_writes,
                "optimal_hit_ratio": opt,
                "workload_id": workload_id,
                "created_at": utcnow(),
                "steps": step_docs,
            }
        )
        return run_id


def _step_from_memory(
    r: AccessResult, operation: str, pool: object, policy: str
) -> dict:
    return {
        "page": r.page_id,
        "operation": operation,
        "hit": r.hit,
        "evicted": r.evicted_page_id,
        "disk_read": False,
        "disk_write": False,
        "dirty_after": False,
        "policy": policy,
        "frames_mru_to_lru": frame_contents(pool),
    }


def _normalize_step(s: dict) -> dict:
    return {
        "step": s.get("step_no"),
        "page": s.get("page_id"),
        "operation": s.get("operation", "read"),
        "hit": s.get("hit"),
        "evicted": s.get("evicted_page_id"),
        "disk_read": s.get("disk_read", False),
        "disk_write": s.get("disk_write", False),
        "dirty_after": s.get("dirty_after", False),
        "frames_mru_to_lru": s.get("frames_mru_to_lru", []),
    }


def _step_from_db(r: DbAccessResult, pool: DatabaseBackedBufferPool) -> dict:
    return {
        "page": r.page_id,
        "operation": r.operation,
        "hit": r.hit,
        "evicted": r.evicted_page_id,
        "disk_read": r.disk_read,
        "disk_write": r.disk_write,
        "dirty_after": r.dirty_after,
        "frames_mru_to_lru": pool.frame_contents_mru_to_lru(),
    }


def _result_payload(steps, stats, opt, io, run_id, policy: str = "lru") -> dict:
    payload = {
        "steps": steps,
        "stats": {
            "references": stats.references,
            "hits": stats.hits,
            "misses": stats.misses,
            "evictions": stats.evictions,
            "hit_ratio": round(stats.hit_ratio, 6),
        },
        "optimal_hit_ratio": round(opt, 6),
        "replacement_policy": policy,
        "run_id": run_id,
    }
    if io is not None:
        payload["io"] = {"disk_reads": io.disk_reads, "disk_writes": io.disk_writes}
    return payload


def _parse_mode(mode: str) -> tuple[str, str]:
    if ":" in mode:
        base, policy = mode.split(":", 1)
        return base, policy
    return mode, "lru"


def _run_summary(doc: dict) -> dict:
    base_mode, policy = _parse_mode(doc.get("mode", ""))
    created = doc.get("created_at")
    return {
        "id": doc["_id"],
        "mode": base_mode,
        "replacement_policy": policy,
        "mode_label": doc.get("mode"),
        "frame_count": doc["frame_count"],
        "reference_count": doc["reference_count"],
        "hits": doc["hits"],
        "misses": doc["misses"],
        "evictions": doc["evictions"],
        "hit_ratio": doc["hit_ratio"],
        "disk_reads": doc.get("disk_reads", 0),
        "disk_writes": doc.get("disk_writes", 0),
        "optimal_hit_ratio": doc.get("optimal_hit_ratio"),
        "workload_id": doc.get("workload_id"),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else None,
    }


def parse_reference_string(
    pages: List[int], write_every_nth: int = 0
) -> List[dict]:
    refs: List[dict] = []
    for i, pid in enumerate(pages):
        op = "write" if write_every_nth and (i + 1) % write_every_nth == 0 else "read"
        refs.append({"page_id": pid, "operation": op})
    return refs
