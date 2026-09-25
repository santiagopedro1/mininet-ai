"""Bounded observation windows and detector-event production."""

from __future__ import annotations

import math
import re
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from statistics import fmean, pstdev
from threading import Event, Lock, Thread
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError

from mininet_ai.compiler import DeploymentPlan
from mininet_ai.compiler.models import AgentInstance
from mininet_ai.errors import MininetAIError
from mininet_ai.runtime.contracts import (
    DetectorEventPayload,
    ObservationRecordedPayload,
    RuntimeEvent,
    RuntimeEventType,
    TelemetryPipelineIssue,
    TelemetryPipelineReport,
)
from mininet_ai.specification.models import (
    AnomalyDetector,
    ObservationPolicy,
    ThresholdDetector,
)
from mininet_ai.substrates import ObservationQuery, SubstrateRuntime


Clock = Callable[[], datetime]
EventIdFactory = Callable[[], str]
_DURATION_FACTORS = {"us": 0.000001, "ms": 0.001, "s": 1.0}
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _event_id() -> str:
    return f"event-{uuid4()}"


def _duration_seconds(value: str) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(us|ms|s)", value)
    if match is None:
        raise ValueError(f"invalid duration {value!r}")
    return float(match.group(1)) * _DURATION_FACTORS[match.group(2)]


