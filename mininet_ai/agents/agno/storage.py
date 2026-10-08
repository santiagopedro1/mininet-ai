"""Secure construction of Agno's persistent session database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from agno.db.sqlite import SqliteDb

from mininet_ai.saved_runs import SourceLocks
from mininet_ai.sdk import AgentProviderError
from mininet_ai.storage import PrivateStoragePathError, prepare_private_sqlite_file


class _PrivateSqliteDb(SqliteDb):
    def __init__(self, path: Path) -> None:
        self._source_lock = SourceLocks([path], writer=True, create=True)
        try:
            self._source_lock.__enter__()
            prepare_private_sqlite_file(path)
            # Agno builds its engine lazily. Check opening and write locking
            # before deployment without creating/changing Agno-owned tables.
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("PRAGMA schema_version").fetchone()
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            super().__init__(db_file=str(path))
        except BaseException:
            self._source_lock.close()
            raise

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._source_lock.close()


def create_agno_database(path: str | Path) -> SqliteDb:
    """Create or open a private, regular SQLite file for Agno state."""

    database_path = Path(path)
    try:
        return _PrivateSqliteDb(database_path)
    except (PrivateStoragePathError, OSError, sqlite3.Error) as error:
        raise AgentProviderError(
            f"could not prepare Agno session database {database_path}: {error}",
            code="agent.agno.database-invalid",
        ) from error
