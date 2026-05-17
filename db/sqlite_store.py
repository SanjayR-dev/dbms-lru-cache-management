"""
SQLite fallback when MongoDB is unavailable (stdlib sqlite3).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, List, Optional

from buffer_pool import AccessResult, optimal_hit_ratio
from db_backed_pool import DatabaseBackedBufferPool, DbAccessResult
from disk_manager import DiskPage
from page_replacement import ReplacementPolicy, create_buffer_pool, frame_contents

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _PROJECT_ROOT / "data" / "buffer_lab.db"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_sqlite() -> Generator[sqlite3.Connection, None, None]:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_sqlite() -> None:
    with get_sqlite() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS disk_pages (
                page_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS workloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                frame_count INTEGER NOT NULL,
                reference_json TEXT NOT NULL,
                description TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS simulation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                frame_count INTEGER NOT NULL,
                reference_count INTEGER NOT NULL,
                hits INTEGER NOT NULL,
                misses INTEGER NOT NULL,
                evictions INTEGER NOT NULL,
                hit_ratio REAL NOT NULL,
                disk_reads INTEGER DEFAULT 0,
                disk_writes INTEGER DEFAULT 0,
                optimal_hit_ratio REAL,
                workload_id INTEGER,
                created_at TEXT,
                steps_json TEXT NOT NULL DEFAULT '[]'
            );
            """
        )
        _migrate_sqlite_schema(conn)


def _migrate_sqlite_schema(conn: sqlite3.Connection) -> None:
    """Upgrade old SQLAlchemy SQLite file (separate access_log, no steps_json)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(simulation_runs)")}
    if not cols:
        return
    if "steps_json" in cols:
        return
    conn.execute("DROP TABLE IF EXISTS access_log")
    conn.execute("DROP TABLE IF EXISTS simulation_runs")
    conn.execute(
        """
        CREATE TABLE simulation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            frame_count INTEGER NOT NULL,
            reference_count INTEGER NOT NULL,
            hits INTEGER NOT NULL,
            misses INTEGER NOT NULL,
            evictions INTEGER NOT NULL,
            hit_ratio REAL NOT NULL,
            disk_reads INTEGER DEFAULT 0,
            disk_writes INTEGER DEFAULT 0,
            optimal_hit_ratio REAL,
            workload_id INTEGER,
            created_at TEXT,
            steps_json TEXT NOT NULL DEFAULT '[]'
        )
        """
    )


def sqlite_database_info() -> dict:
    return {
        "backend": "sqlite",
        "url": str(_DB_PATH),
        "database": _DB_PATH.name,
        "dialect": "sqlite",
        "connected": True,
    }


class SqliteDiskManager:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def read_page(self, page_id: int) -> DiskPage:
        row = self._conn.execute(
            "SELECT page_id, payload, version, updated_at FROM disk_pages WHERE page_id = ?",
            (page_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Page {page_id} not on disk. Seed the database first.")
        return DiskPage(row["page_id"], row["payload"], row["version"], row["updated_at"])

    def write_page(self, page_id: int, payload: str, version: int) -> DiskPage:
        now = utcnow_iso()
        self._conn.execute(
            """
            INSERT INTO disk_pages (page_id, payload, version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(page_id) DO UPDATE SET
                payload = excluded.payload,
                version = excluded.version,
                updated_at = excluded.updated_at
            """,
            (page_id, payload, version, now),
        )
        return DiskPage(page_id, payload, version, now)

    def list_pages(self, limit: int = 500) -> List[dict]:
        rows = self._conn.execute(
            "SELECT page_id, payload, version, updated_at FROM disk_pages ORDER BY page_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def seed_pages(self, page_ids: List[int], prefix: str = "row") -> int:
        created = 0
        now = utcnow_iso()
        for pid in sorted(set(page_ids)):
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO disk_pages (page_id, payload, version, updated_at)
                VALUES (?, ?, 1, ?)
                """,
                (
                    pid,
                    json.dumps({"page_id": pid, "data": f"{prefix}-{pid}"}),
                    now,
                ),
            )
            created += cur.rowcount
        return created

    def page_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM disk_pages").fetchone()[0]


class SqliteSimulationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_workload(
        self, name: str, frame_count: int, references: List[dict], description: Optional[str] = None
    ) -> dict:
        row = self._conn.execute(
            "SELECT id FROM workloads WHERE name = ?", (name,)
        ).fetchone()
        ref_json = json.dumps(references)
        if row:
            self._conn.execute(
                """
                UPDATE workloads SET frame_count = ?, reference_json = ?, description = ?
                WHERE name = ?
                """,
                (frame_count, ref_json, description, name),
            )
            return {"_id": row["id"], "name": name}
        cur = self._conn.execute(
            """
            INSERT INTO workloads (name, frame_count, reference_json, description, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, frame_count, ref_json, description, utcnow_iso()),
        )
        return {"_id": cur.lastrowid, "name": name}

    def list_workloads(self) -> List[dict]:
        rows = self._conn.execute("SELECT * FROM workloads ORDER BY name").fetchall()
        out = []
        for w in rows:
            refs = json.loads(w["reference_json"])
            out.append(
                {
                    "id": w["id"],
                    "name": w["name"],
                    "frame_count": w["frame_count"],
                    "reference_count": len(refs),
                    "description": w["description"],
                    "created_at": w["created_at"],
                }
            )
        return out

    def get_workload(self, workload_id: int) -> Optional[dict]:
        w = self._conn.execute(
            "SELECT * FROM workloads WHERE id = ?", (workload_id,)
        ).fetchone()
        if not w:
            return None
        return {
            "id": w["id"],
            "name": w["name"],
            "frame_count": w["frame_count"],
            "references": json.loads(w["reference_json"]),
            "description": w["description"],
        }

    def delete_all_runs(self) -> int:
        cur = self._conn.execute("DELETE FROM simulation_runs")
        return cur.rowcount

    def list_runs(self, limit: int = 25) -> List[dict]:
        from repository import _run_summary

        rows = self._conn.execute(
            "SELECT * FROM simulation_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_run_summary_sqlite(dict(r)) for r in rows]

    def get_run(self, run_id: int) -> Optional[dict]:
        from repository import _normalize_step

        r = self._conn.execute(
            "SELECT * FROM simulation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not r:
            return None
        out = _run_summary_sqlite(dict(r))
        steps = json.loads(r["steps_json"])
        out["steps"] = [_normalize_step(s) for s in steps]
        return out

    def db_stats(self) -> dict:
        return {
            "disk_pages": self._conn.execute("SELECT COUNT(*) FROM disk_pages").fetchone()[0],
            "workloads": self._conn.execute("SELECT COUNT(*) FROM workloads").fetchone()[0],
            "simulation_runs": self._conn.execute(
                "SELECT COUNT(*) FROM simulation_runs"
            ).fetchone()[0],
        }

    def run_memory_simulation(
        self, frames: int, references: List[dict], policy: ReplacementPolicy = "lru",
        workload_id: Optional[int] = None, persist: bool = True,
    ) -> dict:
        from repository import _result_payload, _step_from_memory

        page_ids = [r["page_id"] for r in references]
        pool = create_buffer_pool(policy, frames, page_ids)
        steps = []
        for ref in references:
            r = pool.access(ref["page_id"])
            steps.append(_step_from_memory(r, ref.get("operation", "read"), pool, policy))
        opt = optimal_hit_ratio(frames, page_ids)
        run_id = None
        if persist:
            run_id = self._persist_run(f"memory:{policy}", frames, workload_id, steps, pool.stats, opt, policy=policy)
        return _result_payload(steps, pool.stats, opt, None, run_id, policy)

    def run_db_backed_simulation(
        self, frames: int, references: List[dict], policy: ReplacementPolicy = "lru",
        workload_id: Optional[int] = None, persist: bool = True, auto_seed: bool = True,
    ) -> dict:
        from repository import _result_payload, _step_from_db

        if policy != "lru":
            raise ValueError("DB-backed mode supports LRU replacement only")
        disk = SqliteDiskManager(self._conn)
        page_ids = [r["page_id"] for r in references]
        if auto_seed:
            disk.seed_pages(page_ids)
        pool = DatabaseBackedBufferPool(frames, disk)
        steps = []
        for ref in references:
            r = pool.access(ref["page_id"], ref.get("operation", "read"))
            steps.append(_step_from_db(r, pool))
        pool.flush_all_dirty()
        opt = optimal_hit_ratio(frames, page_ids)
        run_id = None
        if persist:
            run_id = self._persist_run(
                f"db_backed:{policy}", frames, workload_id, steps, pool.stats, opt,
                disk_reads=pool.io.disk_reads, disk_writes=pool.io.disk_writes, policy=policy,
            )
        return _result_payload(steps, pool.stats, opt, pool.io, run_id, policy)

    def _persist_run(
        self, mode: str, frames: int, workload_id: Optional[int], steps: List[dict],
        stats: Any, opt: float, disk_reads: int = 0, disk_writes: int = 0, policy: str = "lru",
    ) -> int:
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
        cur = self._conn.execute(
            """
            INSERT INTO simulation_runs (
                mode, frame_count, reference_count, hits, misses, evictions,
                hit_ratio, disk_reads, disk_writes, optimal_hit_ratio, workload_id,
                created_at, steps_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mode, frames, stats.references, stats.hits, stats.misses, stats.evictions,
                stats.hit_ratio, disk_reads, disk_writes, opt, workload_id,
                utcnow_iso(), json.dumps(step_docs),
            ),
        )
        return int(cur.lastrowid)


def _run_summary_sqlite(doc: dict) -> dict:
    from repository import _parse_mode

    base_mode, policy = _parse_mode(doc.get("mode", ""))
    return {
        "id": doc["id"],
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
        "created_at": doc.get("created_at"),
    }
