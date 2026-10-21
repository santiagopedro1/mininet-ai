"""Opt-in transition evidence, independent of SDK/UTC compatibility metrics.

An orchestration scope owns one recorder and immutable execution context. Workers
must capture that binding when work is created, never consult a current-fault
pointer. Sink delivery order is deliberately independent of transition time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock, RLock
from time import monotonic_ns
from typing import Literal, Self
from uuid import uuid4


class Phase(StrEnum):
    SETUP = "setup"
    INJECTION = "injection"
    OBSERVATION = "observation"
    DETECTOR = "detector"
    SCHEDULING = "scheduling"
    CONTEXT = "context"
    REASONING = "reasoning"
    DECISION_TO_ACTION = "decision_to_action"
    ACTION_ATTEMPT = "action_attempt"
    ADMISSION = "admission"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    STOPPING = "stopping"
    CLEANUP = "cleanup"


Transition = Literal[
    "operation_started",
    "operation_completed",
    "operation_failed",
    "operation_cancelled",
]


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    trial_id: str
    fault_id: str
    agent_id: str | None = None
    observation_id: str | None = None
    observation_source: str | None = None
    event_id: str | None = None
    invocation_id: str | None = None
    proposal_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    scheduling_operation_id: str | None = None
    substrate_run_id: str | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (self.run_id, self.trial_id, self.fault_id)
        ):
            raise ValueError("run, trial, and fault identities must be explicit")


@dataclass(frozen=True)
class Stamp:
    clock_domain_id: str
    tick_ns: str | None


@dataclass(frozen=True)
class PhaseRecord:
    record_id: str
    collector_seq: int
    context: ExecutionContext
    stamp: Stamp
    utc: datetime
    operation_id: str
    phase: Phase
    transition: Transition
    outcome: str | None = None
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecorderLoss:
    context: ExecutionContext
    reason: str
    operation_id: str | None = None


@dataclass(frozen=True)
class Duration:
    availability: Literal["available", "unavailable"]
    value_seconds: float | None
    reason: str | None = None
    quality_flags: tuple[str, ...] = ()


def interval(
    start: Stamp | None,
    end: Stamp | None,
    *,
    reversed_reason: str = "timestamp_order_invalid",
    trustworthy: bool = True,
) -> Duration:
    """Never repair ticks or reconstruct them from aggregate durations."""
    flags: list[str] = []
    if not trustworthy:
        flags.append("missing_evidence")
    if start is None or end is None:
        flags.append("incomplete_phase")
    else:
        if (
            not isinstance(start.clock_domain_id, str)
            or not start.clock_domain_id
            or start.clock_domain_id != end.clock_domain_id
        ):
            flags.append("clock_domain_mismatch")
        values: list[int] = []
        for stamp in (start, end):
            tick = stamp.tick_ns
            if tick is None:
                flags.append("missing_evidence")
            elif (
                not isinstance(tick, str)
                or not tick.isascii()
                or not tick.isdecimal()
                or (len(tick) > 1 and tick.startswith("0"))
            ):
                flags.append("malformed_tick")
            else:
                try:
                    values.append(int(tick))
                except ValueError:
                    flags.append("malformed_tick")
        if (
            len(values) == 2
            and isinstance(start.clock_domain_id, str)
            and start.clock_domain_id
            and start.clock_domain_id == end.clock_domain_id
        ):
            delta = values[1] - values[0]
            if delta < 0:
                flags.append(reversed_reason)
            elif not flags:
                try:
                    return Duration("available", delta / 1_000_000_000)
                except OverflowError:
                    flags.append("malformed_tick")
    return Duration("unavailable", None, flags[0], tuple(dict.fromkeys(flags)))


class PhaseRecorder:
    """Thread-safe recorder with sticky, inspectable loss (including sink errors).

    A failing recorder does not change network execution. Orchestration must check
    ``losses`` before trusting measurements or required observation evidence.
    """

    def __init__(
        self,
        clock_domain_id: str,
        *,
        clock: Callable[[], int] = monotonic_ns,
        sink: Callable[[PhaseRecord], None] | None = None,
    ) -> None:
        if not isinstance(clock_domain_id, str) or not clock_domain_id:
            raise ValueError("clock domain cannot be empty")
        self.clock_domain_id = clock_domain_id
        self._clock = clock
        self._sink = sink
        self._lock = RLock()
        self._clock_lock = Lock()
        self._records: list[PhaseRecord] = []
        self._losses: list[RecorderLoss] = []
        self.requests = ProviderRequestLedger(self)

    def stamp(self, context: ExecutionContext) -> Stamp:
        try:
            with self._clock_lock:
                tick = self._clock()
            if type(tick) is not int or tick < 0:
                raise ValueError(
                    "clock must return a nonnegative integer nanosecond tick"
                )
            return Stamp(self.clock_domain_id, str(tick))
        except Exception as error:
            self.report_loss(context, f"clock_failure: {error}")
            return Stamp(self.clock_domain_id, None)

    def report_loss(
        self,
        context: ExecutionContext,
        reason: str,
        operation_id: str | None = None,
    ) -> None:
        with self._lock:
            self._losses.append(RecorderLoss(context, reason, operation_id))

    @property
    def records(self) -> tuple[PhaseRecord, ...]:
        with self._lock:
            return tuple(self._records)

    @property
    def losses(self) -> tuple[RecorderLoss, ...]:
        with self._lock:
            return tuple(self._losses)

    def trustworthy(self, context: ExecutionContext) -> bool:
        with self._lock:
            return not any(
                loss.context.run_id == context.run_id
                and loss.context.trial_id == context.trial_id
                for loss in self._losses
            )

    def measurement(
        self,
        context: ExecutionContext,
        start: Stamp | None,
        end: Stamp | None,
        *,
        reversed_reason: str = "timestamp_order_invalid",
    ) -> Duration:
        return interval(
            start,
            end,
            reversed_reason=reversed_reason,
            trustworthy=self.trustworthy(context),
        )

    def record(
        self,
        context: ExecutionContext,
        operation_id: str,
        phase: Phase,
        transition: Transition,
        *,
        outcome: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> PhaseRecord:
        phase = Phase(phase)
        if transition not in {
            "operation_started",
            "operation_completed",
            "operation_failed",
            "operation_cancelled",
        }:
            raise ValueError("unknown operation transition")
        stamp = self.stamp(context)  # Before collector lock or sink delivery.
        with self._lock:
            record = PhaseRecord(
                str(uuid4()),
                len(self._records),
                context,
                stamp,
                datetime.now(UTC),
                operation_id,
                phase,
                transition,
                outcome,
                evidence_ids,
            )
            self._records.append(record)
            if self._sink is not None:
                try:
                    self._sink(record)
                except Exception as error:
                    self.report_loss(context, f"sink_failure: {error}", operation_id)
            return record


@dataclass(frozen=True)
class RecordingBinding:
    recorder: PhaseRecorder
    context: ExecutionContext


_binding: ContextVar[RecordingBinding | None] = ContextVar(
    "phase_recording", default=None
)


def current_binding() -> RecordingBinding | None:
    return _binding.get()


@contextmanager
def recording_scope(
    binding: RecordingBinding | None, **identities: str | None
) -> Iterator[None]:
    if binding is not None and identities:
        binding = replace(binding, context=replace(binding.context, **identities))
    token = _binding.set(binding)
    try:
        yield
    finally:
        _binding.reset(token)


class Operation:
    def __init__(self, phase: Phase, binding: RecordingBinding | None = None) -> None:
        self.binding = binding if binding is not None else current_binding()
        self.id = str(uuid4())
        self.phase = phase
        self.terminal = False
        self._lock = RLock()
        self.emit("operation_started")

    def emit(self, transition: Transition, outcome: str | None = None) -> None:
        if self.binding is not None:
            self.binding.recorder.record(
                self.binding.context,
                self.id,
                self.phase,
                transition,
                outcome=outcome,
            )

    def finish(self, outcome: str = "succeeded", *, failed: bool = False) -> None:
        with self._lock:
            if not self.terminal:
                self.terminal = True
                self.emit(
                    "operation_failed" if failed else "operation_completed", outcome
                )

    def cancel(self, reason: str) -> None:
        with self._lock:
            if not self.terminal:
                self.terminal = True
                self.emit("operation_cancelled", reason)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, kind, error, traceback) -> None:
        if isinstance(
            error, (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit)
        ):
            self.cancel(type(error).__name__)
        elif error is not None:
            self.finish(type(error).__name__, failed=True)
        else:
            self.finish()


@dataclass(frozen=True)
class ProviderRequest:
    request_id: str
    provider: str
    context: ExecutionContext
    started: Stamp
    provider_request_id: str | None = None
    terminal: Stamp | None = None
    terminal_evidence: Literal["normal_response", "remote_termination"] | None = None
    terminal_source: str | None = None
    cancel_requested: Stamp | None = None
    local_error: str | None = None
    interrupted_at: Stamp | None = None


class ProviderRequestLedger:
    """Local interruption never clears a request. Only adapters supply terminal proof."""

    def __init__(self, recorder: PhaseRecorder) -> None:
        self._recorder = recorder
        self._lock = RLock()
        self._requests: dict[str, ProviderRequest] = {}
        self._history: list[ProviderRequest] = []

    def _retain(self, request: ProviderRequest) -> None:
        # Called under the ledger lock: retain repeated cancellations/failures,
        # not just the most recent state for each request.
        self._requests[request.request_id] = request
        self._history.append(request)

    def start(
        self,
        context: ExecutionContext,
        provider: str,
        *,
        provider_request_id: str | None = None,
    ) -> str:
        request = ProviderRequest(
            str(uuid4()),
            provider,
            context,
            self._recorder.stamp(context),
            provider_request_id,
        )
        with self._lock:
            self._retain(request)
        return request.request_id

    def terminal(
        self,
        request_id: str,
        *,
        evidence: Literal["normal_response", "remote_termination"],
        source: str,
        provider_request_id: str | None = None,
    ) -> None:
        if evidence not in {"normal_response", "remote_termination"} or not source:
            raise ValueError("authoritative adapter evidence required")
        with self._lock:
            request = self._requests[request_id]
        stamp = self._recorder.stamp(request.context)
        with self._lock:
            request = self._requests[request_id]
            if request.terminal_evidence is not None:
                raise ValueError("request already terminal")
            self._retain(
                replace(
                    request,
                    terminal=stamp,
                    terminal_evidence=evidence,
                    terminal_source=source,
                    provider_request_id=provider_request_id
                    or request.provider_request_id,
                )
            )

    def interrupted(
        self, request_id: str, reason: str, *, cancelled: bool = False
    ) -> None:
        with self._lock:
            request = self._requests[request_id]
        stamp = self._recorder.stamp(request.context)
        with self._lock:
            request = self._requests[request_id]
            self._retain(
                replace(
                    request,
                    local_error=reason,
                    interrupted_at=stamp,
                    cancel_requested=stamp if cancelled else request.cancel_requested,
                )
            )

    def cancel(self, request_id: str) -> None:
        """Record a local cancellation request, without claiming it stopped generation."""
        with self._lock:
            request = self._requests[request_id]
        stamp = self._recorder.stamp(request.context)
        with self._lock:
            request = self._requests[request_id]
            self._retain(
                replace(
                    request,
                    cancel_requested=stamp,
                )
            )

    @property
    def requests(self) -> tuple[ProviderRequest, ...]:
        with self._lock:
            return tuple(self._requests.values())

    @property
    def history(self) -> tuple[ProviderRequest, ...]:
        """Append-only request-state snapshots, including every local interruption."""
        with self._lock:
            return tuple(self._history)

    @property
    def outstanding(self) -> tuple[ProviderRequest, ...]:
        return tuple(r for r in self.requests if r.terminal_evidence is None)

    @property
    def contained(self) -> bool:
        return not self.outstanding and not self._recorder.losses
