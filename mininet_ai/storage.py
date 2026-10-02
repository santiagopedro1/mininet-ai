"""Private on-host directory and file preparation shared by runtime and storage."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class PrivateStoragePathError(OSError):
    """A storage path is unsafe or cannot be prepared."""

    def __init__(self, path: Path, reason: str, *, unsafe: bool) -> None:
        super().__init__(reason)
        self.path = path
        self.unsafe = unsafe


def check_private_directory(metadata: os.stat_result, directory: Path) -> None:
    """Validate an opened directory without inspecting a potentially replaced path."""
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise PrivateStoragePathError(
            directory,
            f"directory {directory} must be an owner-only directory owned by the current user",
            unsafe=True,
        )


def open_private_directory(
    directory: Path,
    *,
    create: bool,
    private_levels: int = 1,
) -> int:
    """Return a caller-owned descriptor, walking without following symlinks.

    Validate the last ``private_levels`` directories and every directory created
    during this call. Existing higher ancestors need not be private (e.g. /tmp).
    """
    path = directory.absolute()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            owned = index >= len(path.parts) - 1 - max(private_levels, 1)
            created = False
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
            if owned or created:
                check_private_directory(os.fstat(descriptor), path)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


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
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise PrivateStoragePathError(
                    database_path,
                    "path is not an owner-only regular file",
                    unsafe=True,
                )
        else:
            try:
                metadata = os.fstat(descriptor)
                if (
                    metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) & 0o077
                ):
                    raise PrivateStoragePathError(
                        database_path,
                        "filesystem did not create a private user-owned file; use a local --artifact-root",
                        unsafe=True,
                    )
            finally:
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
