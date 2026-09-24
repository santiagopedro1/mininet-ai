"""Atomic ownership records for recoverable Mininet/OVS runs."""

from __future__ import annotations

import errno
import fcntl
import os
import tempfile
from pathlib import Path
from typing import Any, Literal, TextIO

from pydantic import Field

from mininet_ai.specification.models import StrictModel
from mininet_ai.substrates.runtime import RunInfo

STATE_API_VERSION: Literal["mininet-ai/runtime-state/v1alpha2"] = (
    "mininet-ai/runtime-state/v1alpha2"
)
LEGACY_STATE_API_VERSION: Literal["mininet-ai/runtime-state/v1alpha1"] = (
    "mininet-ai/runtime-state/v1alpha1"
)
DEFAULT_STATE_DIRECTORY = Path("/run/mininet-ai")
DEFAULT_LOCK_PATH = Path("/run/lock/mininet-ai-runtime.lock")


class StateStoreError(Exception):
    """Persistent run state could not be read or changed safely."""


class StateLockHeld(StateStoreError):
    """Another process currently owns the Mininet runtime lock."""


def _boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="utf-8"
    ).strip()


def _process_start_ticks(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    fields_after_command = stat[stat.rfind(")") + 2 :].split()
    return int(fields_after_command[19])


class ProcessOwner(StrictModel):
    pid: int = Field(gt=0)
    boot_id: str = Field(min_length=1)
    start_ticks: int = Field(ge=0)

    @classmethod
    def current(cls) -> ProcessOwner:
        return cls.for_pid(os.getpid())

    @classmethod
    def for_pid(cls, pid: int) -> ProcessOwner:
        return cls(
            pid=pid,
            boot_id=_boot_id(),
            start_ticks=_process_start_ticks(pid),
        )

    def is_alive(self) -> bool:
        try:
            return (
                self.boot_id == _boot_id()
                and self.start_ticks == _process_start_ticks(self.pid)
            )
        except (OSError, IndexError, ValueError):
            return False


class PersistedRun(StrictModel):
    api_version: Literal[
        "mininet-ai/runtime-state/v1alpha1",
        "mininet-ai/runtime-state/v1alpha2",
    ] = Field(alias="apiVersion")
    run: RunInfo
    owner: ProcessOwner
    plan: dict[str, Any]
    process_groups: tuple[ProcessOwner, ...] = Field(
        default=(), alias="processGroups"
    )


class PersistedStoppedRun(StrictModel):
    api_version: Literal["mininet-ai/runtime-state/v1alpha2"] = Field(
        alias="apiVersion"
    )
    run: RunInfo
    plan: dict[str, Any]


class RunStateStore:
    """One atomic active-run record protected by an advisory process lock."""

    def __init__(
        self,
        state_directory: Path = DEFAULT_STATE_DIRECTORY,
        lock_path: Path = DEFAULT_LOCK_PATH,
    ) -> None:
        self.state_directory = Path(state_directory)
        self.state_path = self.state_directory / "mininet-ovs.json"
        self.stopped_path = self.state_directory / "mininet-ovs.stopped.json"
        self.lock_path = Path(lock_path)
        self._lock_stream: TextIO | None = None

    @property
    def acquired(self) -> bool:
        return self._lock_stream is not None

    def is_locked(self) -> bool:
        if self.acquired:
            return True
        try:
            self.acquire()
        except StateLockHeld:
            return True
        self.release()
        return False

    def acquire(self) -> None:
        if self.acquired:
            return
        self.lock_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        stream = self.lock_path.open("a+", encoding="utf-8")
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            stream.close()
            raise StateLockHeld("the Mininet runtime lock is held") from error
        self._lock_stream = stream

    def release(self) -> None:
        stream = self._lock_stream
        self._lock_stream = None
        if stream is None or stream.closed:
            return
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()

    def __del__(self) -> None:
        try:
            self.release()
        except (OSError, ValueError):
            pass

    def read(self) -> PersistedRun | None:
        if not self.state_path.exists():
            return None
        try:
            return PersistedRun.model_validate_json(
                self.state_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise StateStoreError(
                f"invalid runtime state file {self.state_path}: {error}"
            ) from error

    def write(self, record: PersistedRun) -> None:
        self._write_json(self.state_path, record)

    def write_stopped(self, record: PersistedStoppedRun) -> None:
        self._write_json(self.stopped_path, record)

    def _write_json(
        self,
        destination: Path,
        record: PersistedRun | PersistedStoppedRun,
    ) -> None:
        self._require_lock()
        self.state_directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        os.chmod(self.state_directory, 0o750)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".mininet-ovs.",
            suffix=".tmp",
            dir=self.state_directory,
        )
        temporary = Path(temporary_name)
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(record.model_dump_json(by_alias=True, indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            directory = os.open(self.state_directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise StateStoreError(
                f"could not write runtime state {destination}: {error}"
            ) from error

    def read_stopped(self) -> PersistedStoppedRun | None:
        if not self.stopped_path.exists():
            return None
        try:
            return PersistedStoppedRun.model_validate_json(
                self.stopped_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise StateStoreError(
                f"invalid stopped-run state file {self.stopped_path}: {error}"
            ) from error

    def clear_stopped(self) -> None:
        self._require_lock()
        try:
            self.stopped_path.unlink(missing_ok=True)
            self.state_directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as error:
            if error.errno != errno.ENOTEMPTY:
                raise StateStoreError(
                    f"could not remove stopped-run state: {error}"
                ) from error

    def clear(self) -> None:
        self._require_lock()
        try:
            for temporary in self.state_directory.glob(
                ".mininet-ovs.*.tmp"
            ):
                temporary.unlink()
            self.state_path.unlink(missing_ok=True)
            self.state_directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as error:
            if error.errno != errno.ENOTEMPTY:
                raise StateStoreError(
                    f"could not remove runtime state directory "
                    f"{self.state_directory}: {error}"
                ) from error

    def _require_lock(self) -> None:
        if not self.acquired:
            raise StateStoreError("the runtime state lock is not held")
