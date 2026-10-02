"""Private, bounded local intent submission to a foreground run owner."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import stat
import struct
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import Field, TypeAdapter, ValidationError

from mininet_ai.errors import MininetAIError
from mininet_ai.runtime.contracts import RuntimeEvent
from mininet_ai.specification.models import StrictModel

if TYPE_CHECKING:
    from mininet_ai.experiment import ExperimentRuntime

DEFAULT_CONTROL_DIRECTORY = None
_MAX_MESSAGE_BYTES = 65_536


class IntentControlError(MininetAIError):
    def __init__(self, message: str, *, code: str = "runtime.control.failed") -> None:
        super().__init__(message)
        self.code = code


class _IntentRequest(StrictModel):
    kind: Literal["submit"] = "submit"
    run_id: str = Field(alias="runId", min_length=1, max_length=256)
    plan_digest: str | None = Field(
        default=None, alias="planDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    agent_id: str = Field(alias="agentId", min_length=1, max_length=512)
    intent: str = Field(min_length=1, max_length=8192)


class _DescribeRequest(StrictModel):
    kind: Literal["describe"] = "describe"
    run_id: str = Field(alias="runId", min_length=1, max_length=256)


class RunDescription(StrictModel):
    """Live owner's identity and compiled manual-intent destinations."""

    run_id: str = Field(alias="runId", min_length=1, max_length=256)
    plan_digest: str = Field(alias="planDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    agents: tuple[str, ...]
    manual_agents: tuple[str, ...] = Field(alias="manualAgents")


_REQUEST = TypeAdapter(
    Annotated[_IntentRequest | _DescribeRequest, Field(discriminator="kind")]
)


def _socket_path(directory: Path, run_id: str) -> Path:
    name = hashlib.sha256(run_id.encode()).hexdigest()[:24]
    return directory.absolute() / name / "control.sock"


def resolve_control_directory(directory: Path | None = None) -> Path:
    """Resolve at execution time; never fall back from invalid XDG configuration."""
    if directory is not None:
        return directory.absolute()
    uid = os.geteuid()
    if uid == 0:
        return Path("/run/mininet-ai/control")
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg is not None:
        parent = Path(xdg)
        if not parent.is_absolute():
            raise IntentControlError(
                "XDG_RUNTIME_DIR must be an absolute private directory"
            )
        try:
            _private_directory(parent, create=False)
        except (OSError, IntentControlError) as error:
            raise IntentControlError(
                f"invalid XDG_RUNTIME_DIR {parent}: {error}; fix or unset XDG_RUNTIME_DIR"
            ) from error
        return parent / "mininet-ai/control"
    return Path(f"/tmp/mininet-ai-{uid}/control")


def _private_directory(directory: Path, *, create: bool) -> None:
    descriptor = _open_directory(directory, create=create)
    os.close(descriptor)


def _open_directory(
    directory: Path,
    *,
    create: bool,
    private_parent: bool = False,
    private_levels: int = 1,
) -> int:
    """Walk without following symlinks, validating every newly owned boundary."""
    path = directory.absolute()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            owned = index >= len(path.parts) - 1 - max(
                private_levels, 2 if private_parent else 1
            )
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
                _check_directory(os.fstat(descriptor), path)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _check_directory(metadata: os.stat_result, directory: Path) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise IntentControlError(
            f"control directory {directory} must be an owner-only directory owned by the current user",
            code="runtime.control.permissions",
        )


def _check_socket_length(path: Path) -> None:
    if len(os.fsencode(path)) >= 108:
        raise IntentControlError(
            f"control socket path is too long: {path}; use a shorter absolute --control-dir"
        )


def _receive(connection: socket.socket, *, deadline: float) -> bytes:
    data = bytearray()
    while len(data) <= _MAX_MESSAGE_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("control request deadline exceeded")
        connection.settimeout(remaining)
        chunk = connection.recv(min(4096, _MAX_MESSAGE_BYTES + 1 - len(data)))
        if not chunk:
            raise IntentControlError(
                "control connection closed before a complete response"
            )
        data.extend(chunk)
        if len(data) > _MAX_MESSAGE_BYTES:
            break
        if b"\n" in chunk:
            return bytes(data).split(b"\n", 1)[0]
    raise IntentControlError("control message exceeds 65536 bytes")


def _send(connection: socket.socket, payload: Mapping[str, object]) -> None:
    data = (
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    )
    if len(data) > _MAX_MESSAGE_BYTES:
        raise IntentControlError("control message exceeds 65536 bytes")
    connection.sendall(data)


class IntentServer:
    """Accept intents for exactly one owner; close intake before draining it."""

    def __init__(
        self,
        owner: ExperimentRuntime,
        directory: Path | None = None,
        *,
        on_submit: Callable[[RuntimeEvent], None] | None = None,
    ) -> None:
        self._owner = owner
        self._directory = resolve_control_directory(directory)
        self._default_directory = directory is None
        self._on_submit = on_submit
        self._stop = Event()
        self._socket: socket.socket | None = None
        self._thread: Thread | None = None
        self._path: Path | None = None
        self._identity: tuple[int, int] | None = None
        self._base_fd: int | None = None
        self._run_fd: int | None = None
        self._run_name: str | None = None
        self._run_identity: tuple[int, int] | None = None
        self._connection_lock = Lock()
        self._active_connection: socket.socket | None = None

    def __enter__(self) -> Self:
        try:
            path = _socket_path(self._directory, self._owner.run.id)
            _check_socket_length(path)
            self._base_fd = _open_directory(
                self._directory,
                create=True,
                private_levels=2 if self._default_directory else 1,
            )
            # Exclusive claim: even stale or colliding endpoints are never adopted.
            os.mkdir(path.parent.name, 0o700, dir_fd=self._base_fd)
            self._run_name = path.parent.name
            metadata = os.stat(
                self._run_name, dir_fd=self._base_fd, follow_symlinks=False
            )
            self._run_identity = (metadata.st_dev, metadata.st_ino)
            self._run_fd = os.open(
                self._run_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self._base_fd,
            )
            metadata = os.fstat(self._run_fd)
            if (metadata.st_dev, metadata.st_ino) != self._run_identity:
                raise IntentControlError("control run directory changed during setup")
            _check_directory(metadata, path.parent)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket = listener
            listener.bind(f"/proc/self/fd/{self._run_fd}/control.sock")
            self._path = path
            metadata = os.stat(
                "control.sock", dir_fd=self._run_fd, follow_symlinks=False
            )
            self._identity = (metadata.st_dev, metadata.st_ino)
            os.chmod("control.sock", 0o600, dir_fd=self._run_fd, follow_symlinks=False)
            listener.listen(8)
            listener.settimeout(0.1)
            thread = Thread(target=self._serve, name="mininet-ai-intents", daemon=True)
            thread.start()
            self._thread = thread
            return self
        except Exception as error:
            self.close()
            if isinstance(error, IntentControlError):
                raise
            raise IntentControlError(
                f"could not open intent endpoint in {self._directory}: {error}"
            ) from error

    def __exit__(self, *error: object) -> None:
        self.close()

    @property
    def path(self) -> Path | None:
        return self._path

    def close(self) -> None:
        self._stop.set()
        with self._connection_lock:
            if self._active_connection is not None:
                try:
                    self._active_connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._path is not None:
            try:
                metadata = os.stat(
                    "control.sock", dir_fd=self._run_fd, follow_symlinks=False
                )
                if (metadata.st_dev, metadata.st_ino) == self._identity:
                    os.unlink("control.sock", dir_fd=self._run_fd)
            except FileNotFoundError:
                pass
            self._path = None
        if self._run_fd is not None:
            os.close(self._run_fd)
            self._run_fd = None
        if self._base_fd is not None:
            try:
                if self._run_name is not None:
                    metadata = os.stat(
                        self._run_name, dir_fd=self._base_fd, follow_symlinks=False
                    )
                    if (metadata.st_dev, metadata.st_ino) == self._run_identity:
                        os.rmdir(self._run_name, dir_fd=self._base_fd)
            except OSError:
                # Nonempty/replaced directories belong to someone else now.
                pass
            finally:
                os.close(self._base_fd)
                self._base_fd = None

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except TimeoutError:
                continue
            with connection:
                with self._connection_lock:
                    if self._stop.is_set():
                        continue
                    self._active_connection = connection
                connection.settimeout(2)
                try:
                    response = self._handle_request(connection)
                    connection.settimeout(2)
                    try:
                        _send(connection, response)
                    except OSError, IntentControlError:
                        # A lost acknowledgement must not undo or replay accepted work.
                        pass
                finally:
                    with self._connection_lock:
                        self._active_connection = None

    def _handle_request(self, connection: socket.socket) -> Mapping[str, object]:
        try:
            _, uid, _ = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if uid != os.geteuid():
                raise IntentControlError("intent client must run as the owner user")
            payload = json.loads(_receive(connection, deadline=time.monotonic() + 2))
            if isinstance(payload, dict):
                payload.setdefault("kind", "submit")
            request = _REQUEST.validate_python(payload)
            run = self._owner.run
            if request.run_id != run.id:
                raise IntentControlError(
                    "intent does not match the owner's run and deployment plan",
                    code="runtime.control.run-mismatch",
                )
            if isinstance(request, _DescribeRequest):
                description = RunDescription(
                    runId=run.id,
                    planDigest=run.plan_digest,
                    agents=self._owner.agent_ids,
                    manualAgents=self._owner.manual_agent_ids,
                )
                return {"describe": description.model_dump(mode="json", by_alias=True)}
            if (
                request.plan_digest is not None
                and request.plan_digest != run.plan_digest
            ):
                raise IntentControlError(
                    "intent does not match the owner's run and deployment plan",
                    code="runtime.control.run-mismatch",
                )
            event = self._owner.submit_intent(
                request.agent_id, request.intent, source="terminal"
            )
        except (MininetAIError, ValueError, OSError) as error:
            return {
                "error": {
                    "code": getattr(error, "code", "runtime.control.invalid-request"),
                    "message": str(error),
                }
            }
        if self._on_submit is not None:
            try:
                self._on_submit(event)
            except Exception:
                logging.getLogger(__name__).exception(
                    "could not log accepted terminal intent"
                )
        return {"event": event.model_dump(mode="json", by_alias=True)}


class IntentClient:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = resolve_control_directory(directory)
        self._default_directory = directory is None

    def submit(
        self,
        run_id: str,
        agent_id: str,
        intent: str,
        *,
        plan_digest: str | None = None,
        timeout_seconds: float = 5,
    ) -> RuntimeEvent:
        """Return the accepted event, not an agent execution result; never retry."""
        try:
            request = _IntentRequest(
                runId=run_id, planDigest=plan_digest, agentId=agent_id, intent=intent
            )
            response = self._request(run_id, request, timeout_seconds=timeout_seconds)
            return RuntimeEvent.model_validate(response["event"])
        except IntentControlError:
            raise
        except (ValueError, KeyError, TypeError, ValidationError) as error:
            raise IntentControlError(
                f"invalid intent response or request: {error}"
            ) from error

    def describe(self, run_id: str, *, timeout_seconds: float = 5) -> RunDescription:
        """Discover agents from the live owner without reading the experiment YAML."""
        try:
            request = _DescribeRequest(runId=run_id)
            response = self._request(run_id, request, timeout_seconds=timeout_seconds)
            description = RunDescription.model_validate(response["describe"])
            if description.run_id != run_id:
                raise IntentControlError(
                    "description does not match requested run",
                    code="runtime.control.run-mismatch",
                )
            return description
        except IntentControlError:
            raise
        except (ValueError, KeyError, TypeError, ValidationError) as error:
            raise IntentControlError(
                f"invalid description response or request: {error}"
            ) from error

    def _request(
        self,
        run_id: str,
        request: _IntentRequest | _DescribeRequest,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        try:
            path = _socket_path(self._directory, run_id)
            _check_socket_length(path)
            descriptor = _open_directory(
                path.parent,
                create=False,
                private_levels=3 if self._default_directory else 2,
            )
            try:
                metadata = os.stat(
                    "control.sock", dir_fd=descriptor, follow_symlinks=False
                )
                if (
                    not stat.S_ISSOCK(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) & 0o077
                ):
                    raise IntentControlError(
                        "intent endpoint must be a private socket owned by the current user"
                    )
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    deadline = time.monotonic() + timeout_seconds
                    connection.settimeout(timeout_seconds)
                    connection.connect(f"/proc/self/fd/{descriptor}/control.sock")
                    _, uid, _ = struct.unpack(
                        "3i",
                        connection.getsockopt(
                            socket.SOL_SOCKET, socket.SO_PEERCRED, 12
                        ),
                    )
                    if uid != os.geteuid():
                        raise IntentControlError(
                            "intent owner must run as the client user"
                        )
                    _send(
                        connection, request.model_dump(by_alias=True, exclude_none=True)
                    )
                    response = json.loads(_receive(connection, deadline=deadline))
            finally:
                os.close(descriptor)
            if not isinstance(response, dict):
                raise IntentControlError("control response must be an object")
            if "error" in response:
                raise IntentControlError(
                    response["error"]["message"], code=response["error"]["code"]
                )
            return response
        except IntentControlError:
            raise
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as error:
            raise IntentControlError(
                f"could not contact run {run_id!r} at {_socket_path(self._directory, run_id)}: {error}; use the owner's user and control directory; do not retry intent submissions automatically after a timeout",
                code="runtime.control.unavailable",
            ) from error
