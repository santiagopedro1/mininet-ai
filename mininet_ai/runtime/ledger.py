"""Persistent experiment run manifests and append-only records."""

from __future__ import annotations

import os
import sqlite3
import stat
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, ConfigDict, Field, JsonValue, TypeAdapter

from mininet_ai.audit.contracts import AuditEvent, AuditEventType
from mininet_ai.errors import MininetAIError
from mininet_ai.runtime.contracts import RuntimeEvent
from mininet_ai.specification.models import StrictModel

if TYPE_CHECKING:
    from mininet_ai.compiler import DeploymentPlan


RUN_LEDGER_CONTRACT_VERSION: Literal["mininet-ai/run-ledger/v1alpha1"] = (
    "mininet-ai/run-ledger/v1alpha1"
)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class LedgerError(MininetAIError):
    """A run ledger operation could not be completed safely."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class PluginManifest(StrictModel):
    group: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str | None = Field(default=None, min_length=1)
    source_digest: str | None = Field(
        default=None,
        alias="sourceDigest",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )


class RunManifest(StrictModel):
    """Reproducibility metadata captured before runtime work begins."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    contract_version: Literal["mininet-ai/run-ledger/v1alpha1"] = Field(
        default=RUN_LEDGER_CONTRACT_VERSION,
        alias="contractVersion",
    )
    run_id: str = Field(alias="runId", min_length=1)
    plan_digest: str = Field(
        alias="planDigest",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    substrate: str = Field(min_length=1)
    created_at: AwareDatetime = Field(alias="createdAt")
    specification: dict[str, JsonValue]
    deployment_plan: dict[str, JsonValue] = Field(alias="deploymentPlan")
    plugins: tuple[PluginManifest, ...] = ()
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def from_plan(
        cls,
        run_id: str,
        plan: DeploymentPlan,
        *,
        created_at: AwareDatetime,
        plugins: tuple[PluginManifest, ...] = (),
        configuration: dict[str, JsonValue] | None = None,
    ) -> RunManifest:
        return cls(
            runId=run_id,
            planDigest=plan.digest,
            substrate=plan.substrate,
            createdAt=created_at,
            specification=_JSON_OBJECT.validate_python(plan.snapshot),
            deploymentPlan=_JSON_OBJECT.validate_python(
                plan.model_dump(mode="json", by_alias=True)
            ),
            plugins=plugins,
            configuration=configuration or {},
        )


class LedgerRecordCategory(StrEnum):
    RUN_LIFECYCLE = "run.lifecycle"
    RUNTIME_EVENT = "runtime.event"
    AUDIT_EVENT = "audit.event"
    INVOCATION = "invocation"
    ACTION = "action"
    METRIC = "metric"


class LedgerEntry(StrictModel):
    """One pending append to a run history."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    run_id: str = Field(alias="runId", min_length=1)
    category: LedgerRecordCategory
    type: str = Field(min_length=1)
    recorded_at: AwareDatetime = Field(alias="recordedAt")
    correlation_id: str | None = Field(
        default=None,
        alias="correlationId",
        min_length=1,
    )
    causation_id: str | None = Field(
        default=None,
        alias="causationId",
        min_length=1,
    )
    data: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def from_runtime_event(cls, event: RuntimeEvent) -> LedgerEntry:
        return cls(
            runId=event.run_id,
            category=LedgerRecordCategory.RUNTIME_EVENT,
            type=event.type,
            recordedAt=event.observed_at,
            correlationId=event.correlation_id or event.event_id,
            causationId=event.causation_id,
            data=_JSON_OBJECT.validate_python(
                event.model_dump(mode="json", by_alias=True)
            ),
        )

    @classmethod
    def from_audit_event(cls, event: AuditEvent) -> LedgerEntry:
        category = LedgerRecordCategory.AUDIT_EVENT
        if event.type in {
            AuditEventType.AGENT_STARTED,
            AuditEventType.AGENT_COMPLETED,
            AuditEventType.AGENT_FAILED,
        }:
            category = LedgerRecordCategory.INVOCATION
        elif event.type in {
            AuditEventType.CAPABILITY_STARTED,
            AuditEventType.CAPABILITY_COMPLETED,
            AuditEventType.CAPABILITY_FAILED,
        }:
            category = LedgerRecordCategory.ACTION
        return cls(
            runId=event.run_id,
            category=category,
            type=event.type.value,
            recordedAt=event.recorded_at,
            correlationId=event.invocation_id,
            data=_JSON_OBJECT.validate_python(
                event.model_dump(mode="json", by_alias=True)
            ),
        )


class LedgerRecord(LedgerEntry):
    contract_version: Literal["mininet-ai/run-ledger/v1alpha1"] = Field(
        default=RUN_LEDGER_CONTRACT_VERSION,
        alias="contractVersion",
    )
    sequence: int = Field(ge=1)


@runtime_checkable
class RunLedger(Protocol):
    """Durable storage for one or more experiment run histories."""

    def create_run(self, manifest: RunManifest) -> None:
        """Persist a new run manifest exactly once."""
        ...

    def get_run(self, run_id: str) -> RunManifest:
        """Return the persisted run manifest."""
        ...

    def append(self, entry: LedgerEntry) -> LedgerRecord:
        """Append one immutable record and assign its per-run sequence."""
        ...

    def records(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[LedgerRecord, ...]:
        """Read records in sequence order after an exclusive cursor."""
        ...

    def close(self) -> None:
        """Flush and close the ledger; repeated calls are safe."""
        ...


class LedgerEventSink:
    """Append normalized runtime events to a run ledger."""

    def __init__(self, ledger: RunLedger) -> None:
        self._ledger = ledger

    def write(self, event: RuntimeEvent) -> None:
        self._ledger.append(LedgerEntry.from_runtime_event(event))


class LedgerAuditSink:
    """Adapt a run ledger to the existing synchronous audit-sink interface."""

    def __init__(self, ledger: RunLedger) -> None:
        self._ledger = ledger

    def write(self, event: AuditEvent) -> None:
        self._ledger.append(LedgerEntry.from_audit_event(event))


class SQLiteRunLedger:
    """Thread-safe SQLite implementation of the run-ledger interface."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = None
        self._prepare_file()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, check_same_thread=False)
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, 1}:
                raise LedgerError(
                    f"run ledger schema version {version} is unsupported",
                    code="ledger.version.unsupported",
                )
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA temp_store = MEMORY")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    plan_digest TEXT NOT NULL,
                    substrate TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    manifest_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    type TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    correlation_id TEXT,
                    record_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
                """
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
            self._connection = connection
        except LedgerError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise LedgerError(
                f"could not open run ledger {self.path}: {error}",
                code="ledger.open.failed",
            ) from error

    def create_run(self, manifest: RunManifest) -> None:
        connection = self._require_open()
        try:
            with self._lock, connection:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, plan_digest, substrate, created_at, manifest_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.run_id,
                        manifest.plan_digest,
                        manifest.substrate,
                        manifest.created_at.isoformat(),
                        manifest.model_dump_json(by_alias=True),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise LedgerError(
                f"run {manifest.run_id!r} already exists in the ledger",
                code="ledger.run.duplicate",
            ) from error
        except sqlite3.Error as error:
            raise LedgerError(
                f"could not persist run {manifest.run_id!r}: {error}",
                code="ledger.write.failed",
            ) from error

    def get_run(self, run_id: str) -> RunManifest:
        connection = self._require_open()
        try:
            with self._lock:
                row = connection.execute(
                    "SELECT manifest_json FROM runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise LedgerError(
                f"could not read run {run_id!r}: {error}",
                code="ledger.read.failed",
            ) from error
        if row is None:
            raise LedgerError(
                f"run {run_id!r} does not exist in the ledger",
                code="ledger.run.unknown",
            )
        try:
            return RunManifest.model_validate_json(row[0])
        except ValueError as error:
            raise LedgerError(
                f"run {run_id!r} has an invalid persisted manifest: {error}",
                code="ledger.data.invalid",
            ) from error

    def append(self, entry: LedgerEntry) -> LedgerRecord:
        connection = self._require_open()
        with self._lock:
            try:
                connection.execute("BEGIN IMMEDIATE")
                exists = connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?",
                    (entry.run_id,),
                ).fetchone()
                if exists is None:
                    connection.rollback()
                    raise LedgerError(
                        f"run {entry.run_id!r} does not exist in the ledger",
                        code="ledger.run.unknown",
                    )
                row = connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM records
                    WHERE run_id = ?
                    """,
                    (entry.run_id,),
                ).fetchone()
                sequence = int(row[0])
                record = LedgerRecord(
                    **entry.model_dump(mode="python", by_alias=True),
                    sequence=sequence,
                )
                connection.execute(
                    """
                    INSERT INTO records (
                        run_id, sequence, category, type, recorded_at,
                        correlation_id, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.run_id,
                        record.sequence,
                        record.category.value,
                        record.type,
                        record.recorded_at.isoformat(),
                        record.correlation_id,
                        record.model_dump_json(by_alias=True),
                    ),
                )
                connection.commit()
                return record
            except LedgerError:
                raise
            except (sqlite3.Error, ValueError) as error:
                connection.rollback()
                raise LedgerError(
                    f"could not append run record: {error}",
                    code="ledger.write.failed",
                ) from error

    def records(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[LedgerRecord, ...]:
        if after_sequence < 0:
            raise ValueError("ledger cursor cannot be negative")
        if limit is not None and limit <= 0:
            raise ValueError("ledger record limit must be positive")
        connection = self._require_open()
        query = (
            "SELECT record_json FROM records "
            "WHERE run_id = ? AND sequence > ? ORDER BY sequence"
        )
        parameters: tuple[object, ...] = (run_id, after_sequence)
        if limit is not None:
            query += " LIMIT ?"
            parameters += (limit,)
        try:
            with self._lock:
                exists = connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if exists is None:
                    raise LedgerError(
                        f"run {run_id!r} does not exist in the ledger",
                        code="ledger.run.unknown",
                    )
                rows = connection.execute(query, parameters).fetchall()
            return tuple(LedgerRecord.model_validate_json(row[0]) for row in rows)
        except LedgerError:
            raise
        except (sqlite3.Error, ValueError) as error:
            raise LedgerError(
                f"could not read records for run {run_id!r}: {error}",
                code="ledger.read.failed",
            ) from error

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
                mode = stat.S_IMODE(status.st_mode)
                if not stat.S_ISREG(status.st_mode) or mode & 0o077:
                    raise LedgerError(
                        f"run ledger {self.path} is not an owner-only regular file",
                        code="ledger.path.unsafe",
                    )
                return
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags, 0o600)
            os.close(descriptor)
        except LedgerError:
            raise
        except OSError as error:
            raise LedgerError(
                f"could not prepare run ledger {self.path}: {error}",
                code="ledger.open.failed",
            ) from error

    def _require_open(self) -> sqlite3.Connection:
        with self._lock:
            if self._connection is None:
                raise LedgerError(
                    "run ledger is closed",
                    code="ledger.closed",
                )
            return self._connection
