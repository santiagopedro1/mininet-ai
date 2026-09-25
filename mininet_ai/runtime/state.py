"""Mininet-owned, scoped shared operational state."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol, runtime_checkable

from pydantic import JsonValue

from mininet_ai.errors import MininetAIError
from mininet_ai.sdk import (
    SharedStateChange,
    SharedStateEntry,
    SharedStateSnapshot,
    SharedStateUpdate,
)


SharedScope = Literal["run", "deployment"]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SharedStateError(MininetAIError):
    """A shared-state operation was invalid or could not be completed."""

    def __init__(self, message: str, *, code: str, conflict: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.conflict = conflict


@dataclass(frozen=True)
class SharedStateAccess:
    """Authorized namespaces and capacities for one agent invocation."""

    run_id: str
    deployment: str
    agent_id: str
    limits: Mapping[SharedScope, int]

    def namespace(self, scope: SharedScope) -> str:
        if scope not in self.limits:
            raise SharedStateError(
                f"agent {self.agent_id!r} cannot access {scope!r} shared state",
                code="state.scope.forbidden",
                conflict=True,
            )
        if scope == "run":
            return f"run:{self.run_id}"
        return f"run:{self.run_id}:deployment:{self.deployment}"


@runtime_checkable
class SharedStateStore(Protocol):
    """Read and atomically mutate authorized shared-state namespaces."""

    def snapshot(self, access: SharedStateAccess) -> SharedStateSnapshot: ...

    def apply(
        self,
        access: SharedStateAccess,
        updates: tuple[SharedStateUpdate, ...],
    ) -> tuple[SharedStateChange, ...]: ...

    def close(self) -> None: ...


def _validate_updates(
    access: SharedStateAccess,
    updates: tuple[SharedStateUpdate, ...],
) -> None:
    identities: set[tuple[str, str]] = set()
    for update in updates:
        access.namespace(update.scope)
        identity = (update.scope, update.key)
        if identity in identities:
            raise SharedStateError(
                f"duplicate shared-state update for {update.scope}:{update.key}",
                code="state.update.duplicate",
                conflict=True,
            )
        identities.add(identity)


def _check_expected(
    update: SharedStateUpdate,
    current_version: int | None,
    deleted: bool,
) -> None:
    expected = update.expected_version
    visible_version = None if deleted else current_version
    if expected is None:
        return
    if expected == 0 and visible_version is None:
        return
    if expected != visible_version:
        actual = 0 if visible_version is None else visible_version
        raise SharedStateError(
            f"shared-state version conflict for {update.scope}:{update.key}; "
            f"expected {expected}, found {actual}",
            code="state.version.conflict",
            conflict=True,
        )


class InMemorySharedStateStore:
    """Thread-safe in-memory shared state for tests and rootless runtimes."""

    def __init__(self, *, clock: Clock = _utc_now) -> None:
        self._clock = clock
        self._lock = RLock()
        self._entries: dict[
            tuple[str, str], tuple[JsonValue, int, bool, str, datetime]
        ] = {}

    def snapshot(self, access: SharedStateAccess) -> SharedStateSnapshot:
        with self._lock:
            values = {
                scope: self._scope_snapshot(access, scope)
                for scope in access.limits
            }
        return SharedStateSnapshot(
            allowedScopes=tuple(access.limits),
            run=values.get("run", {}),
            deployment=values.get("deployment", {}),
        )

    def _scope_snapshot(
        self,
        access: SharedStateAccess,
        scope: SharedScope,
    ) -> dict[str, SharedStateEntry]:
        namespace = access.namespace(scope)
        return {
            key: SharedStateEntry(
                value=value,
                version=version,
                updatedBy=updated_by,
                updatedAt=updated_at,
            )
            for (stored_namespace, key), (
                value,
                version,
                deleted,
                updated_by,
                updated_at,
            ) in sorted(self._entries.items())
            if stored_namespace == namespace and not deleted
        }

    def apply(
        self,
        access: SharedStateAccess,
        updates: tuple[SharedStateUpdate, ...],
    ) -> tuple[SharedStateChange, ...]:
        _validate_updates(access, updates)
        with self._lock:
            staged = dict(self._entries)
            changes = self._apply_to(staged, access, updates)
            self._entries = staged
            return changes

    def _apply_to(
        self,
        entries: dict[tuple[str, str], tuple[JsonValue, int, bool, str, datetime]],
        access: SharedStateAccess,
        updates: tuple[SharedStateUpdate, ...],
    ) -> tuple[SharedStateChange, ...]:
        changes = []
        for update in updates:
            namespace = access.namespace(update.scope)
            identity = (namespace, update.key)
            current = entries.get(identity)
            version = current[1] if current is not None else None
            deleted = current[2] if current is not None else True
            _check_expected(update, version, deleted)
            next_version = (version or 0) + 1
            if update.operation == "delete" and (current is None or deleted):
                raise SharedStateError(
                    f"shared-state entry {update.scope}:{update.key} does not exist",
                    code="state.entry.missing",
                    conflict=True,
                )
            if update.operation == "set" and (current is None or deleted):
                active = sum(
                    1
                    for (stored_namespace, _), item in entries.items()
                    if stored_namespace == namespace and not item[2]
                )
                if active >= access.limits[update.scope]:
                    raise SharedStateError(
                        f"{update.scope} shared state reached its entry limit",
                        code="state.capacity.exceeded",
                        conflict=True,
                    )
            now = self._clock()
            value = update.value
            is_deleted = update.operation == "delete"
            entries[identity] = (
                value,
                next_version,
                is_deleted,
                access.agent_id,
                now,
            )
            changes.append(
                SharedStateChange(
                    scope=update.scope,
                    operation=update.operation,
                    key=update.key,
                    value=None if is_deleted else value,
                    version=next_version,
                )
            )
        return tuple(changes)

    def close(self) -> None:
        return None


class SQLiteSharedStateStore:
    """Persistent, transactional SQLite shared-state adapter."""

    def __init__(self, path: str | Path, *, clock: Clock = _utc_now) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = None
        self._prepare_file()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, check_same_thread=False)
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, 1}:
                raise SharedStateError(
                    f"shared-state schema version {version} is unsupported",
                    code="state.version.unsupported",
                )
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA temp_store = MEMORY")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_state (
                    namespace TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT,
                    version INTEGER NOT NULL,
                    deleted INTEGER NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
                """
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
            self._connection = connection
        except SharedStateError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise SharedStateError(
                f"could not open shared-state store {self.path}: {error}",
                code="state.open.failed",
            ) from error

    def snapshot(self, access: SharedStateAccess) -> SharedStateSnapshot:
        connection = self._require_open()
        values: dict[str, dict[str, SharedStateEntry]] = {}
        try:
            with self._lock:
                for scope in access.limits:
                    rows = connection.execute(
                        """
                        SELECT key, value_json, version, updated_by, updated_at
                        FROM shared_state
                        WHERE namespace = ? AND deleted = 0
                        ORDER BY key
                        """,
                        (access.namespace(scope),),
                    ).fetchall()
                    values[scope] = {
                        row[0]: SharedStateEntry(
                            value=json.loads(row[1]),
                            version=row[2],
                            updatedBy=row[3],
                            updatedAt=row[4],
                        )
                        for row in rows
                    }
        except (sqlite3.Error, ValueError) as error:
            raise SharedStateError(
                f"could not read shared state: {error}",
                code="state.read.failed",
            ) from error
        return SharedStateSnapshot(
            allowedScopes=tuple(access.limits),
            run=values.get("run", {}),
            deployment=values.get("deployment", {}),
        )

    def apply(
        self,
        access: SharedStateAccess,
        updates: tuple[SharedStateUpdate, ...],
    ) -> tuple[SharedStateChange, ...]:
        _validate_updates(access, updates)
        connection = self._require_open()
        changes = []
        with self._lock:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for update in updates:
                    namespace = access.namespace(update.scope)
                    row = connection.execute(
                        """
                        SELECT value_json, version, deleted
                        FROM shared_state WHERE namespace = ? AND key = ?
                        """,
                        (namespace, update.key),
                    ).fetchone()
                    version = int(row[1]) if row is not None else None
                    deleted = bool(row[2]) if row is not None else True
                    _check_expected(update, version, deleted)
                    if update.operation == "delete" and (row is None or deleted):
                        raise SharedStateError(
                            f"shared-state entry {update.scope}:{update.key} "
                            "does not exist",
                            code="state.entry.missing",
                            conflict=True,
                        )
                    if update.operation == "set" and (row is None or deleted):
                        count = int(
                            connection.execute(
                                """
                                SELECT COUNT(*) FROM shared_state
                                WHERE namespace = ? AND deleted = 0
                                """,
                                (namespace,),
                            ).fetchone()[0]
                        )
                        if count >= access.limits[update.scope]:
                            raise SharedStateError(
                                f"{update.scope} shared state reached its entry limit",
                                code="state.capacity.exceeded",
                                conflict=True,
                            )
                    next_version = (version or 0) + 1
                    is_deleted = update.operation == "delete"
                    value_json = None if is_deleted else json.dumps(update.value)
                    now = self._clock()
                    connection.execute(
                        """
                        INSERT INTO shared_state (
                            namespace, scope, key, value_json, version, deleted,
                            updated_by, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(namespace, key) DO UPDATE SET
                            scope = excluded.scope,
                            value_json = excluded.value_json,
                            version = excluded.version,
                            deleted = excluded.deleted,
                            updated_by = excluded.updated_by,
                            updated_at = excluded.updated_at
                        """,
                        (
                            namespace,
                            update.scope,
                            update.key,
                            value_json,
                            next_version,
                            int(is_deleted),
                            access.agent_id,
                            now.isoformat(),
                        ),
                    )
                    changes.append(
                        SharedStateChange(
                            scope=update.scope,
                            operation=update.operation,
                            key=update.key,
                            value=None if is_deleted else update.value,
                            version=next_version,
                        )
                    )
                connection.commit()
            except SharedStateError:
                connection.rollback()
                raise
            except (sqlite3.Error, ValueError, TypeError) as error:
                connection.rollback()
                raise SharedStateError(
                    f"could not update shared state: {error}",
                    code="state.write.failed",
                ) from error
        return tuple(changes)

    def close(self) -> None:
        with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()

    def _prepare_file(self) -> None:
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.path.exists() or self.path.is_symlink():
                status = self.path.lstat()
                if not stat.S_ISREG(status.st_mode) or stat.S_IMODE(
                    status.st_mode
                ) & 0o077:
                    raise SharedStateError(
                        f"shared-state store {self.path} is not an owner-only "
                        "regular file",
                        code="state.path.unsafe",
                    )
                return
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags, 0o600)
            os.close(descriptor)
        except SharedStateError:
            raise
        except OSError as error:
            raise SharedStateError(
                f"could not prepare shared-state store {self.path}: {error}",
                code="state.open.failed",
            ) from error

    def _require_open(self) -> sqlite3.Connection:
        with self._lock:
            if self._connection is None:
                raise SharedStateError(
                    "shared-state store is closed",
                    code="state.closed",
                )
            return self._connection
