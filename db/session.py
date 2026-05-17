"""Unified DB session: MongoDB or SQLite fallback."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Tuple, Union

from db.backend import get_active_backend, resolve_backend
from db.mongo_connection import database_info as mongo_database_info
from db.mongo_connection import get_db, init_database
from db.sqlite_store import (
    SqliteDiskManager,
    SqliteSimulationRepository,
    get_sqlite,
    init_sqlite,
    sqlite_database_info,
)
from disk_manager import DiskManager
from repository import SimulationRepository


@contextmanager
def use_store() -> Generator[
    Tuple[Union[SimulationRepository, SqliteSimulationRepository], Union[DiskManager, SqliteDiskManager]],
    None,
    None,
]:
    if get_active_backend() == "mongodb":
        with get_db() as db:
            yield SimulationRepository(db), DiskManager(db)
    else:
        with get_sqlite() as conn:
            yield SqliteSimulationRepository(conn), SqliteDiskManager(conn)


def startup_database() -> str:
    backend = resolve_backend()
    if backend == "mongodb":
        init_database()
    else:
        init_sqlite()
    return backend


def connection_info() -> dict:
    if get_active_backend() == "mongodb":
        return mongo_database_info()
    return sqlite_database_info()
