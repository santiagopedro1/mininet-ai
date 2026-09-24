"""Bounded in-process delivery for normalized runtime events."""

from __future__ import annotations

import time
from collections import deque
from threading import Condition
from typing import Protocol, runtime_checkable

from mininet_ai.errors import MininetAIError
from mininet_ai.runtime.contracts import RuntimeEvent


class EventBusError(MininetAIError):
    """A runtime event could not be safely delivered."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class RuntimeEventBus(Protocol):
    """Bounded FIFO event delivery used by continuous runtime producers."""

    @property
    def capacity(self) -> int:
        """Maximum number of queued events."""
        ...

    @property
    def size(self) -> int:
        """Current number of queued events."""
        ...

    def publish(self, event: RuntimeEvent) -> None:
        """Publish immediately or raise when delivery cannot be accepted."""
        ...

    def receive(self, *, timeout_seconds: float | None = None) -> RuntimeEvent | None:
        """Return the next event, or ``None`` on timeout or exhausted closure."""
        ...

    def close(self) -> None:
        """Reject new events and wake blocked consumers."""
        ...


class InMemoryRuntimeEventBus:
    """Thread-safe bounded FIFO bus for one continuous-runtime process."""

    def __init__(self, *, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("event bus capacity must be positive")
        self._capacity = capacity
        self._events: deque[RuntimeEvent] = deque()
        self._closed = False
        self._condition = Condition()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._condition:
            return len(self._events)

    def publish(self, event: RuntimeEvent) -> None:
        with self._condition:
            if self._closed:
                raise EventBusError(
                    "runtime event bus is closed",
                    code="runtime.event-bus.closed",
                )
            if len(self._events) >= self._capacity:
                raise EventBusError(
                    "runtime event bus is full",
                    code="runtime.event-bus.full",
                )
            self._events.append(event)
            self._condition.notify()

    def receive(self, *, timeout_seconds: float | None = None) -> RuntimeEvent | None:
        if timeout_seconds is not None and timeout_seconds < 0:
            raise ValueError("event receive timeout cannot be negative")
        deadline = (
            None
            if timeout_seconds is None
            else time.monotonic() + timeout_seconds
        )
        with self._condition:
            while not self._events:
                if self._closed:
                    return None
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._events.popleft()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
