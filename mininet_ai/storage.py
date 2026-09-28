"""Private on-host storage preparation shared by SQLite-backed modules."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class PrivateStoragePathError(OSError):
    """A persistent database path is unsafe or cannot be prepared."""

    def __init__(self, path: Path, reason: str, *, unsafe: bool) -> None:
        super().__init__(reason)
        self.path = path
        self.unsafe = unsafe


def prepare_private_sqlite_file(path: str | Path) -> Path:
    """Return an owner-only regular SQLite path, creating it when absent."""

    database_path = Path(path)
    try:
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not stat.S_ISDIR(database_path.parent.stat().st_mode):
            raise PrivateStoragePathError(
                database_path,
                "database parent is not a directory",
                unsafe=False,
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(database_path, flags, 0o600)
        except FileExistsError:
            metadata = database_path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise PrivateStoragePathError(
                    database_path,
                    "path is not an owner-only regular file",
                    unsafe=True,
                )
        else:
            os.close(descriptor)
    except PrivateStoragePathError:
        raise
    except OSError as error:
        raise PrivateStoragePathError(
            database_path,
            str(error) or type(error).__name__,
            unsafe=False,
        ) from error
    return database_path
