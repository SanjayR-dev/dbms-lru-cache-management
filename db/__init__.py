"""MongoDB persistence layer."""

from db.mongo_connection import get_database, get_db, init_database

__all__ = ["get_database", "get_db", "init_database"]
