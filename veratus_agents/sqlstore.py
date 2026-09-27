"""Shared SQLite/PostgreSQL plumbing for small append-mostly stores."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

SCHEMA_LOCK_KEY = 5640914734765077


def lock_schema(connection: Any) -> None:
    """Serialize CREATE TABLE IF NOT EXISTS between workers on PostgreSQL.

    Two sessions creating the same table at once fail with a unique violation,
    which happens when both workers take their first request on a fresh
    database. The advisory lock ends with the transaction.
    """
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))


class SqlStore:
    """SQLite locally; PostgreSQL whenever ``database_url`` is configured.

    Subclasses set ``SCHEMA`` and write queries with ``?`` placeholders, which
    ``_sql`` converts for psycopg.
    """

    SCHEMA = ""

    def __init__(self, sqlite_path: str | Path, database_url: str | None = None):
        self.database_url = database_url
        self.path = None if database_url else Path(sqlite_path)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as (db, _):
            if self.path:
                db.executescript(self.SCHEMA)
            else:
                lock_schema(db)
                db.execute(self.SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[tuple[Any, str]]:
        """Yield (connection, placeholder); commit on success, roll back on error."""
        if self.database_url:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self.database_url, row_factory=dict_row) as db:
                yield db, "%s"
            return
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db, "?"
            db.commit()

    @staticmethod
    def _sql(query: str, placeholder: str) -> str:
        return query.replace("?", placeholder)