class TelemetryPipelineError(MininetAIError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class TelemetryPipelineState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


@runtime_checkable
class RuntimeEventPublisher(Protocol):
    def publish(self, event: RuntimeEvent) -> None: ...


@dataclass(frozen=True)
class _Sample:
    observed_at: datetime
    values: dict[str, JsonValue]


@dataclass(frozen=True)
class _PolicyBinding:
    identity: str
    agent: AgentInstance
    policy: ObservationPolicy


class TelemetryPipeline:
    """Sample declared observations and publish aggregated detector events."""

    def __init__(
        self,
        plan: DeploymentPlan,
        run_id: str,
        substrate: SubstrateRuntime,
        publisher: RuntimeEventPublisher,
        *,
        clock: Clock = _utc_now,
        event_id_factory: EventIdFactory = _event_id,
    ) -> None:
        if not run_id:
            raise ValueError("telemetry run id cannot be empty")
        self._run_id = run_id
        self._substrate = substrate
        self._publisher = publisher
        self._clock = clock
        self._event_id_factory = event_id_factory
        self._bindings = tuple(
            _PolicyBinding(
                identity=f"{agent.id}:{index}:{policy.observation}",
                agent=agent,
                policy=policy,
            )
            for agent in plan.agents
            for index, policy in enumerate(agent.observation_policies)
        )
        self._by_agent_observation: dict[
            tuple[str, str], list[_PolicyBinding]
        ] = {}
        for binding in self._bindings:
            self._by_agent_observation.setdefault(
                (binding.agent.id, binding.policy.observation), []
            ).append(binding)
        self._windows: dict[str, deque[_Sample]] = {
            binding.identity: deque() for binding in self._bindings
        }
        self._anomaly_history: dict[
            tuple[str, str, str], deque[tuple[datetime, float]]
        ] = {}
        self._last_emitted: dict[tuple[str, str, str], datetime] = {}
        self._stop = Event()
        self._threads: list[Thread] = []
        self._state = TelemetryPipelineState.CREATED
        self._state_lock = Lock()
        self._data_lock = Lock()
        self._report_lock = Lock()
        self._sequence_lock = Lock()
        self._sequence = 0
        self._counts = {
            "samples": 0,
            "observations": 0,
            "detector_events": 0,
            "failures": 0,
        }
        self._issues: list[TelemetryPipelineIssue] = []

    @property
    def state(self) -> TelemetryPipelineState:
        with self._state_lock:
            return self._state

    def start(self) -> None:
        """Start one sampler for each compiled agent observation policy."""

        with self._state_lock:
            if self._state != TelemetryPipelineState.CREATED:
                raise TelemetryPipelineError(
                    f"telemetry pipeline is already {self._state.value}",
                    code="telemetry.lifecycle.invalid",
                )
            self._state = TelemetryPipelineState.RUNNING
        for binding in self._bindings:
            thread = Thread(
                target=self._sampling_loop,
                args=(binding,),
                name=f"mininet-ai-telemetry-{binding.identity}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def sample_once(
        self,
        agent_id: str,
        observation: str,
    ) -> tuple[RuntimeEvent, ...]:
        """Synchronously sample matching policies for deterministic callers."""

        bindings = self._by_agent_observation.get((agent_id, observation))
        if not bindings:
            raise TelemetryPipelineError(
                f"agent {agent_id!r} has no policy for observation {observation!r}",
                code="telemetry.policy.unknown",
            )
        emitted = []
        for binding in bindings:
            emitted.extend(self._sample(binding))
        return tuple(emitted)

    def stop(self, *, timeout_seconds: float = 30) -> TelemetryPipelineReport:
        """Stop all samplers and return their aggregate report."""

        if timeout_seconds <= 0:
            raise ValueError("telemetry stop timeout must be positive")
        with self._state_lock:
            if self._state == TelemetryPipelineState.STOPPED:
                return self.report()
            if self._state != TelemetryPipelineState.RUNNING:
                raise TelemetryPipelineError(
                    f"cannot stop telemetry pipeline from {self._state.value}",
                    code="telemetry.lifecycle.invalid",
                )
            self._state = TelemetryPipelineState.STOPPING
        self._stop.set()
        deadline = time.monotonic() + timeout_seconds
        for thread in self._threads:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                thread.join(remaining)
            if thread.is_alive():
                raise TelemetryPipelineError(
                    f"sampler {thread.name!r} did not stop before the deadline",
                    code="telemetry.stop.timeout",
                )
        with self._state_lock:
            self._state = TelemetryPipelineState.STOPPED
        return self.report()

    def report(self) -> TelemetryPipelineReport:
        with self._report_lock:
            return TelemetryPipelineReport(
                samples=self._counts["samples"],
                observations=self._counts["observations"],
                detectorEvents=self._counts["detector_events"],
                failures=self._counts["failures"],
                issues=tuple(self._issues),
            )

    def _sampling_loop(self, binding: _PolicyBinding) -> None:
        every = _duration_seconds(binding.policy.every)
        while not self._stop.is_set():
            try:
                self._sample(binding)
            except Exception as error:
                self._issue(
                    "telemetry.sample.failed",
                    str(error) or type(error).__name__,
                    binding,
                )
            if self._stop.wait(every):
                return

    def _sample(self, binding: _PolicyBinding) -> tuple[RuntimeEvent, ...]:
        query = ObservationQuery(
            name=binding.policy.observation,
            targets=binding.agent.attachment.targets,
        )
        result = self._substrate.observe(self._run_id, query)
        if result.run_id != self._run_id or result.query != query:
            raise TelemetryPipelineError(
                "substrate returned mismatched observation identity",
                code="telemetry.observation.identity-mismatch",
            )
        try:
            values = _JSON_OBJECT.validate_python(result.values)
        except ValidationError as error:
            raise TelemetryPipelineError(
                f"substrate returned non-JSON observation values: {error}",
                code="telemetry.observation.invalid",
            ) from error
        if not set(values).issubset(query.targets):
            raise TelemetryPipelineError(
                "substrate returned values outside the observation targets",
                code="telemetry.observation.target-mismatch",
            )
        with self._data_lock:
            window = self._windows[binding.identity]
            if window and result.observed_at < window[-1].observed_at:
                raise TelemetryPipelineError(
                    "substrate observation timestamp moved backwards",
                    code="telemetry.observation.out-of-order",
                )
            window.append(_Sample(result.observed_at, values))
            window_seconds = _duration_seconds(binding.policy.window)
            every_seconds = _duration_seconds(binding.policy.every)
            max_samples = math.ceil(window_seconds / every_seconds) + 1
            cutoff = result.observed_at - timedelta(seconds=window_seconds)
            while window and window[0].observed_at < cutoff:
                window.popleft()
            while len(window) > max_samples:
                window.popleft()
            samples = tuple(window)
            aggregated = _aggregate_samples(samples, binding.policy.aggregation)
            events = self._events(binding, samples, aggregated)
        with self._report_lock:
            self._counts["samples"] += 1
        emitted = []
        for event in events:
            try:
                self._publisher.publish(event)
            except Exception as error:
                self._issue(
                    "telemetry.publish.failed",
                    str(error) or type(error).__name__,
                    binding,
                )
                continue
            emitted.append(event)
            with self._report_lock:
                if event.type == RuntimeEventType.OBSERVATION_RECORDED:
                    self._counts["observations"] += 1
                else:
                    self._counts["detector_events"] += 1
        return tuple(emitted)

    def _events(
        self,
        binding: _PolicyBinding,
        samples: tuple[_Sample, ...],
        aggregated: dict[str, JsonValue],
    ) -> tuple[RuntimeEvent, ...]:
        ended_at = samples[-1].observed_at
        started_at = samples[0].observed_at
        observed_at = max(self._clock(), ended_at)
        observation_id = self._event_id_factory()
        payload = ObservationRecordedPayload(
            agentId=binding.agent.id,
            observation=binding.policy.observation,
            targets=binding.agent.attachment.targets,
            values=aggregated,
            aggregation=binding.policy.aggregation,
            sampleCount=len(samples),
            windowStartedAt=started_at,
            windowEndedAt=ended_at,
        )
        observation_event = RuntimeEvent(
            eventId=observation_id,
            runId=self._run_id,
            type=RuntimeEventType.OBSERVATION_RECORDED,
            source="telemetry",
            subject=binding.agent.id,
            occurredAt=ended_at,
            observedAt=observed_at,
            sequence=self._next_sequence(),
            payload=payload.model_dump(mode="json", by_alias=True),
        )
        detector_events = self._detector_events(
            binding,
            samples,
            aggregated,
            observation_event,
        )
        return (observation_event, *detector_events)

    def _detector_events(
        self,
        binding: _PolicyBinding,
        samples: tuple[_Sample, ...],
        aggregated: dict[str, JsonValue],
        observation_event: RuntimeEvent,
    ) -> tuple[RuntimeEvent, ...]:
        events = []
        ended_at = samples[-1].observed_at
        started_at = samples[0].observed_at
        for detector in binding.policy.detectors:
            for target in binding.agent.attachment.targets:
                value = _numeric_path(aggregated.get(target), detector.path)
                if value is None:
                    continue
                operator = None
                threshold = None
                baseline_mean = None
                baseline_stddev = None
                score = None
                if isinstance(detector, ThresholdDetector):
                    if not _compare(value, detector.operator, detector.value):
                        continue
                    operator = detector.operator
                    threshold = detector.value
                    detector_type = "threshold"
                    sample_count = len(samples)
                else:
                    history_key = (binding.identity, detector.name, target)
                    history = self._anomaly_history.setdefault(
                        history_key,
                        deque(),
                    )
                    cutoff = ended_at - timedelta(
                        seconds=_duration_seconds(binding.policy.window)
                    )
                    while history and history[0][0] < cutoff:
                        history.popleft()
                    baseline = [item[1] for item in history]
                    anomaly = _anomaly(value, baseline, detector)
                    history.append((ended_at, value))
                    if anomaly is None:
                        continue
                    mean, stddev, score = anomaly
                    baseline_mean = mean
                    baseline_stddev = stddev
                    detector_type = "anomaly"
                    sample_count = len(baseline)
                cooldown_key = (binding.identity, detector.name, target)
                last = self._last_emitted.get(cooldown_key)
                cooldown = _duration_seconds(detector.cooldown)
                if last is not None and (ended_at - last).total_seconds() < cooldown:
                    continue
                self._last_emitted[cooldown_key] = ended_at
                detector_payload = DetectorEventPayload(
                    agentId=binding.agent.id,
                    observation=binding.policy.observation,
                    detector=detector.name,
                    detectorType=detector_type,
                    aggregation=binding.policy.aggregation,
                    target=target,
                    path=detector.path,
                    value=value,
                    operator=operator,
                    threshold=threshold,
                    baselineMean=baseline_mean,
                    baselineStddev=baseline_stddev,
                    score=score,
                    sampleCount=sample_count,
                    windowStartedAt=started_at,
                    windowEndedAt=ended_at,
                )
                events.append(
                    RuntimeEvent(
                        eventId=self._event_id_factory(),
                        runId=self._run_id,
                        type=detector.event,
                        source="telemetry",
                        subject=target,
                        occurredAt=ended_at,
                        observedAt=max(self._clock(), ended_at),
                        sequence=self._next_sequence(),
                        correlationId=observation_event.event_id,
                        causationId=observation_event.event_id,
                        payload=detector_payload.model_dump(
                            mode="json",
                            by_alias=True,
                            exclude_none=True,
                        ),
                    )
                )
        return tuple(events)

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            sequence = self._sequence
            self._sequence += 1
            return sequence

    def _issue(
        self,
        code: str,
        message: str,
        binding: _PolicyBinding,
    ) -> None:
        issue = TelemetryPipelineIssue(
            code=code,
            message=message,
            agentId=binding.agent.id,
            observation=binding.policy.observation,
        )
        with self._report_lock:
            self._issues.append(issue)
            self._counts["failures"] += 1


def _flatten(value: JsonValue, prefix: str = "") -> dict[str, JsonValue]:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            result.update(_flatten(child, path))
        return result
    return {prefix: value} if prefix else {}


def _assign(result: dict[str, JsonValue], path: str, value: JsonValue) -> None:
    parts = path.split(".")
    current: dict[str, JsonValue] = result
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _aggregate_samples(
    samples: tuple[_Sample, ...],
    aggregation: str,
) -> dict[str, JsonValue]:
    paths: dict[tuple[str, str], list[JsonValue]] = {}
    for sample in samples:
        for target, value in sample.values.items():
            for path, leaf in _flatten(value).items():
                paths.setdefault((target, path), []).append(leaf)
    result: dict[str, JsonValue] = {}
    for (target, path), values in sorted(paths.items()):
        numeric = [
            float(value)
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if aggregation == "latest" or len(numeric) != len(values):
            aggregated: JsonValue = values[-1]
        elif aggregation == "minimum":
            aggregated = min(numeric)
        elif aggregation == "maximum":
            aggregated = max(numeric)
        elif aggregation == "mean":
            aggregated = fmean(numeric)
        elif aggregation == "sum":
            aggregated = sum(numeric)
        else:
            raise AssertionError(f"unsupported aggregation {aggregation!r}")
        target_values = result.setdefault(target, {})
        if not isinstance(target_values, dict):
            raise AssertionError("target aggregation must be an object")
        _assign(target_values, path, aggregated)
    return result


def _numeric_path(value: JsonValue | None, path: str) -> float | None:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return None
    number = float(current)
    return number if math.isfinite(number) else None


def _compare(value: float, operator: str, expected: float) -> bool:
    operations = {
        "gt": value > expected,
        "gte": value >= expected,
        "lt": value < expected,
        "lte": value <= expected,
        "eq": value == expected,
        "ne": value != expected,
    }
    return operations[operator]


def _anomaly(
    value: float,
    baseline: list[float],
    detector: AnomalyDetector,
) -> tuple[float, float, float] | None:
    if len(baseline) < detector.min_samples:
        return None
    mean = fmean(baseline)
    stddev = pstdev(baseline)
    if stddev == 0:
        score = 0 if value == mean else sys.float_info.max
    else:
        score = abs(value - mean) / stddev
    if score < detector.sensitivity:
        return None
    return mean, stddev, score
