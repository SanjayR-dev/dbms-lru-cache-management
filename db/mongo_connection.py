"""
MongoDB connectivity (PyMongo).

Environment:
  MONGODB_URI   default mongodb://127.0.0.1:27017
  MONGODB_DB    default buffer_lab
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.database import Database
from pymongo.errors import PyMongoError

_client: MongoClient | None = None

COL_DISK_PAGES = "disk_pages"
COL_WORKLOADS = "workloads"
COL_SIMULATION_RUNS = "simulation_runs"
COL_COUNTERS = "counters"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_mongo_uri() -> str:
    return os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017").strip()


def resolve_db_name() -> str:
    return os.environ.get("MONGODB_DB", "buffer_lab").strip() or "buffer_lab"


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            resolve_mongo_uri(),
            serverSelectionTimeoutMS=5000,
        )
    return _client


def get_database() -> Database:
    return get_client()[resolve_db_name()]


@contextmanager
def get_db() -> Generator[Database, None, None]:
    """Yield a database handle (MongoDB has no per-request transaction in this lab)."""
    yield get_database()


def init_database() -> None:
    db = get_database()
    db[COL_DISK_PAGES].create_index([("page_id", ASCENDING)], unique=True)
    db[COL_WORKLOADS].create_index([("name", ASCENDING)], unique=True)
    db[COL_SIMULATION_RUNS].create_index([("created_at", ASCENDING)])
    db[COL_COUNTERS].update_one(
        {"_id": "workload_id"},
        {"$setOnInsert": {"seq": 0}},
        upsert=True,
    )
    db[COL_COUNTERS].update_one(
        {"_id": "run_id"},
        {"$setOnInsert": {"seq": 0}},
        upsert=True,
    )


def next_sequence(db: Database, name: str) -> int:
    doc = db[COL_COUNTERS].find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])


def database_info() -> dict:
    uri = resolve_mongo_uri()
    db_name = resolve_db_name()
    try:
        get_client().admin.command("ping")
        ok = True
    except PyMongoError:
        ok = False
    return {
        "backend": "mongodb",
        "url": uri,
        "database": db_name,
        "dialect": "mongodb",
        "connected": ok,
    }


def collection_counts(db: Database) -> dict:
    return {
        "disk_pages": db[COL_DISK_PAGES].count_documents({}),
        "workloads": db[COL_WORKLOADS].count_documents({}),
        "simulation_runs": db[COL_SIMULATION_RUNS].count_documents({}),
    }
