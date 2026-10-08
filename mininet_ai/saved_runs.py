"""Private source discovery and lifetime coordination for saved run evidence.

Locks cover cooperating local writers, not hostile same-user/root processes.
They are independent of artifact roots and survive neither reboot nor external
namespace replacement. Finalization is therefore checked separately.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import uuid
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from mininet_ai.storage import (
    PrivateStoragePathError,
    check_private_directory,
    open_private_directory,
)
from mininet_ai.substrates.mininet_ovs.state import ProcessOwner


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def open_source(path: Path, *, create: bool = False) -> int:
    """Open an owner-only file/directory without following any path symlinks."""
    path = absolute(path)
    parent = open_private_directory(path.parent, create=create, private_levels=0)
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            fd = os.open(path.name, flags, dir_fd=parent)
        except FileNotFoundError:
            if not create:
                raise
            try:
                fd = os.open(
                    path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent
                )
            except FileExistsError:
                fd = os.open(path.name, flags, dir_fd=parent)
        try:
            metadata = os.fstat(fd)
            if (
                not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise PrivateStoragePathError(
                    path, f"unsafe private source {path}", unsafe=True
                )
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(parent)


def identity(fd: int) -> tuple[int, int]:
    metadata = os.fstat(fd)
    return metadata.st_dev, metadata.st_ino


class SourceLocks:
    """Hold shared lifetime writer locks or nonblocking exclusive export locks.

    Open sources first without database initialization, deduplicate physical
    aliases, acquire a deterministic set, then revalidate source/lock identities.
    Never unlink locks: that would create independent domains for live holders.
    """

    def __init__(
        self, paths: list[Path], *, writer: bool, create: bool = False
    ) -> None:
        self.paths = [absolute(path) for path in paths]
        self.writer = writer
        self.create = create
        self._sources: dict[Path, int] = {}
        self._locks: dict[str, int] = {}
        self._namespace: int | None = None
        self.namespace = Path(f"/tmp/mininet-ai-locks-{os.geteuid()}")

    def __enter__(self) -> Self:
        try:
            self._namespace = open_private_directory(self.namespace, create=True)
            for path in self.paths:
                if path not in self._sources:
                    self._sources[path] = open_source(path, create=self.create)
            for device, inode in sorted(
                {identity(fd) for fd in self._sources.values()}
            ):
                name = f"{device}-{inode}.lock"
                fd = os.open(
                    name,
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=self._namespace,
                )
                self._locks[name] = fd
                metadata = os.fstat(fd)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or metadata.st_mode & 0o077
                ):
                    raise OSError(f"unsafe source lock {self.namespace / name}")
                try:
                    fcntl.flock(
                        fd,
                        (fcntl.LOCK_SH if self.writer else fcntl.LOCK_EX)
                        | fcntl.LOCK_NB,
                    )
                except BlockingIOError as error:
                    raise OSError(
                        "source busy: a managed writer or export is active"
                    ) from error
            self.verify()
            return self
        except BaseException:
            self.close()
            raise

    def descriptor(self, path: Path) -> int:
        return self._sources[absolute(path)]

    def verify(self) -> None:
        assert self._namespace is not None
        namespace = open_private_directory(self.namespace, create=False)
        try:
            if identity(namespace) != identity(self._namespace):
                raise OSError("source lock namespace was replaced")
            check_private_directory(os.fstat(namespace), self.namespace)
            for name, fd in self._locks.items():
                metadata = os.stat(name, dir_fd=namespace, follow_symlinks=False)
                if (metadata.st_dev, metadata.st_ino) != identity(fd):
                    raise OSError(f"source lock was replaced: {name}")
            for path, fd in self._sources.items():
                other = open_source(path)
                try:
                    if identity(other) != identity(fd):
                        raise OSError(f"source path was replaced: {path}")
                finally:
                    os.close(other)
        finally:
            os.close(namespace)

    def close(self) -> None:
        for fd in (*self._locks.values(), *self._sources.values()):
            os.close(fd)
        self._locks.clear()
        self._sources.clear()
        if self._namespace is not None:
            os.close(self._namespace)
            self._namespace = None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                self.verify()
        finally:
            self.close()


class RunMetadata(BaseModel):
    """Version-one local source registry, not a signed evidence manifest."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    run_id: str
    owner_uid: int
    owner: ProcessOwner
    sources: dict[str, str]
    lifecycle: str = "preparing"
    finalized: bool = False
    managed_writers_verified: bool = False
    outcome: str = "unknown"
    source_identities: dict[str, tuple[int, int]] = Field(default_factory=dict)


def write_metadata(directory: Path, metadata: RunMetadata) -> None:
    parent = open_private_directory(directory, create=False)
    temporary = f".run-{uuid.uuid4().hex}.json"
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        with os.fdopen(fd, "w") as stream:
            stream.write(metadata.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, "run.json", src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def read_metadata(directory: Path, run_id: str) -> RunMetadata:
    fd = open_source(directory / "run.json")
    with os.fdopen(fd) as stream:
        metadata = RunMetadata.model_validate(json.load(stream))
    if metadata.schema_version != 1:
        raise ValueError(f"unsupported run metadata version {metadata.schema_version}")
    if metadata.run_id != run_id or metadata.owner_uid != os.geteuid():
        raise ValueError("run metadata identity/ownership mismatch")
    if set(metadata.sources) != {"ledger", "shared_state", "log", "agno", "artifacts"}:
        raise ValueError("run metadata has invalid source roles")
    if any(not Path(path).is_absolute() for path in metadata.sources.values()):
        raise ValueError("run metadata source paths must be absolute")
    return metadata


class RunEvidence:
    """Own early source metadata and coordination until final output is closed."""

    def __init__(
        self,
        directory: Path,
        run_id: str,
        sources: dict[str, Path],
        *,
        verified_writers: bool,
    ) -> None:
        self.directory = directory
        self.metadata = RunMetadata(
            run_id=run_id,
            owner_uid=os.geteuid(),
            owner=ProcessOwner.current(),
            sources={key: str(absolute(path)) for key, path in sources.items()},
            managed_writers_verified=verified_writers,
        )
        self._run_lock = SourceLocks([directory], writer=True)
        self._source_locks: SourceLocks | None = None
        try:
            self._run_lock.__enter__()
            write_metadata(directory, self.metadata)
            self._source_locks = SourceLocks(
                list(sources.values()), writer=True, create=True
            )
            self._source_locks.__enter__()
            self.metadata.source_identities = {
                role: identity(self._source_locks.descriptor(path))
                for role, path in sources.items()
            }
            write_metadata(directory, self.metadata)
        except BaseException:
            self.close()
            raise

    def active(self) -> None:
        self.metadata.lifecycle = "running"
        write_metadata(self.directory, self.metadata)

    def finish(self, *, finalized: bool, outcome: str) -> None:
        self._run_lock.verify()
        if self._source_locks is not None:
            self._source_locks.verify()
        self.metadata.lifecycle = "stopped" if finalized else "failed"
        self.metadata.finalized = finalized
        self.metadata.outcome = outcome
        write_metadata(self.directory, self.metadata)

    def close(self) -> None:
        if self._source_locks is not None:
            self._source_locks.close()
        self._run_lock.close()
