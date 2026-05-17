"""
Disk manager: reads/writes logical pages through PyMongo → MongoDB.

The buffer pool treats this as secondary storage; only misses trigger reads,
and evicted dirty frames are written back (write-back policy).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List

from pymongo.database import Database

from db.mongo_connection import COL_DISK_PAGES, utcnow


@dataclass
class DiskPage:
    page_id: int
    payload: str
    version: int
    updated_at: str | None = None


class DiskManager:
    def __init__(self, db: Database) -> None:
        self._col = db[COL_DISK_PAGES]

    def read_page(self, page_id: int) -> DiskPage:
        doc = self._col.find_one({"page_id": page_id})
        if doc is None:
            raise KeyError(f"Page {page_id} not on disk. Seed the database first.")
        return _doc_to_page(doc)

    def write_page(self, page_id: int, payload: str, version: int) -> DiskPage:
        now = utcnow()
        doc = {
            "page_id": page_id,
            "payload": payload,
            "version": version,
            "updated_at": now,
        }
        self._col.update_one({"page_id": page_id}, {"$set": doc}, upsert=True)
        return DiskPage(
            page_id=page_id,
            payload=payload,
            version=version,
            updated_at=now.isoformat(),
        )

    def list_pages(self, limit: int = 500) -> List[dict]:
        cursor = self._col.find().sort("page_id", 1).limit(limit)
        out = []
        for doc in cursor:
            p = _doc_to_page(doc)
            out.append(
                {
                    "page_id": p.page_id,
                    "payload": p.payload,
                    "version": p.version,
                    "updated_at": p.updated_at,
                }
            )
        return out

    def seed_pages(self, page_ids: List[int], prefix: str = "row") -> int:
        created = 0
        now = utcnow()
        for pid in sorted(set(page_ids)):
            res = self._col.update_one(
                {"page_id": pid},
                {
                    "$setOnInsert": {
                        "page_id": pid,
                        "payload": json.dumps({"page_id": pid, "data": f"{prefix}-{pid}"}),
                        "version": 1,
                        "updated_at": now,
                    }
                },
                upsert=True,
            )
            if res.upserted_id is not None:
                created += 1
        return created

    def page_count(self) -> int:
        return self._col.count_documents({})


def _doc_to_page(doc: dict) -> DiskPage:
    updated = doc.get("updated_at")
    if hasattr(updated, "isoformat"):
        updated = updated.isoformat()
    return DiskPage(
        page_id=int(doc["page_id"]),
        payload=str(doc["payload"]),
        version=int(doc.get("version", 1)),
        updated_at=updated,
    )
