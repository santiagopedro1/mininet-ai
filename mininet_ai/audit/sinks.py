"""Append-only sinks for structured audit events."""

from __future__ import annotations

import fcntl
import os
import stat
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from mininet_ai.audit.contracts import AuditEvent
from mininet_ai.errors import AuditError


_DEFAULT_EVENT_LIMIT = 4 * 1024 * 1024


@runtime_checkable
class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None:
        """Persist one complete event or raise without silently dropping it."""
        ...


class MemoryAuditSink:
    """Thread-safe sink for tests and embedding applications."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def write(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)


class JsonLinesAuditSink:
    """Append UTF-8 JSON events to a process-safe, owner-only file."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_event_bytes: int = _DEFAULT_EVENT_LIMIT,
        sync: bool = False,
    ) -> None:
        if max_event_bytes <= 0:
            raise ValueError("max_event_bytes must be positive")
        self.path = Path(path)
        self._max_event_bytes = max_event_bytes
        self._sync = sync
        self._lock = threading.Lock()

    def write(self, event: AuditEvent) -> None:
        payload = (event.model_dump_json(by_alias=True) + "\n").encode("utf-8")
        if len(payload) > self._max_event_bytes:
            raise AuditError(
                "audit event exceeds the configured size limit",
                code="audit.event.too-large",
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NONBLOCK
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise AuditError(
                f"could not open audit log {self.path}: {error}",
                code="audit.write.failed",
            ) from error
        try:
            file_status = os.fstat(descriptor)
            mode = stat.S_IMODE(file_status.st_mode)
            if not stat.S_ISREG(file_status.st_mode):
                raise AuditError(
                    f"audit log {self.path} is not a regular file",
                    code="audit.path.unsafe",
                )
            if mode & 0o077:
                raise AuditError(
                    f"audit log {self.path} must not be accessible by other users",
                    code="audit.path.unsafe",
                )
            with self._lock:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._write_all(descriptor, payload)
                if self._sync:
                    os.fsync(descriptor)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as error:
            raise AuditError(
                f"could not append audit log {self.path}: {error}",
                code="audit.write.failed",
            ) from error
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("audit write made no progress")
            remaining = remaining[written:]
