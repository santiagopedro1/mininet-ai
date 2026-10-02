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

DEFAULT_CONTROL_DIRECTORY = Path(".mininet-ai/control")
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
    name = hashlib.sha256(run_id.encode()).hexdigest()[:24] + ".sock"
    return directory.absolute() / name


def _private_directory(directory: Path, *, create: bool) -> None:
    if create:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise IntentControlError(
            f"control directory {directory} must be an owner-only directory owned by the current user",
            code="runtime.control.permissions",
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
        directory: Path = DEFAULT_CONTROL_DIRECTORY,
        *,
        on_submit: Callable[[RuntimeEvent], None] | None = None,
    ) -> None:
        self._owner = owner
        self._directory = directory
        self._on_submit = on_submit
        self._stop = Event()
        self._socket: socket.socket | None = None
        self._thread: Thread | None = None
        self._path: Path | None = None
        self._identity: tuple[int, int] | None = None
        self._connection_lock = Lock()
        self._active_connection: socket.socket | None = None

    def __enter__(self) -> Self:
        try:
            _private_directory(self._directory, create=True)
            path = _socket_path(self._directory, self._owner.run.id)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket = listener
            listener.bind(str(path))
            self._path = path
            metadata = path.lstat()
            self._identity = (metadata.st_dev, metadata.st_ino)
            path.chmod(0o600)
            listener.listen(8)
            listener.settimeout(0.1)
            self._thread = Thread(
                target=self._serve, name="mininet-ai-intents", daemon=True
            )
            self._thread.start()
            return self
        except (OSError, ValueError, IntentControlError) as error:
            self.close()
            if isinstance(error, IntentControlError):
                raise
            raise IntentControlError(
                f"could not open intent endpoint: {error}"
            ) from error

    def __exit__(self, *error: object) -> None:
        self.close()

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
                metadata = self._path.lstat()
                if (metadata.st_dev, metadata.st_ino) == self._identity:
                    self._path.unlink()
            except FileNotFoundError:
                pass
            self._path = None

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
    def __init__(self, directory: Path = DEFAULT_CONTROL_DIRECTORY) -> None:
        self._directory = directory

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
            _private_directory(self._directory, create=False)
            path = _socket_path(self._directory, run_id)
            metadata = path.lstat()
            if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise IntentControlError(
                    "intent endpoint must be a socket owned by the current user"
                )
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                deadline = time.monotonic() + timeout_seconds
                connection.settimeout(timeout_seconds)
                connection.connect(str(path))
                _send(connection, request.model_dump(by_alias=True, exclude_none=True))
                response = json.loads(_receive(connection, deadline=deadline))
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
                f"could not submit intent to run {run_id!r}: {error}; use the owner's user and control directory; do not retry automatically after a timeout",
                code="runtime.control.unavailable",
            ) from error
