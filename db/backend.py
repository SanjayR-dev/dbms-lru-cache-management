"""Choose MongoDB or SQLite fallback."""

from __future__ import annotations

import os
from typing import Literal

Backend = Literal["mongodb", "sqlite"]

_active: Backend | None = None


def resolve_backend() -> Backend:
    global _active
    if _active is not None:
        return _active
    forced = os.environ.get("DB_BACKEND", "").strip().lower()
    if forced == "sqlite":
        _active = "sqlite"
        return _active
    if forced == "mongodb":
        _active = "mongodb"
        return _active
    try:
        from db.mongo_connection import get_client

        get_client().admin.command("ping")
        _active = "mongodb"
    except Exception:
        from db.sqlite_store import init_sqlite

        init_sqlite()
        _active = "sqlite"
    return _active


def get_active_backend() -> Backend:
    return resolve_backend()
