"""Bounded in-process delivery for immutable coordination messages."""

from __future__ import annotations

import time
from collections import deque
from threading import Condition
from typing import NoReturn, Protocol, runtime_checkable

from mininet_ai.coordination.contracts import CoordinationMessage
from mininet_ai.errors import MininetAIError


class MessageChannelError(MininetAIError):
    """A coordination message could not be safely accepted for delivery."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        message_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message_id = message_id


@runtime_checkable
class MessageChannel(Protocol):
    """Bounded at-most-once delivery within one runtime process."""

    @property
    def capacity(self) -> int:
        """Maximum number of queued messages."""
        ...

    @property
    def size(self) -> int:
        """Current number of queued messages."""
        ...

    def send(self, message: CoordinationMessage) -> None:
        """Accept a message immediately or raise a typed delivery error."""
        ...

    def receive(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> CoordinationMessage | None:
        """Return the next message, or ``None`` on timeout or drained closure."""
        ...

    def close(self) -> None:
        """Reject new messages and wake blocked consumers."""
        ...


class MessageSink(Protocol):
    """Synchronous observer for messages accepted by a channel."""

    def write(self, message: CoordinationMessage) -> None: ...


class InMemoryMessageChannel:
    """Thread-safe bounded FIFO channel with lifetime duplicate suppression."""

    def __init__(
        self,
        *,
        capacity: int,
        sink: MessageSink | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("message channel capacity must be positive")
        self._capacity = capacity
        self._messages: deque[CoordinationMessage] = deque()
        self._accepted_ids: set[str] = set()
        self._closed = False
        self._condition = Condition()
        self._sink = sink

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._condition:
            return len(self._messages)

    def send(self, message: CoordinationMessage) -> None:
        with self._condition:
            if self._closed:
                self._reject(
                    message,
                    "coordination message channel is closed",
                    code="coordination.message-channel.closed",
                )
            if message.message_id in self._accepted_ids:
                self._reject(
                    message,
                    f"coordination message {message.message_id!r} was already accepted",
                    code="coordination.message-channel.duplicate",
                )
            if len(self._messages) >= self._capacity:
                self._reject(
                    message,
                    "coordination message channel is full",
                    code="coordination.message-channel.full",
                )
            if self._sink is not None:
                self._sink.write(message)
            self._accepted_ids.add(message.message_id)
            self._messages.append(message)
            self._condition.notify()

    def receive(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> CoordinationMessage | None:
        if timeout_seconds is not None and timeout_seconds < 0:
            raise ValueError("message receive timeout cannot be negative")
        deadline = (
            None
            if timeout_seconds is None
            else time.monotonic() + timeout_seconds
        )
        with self._condition:
            while not self._messages:
                if self._closed:
                    return None
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._messages.popleft()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @staticmethod
    def _reject(
        message: CoordinationMessage,
        reason: str,
        *,
        code: str,
    ) -> NoReturn:
        raise MessageChannelError(
            reason,
            code=code,
            message_id=message.message_id,
        )
