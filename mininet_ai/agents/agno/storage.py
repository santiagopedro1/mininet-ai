"""Secure construction of Agno's persistent session database."""

from __future__ import annotations

from pathlib import Path

from agno.db.sqlite import SqliteDb

from mininet_ai.sdk import AgentProviderError
from mininet_ai.storage import PrivateStoragePathError, prepare_private_sqlite_file


def create_agno_database(path: str | Path) -> SqliteDb:
    """Create or open a private, regular SQLite file for Agno state."""

    database_path = Path(path)
    try:
        prepare_private_sqlite_file(database_path)
    except PrivateStoragePathError as error:
        raise AgentProviderError(
            f"could not prepare Agno session database {database_path}: {error}",
            code="agent.agno.database-invalid",
        ) from error
    return SqliteDb(db_file=str(database_path))
