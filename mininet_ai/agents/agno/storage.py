"""Secure construction of Agno's persistent session database."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from agno.db.sqlite import SqliteDb

from mininet_ai.sdk import AgentProviderError


def create_agno_database(path: str | Path) -> SqliteDb:
    """Create or open a private, regular SQLite file for Agno state."""

    database_path = Path(path)
    try:
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = database_path.parent.stat()
        if not stat.S_ISDIR(parent.st_mode):
            raise OSError("database parent is not a directory")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(database_path, flags, 0o600)
        except FileExistsError:
            metadata = database_path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise OSError("database path is not a regular file")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise OSError("database file permissions must be 0600")
        else:
            os.close(descriptor)
    except OSError as error:
        raise AgentProviderError(
            f"could not prepare Agno session database {database_path}: {error}",
            code="agent.agno.database-invalid",
        ) from error
    return SqliteDb(db_file=str(database_path))
