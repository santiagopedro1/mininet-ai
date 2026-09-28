"""Versioned contracts for events delivered to the continuous runtime."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, TypeVar

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from mininet_ai.specification.models import StrictModel
from mininet_ai.sdk import AgentInvocationResult


RUNTIME_EVENT_CONTRACT_VERSION: Literal["mininet-ai/runtime-event/v1alpha1"] = (
    "mininet-ai/runtime-event/v1alpha1"
)
RUNTIME_EVENT_SCHEMA_ID = "urn:mininet-ai:schema:v1alpha1:runtime-event"


class RuntimeEventType(StrEnum):
    MANUAL_INTENT = "intent.manual"
    INTERVAL_ELAPSED = "trigger.interval.elapsed"
    OBSERVATION_RECORDED = "observation.recorded"
    AGENT_LIFECYCLE = "agent.lifecycle.changed"
    RUNTIME_FAILURE = "runtime.failure"


class ManualIntentPayload(StrictModel):
    agent_id: str = Field(alias="agentId", min_length=1)
    intent: str = Field(min_length=1)


class IntervalElapsedPayload(StrictModel):
    agent_id: str = Field(alias="agentId", min_length=1)
    trigger: str = Field(min_length=1)
    scheduled_at: AwareDatetime = Field(alias="scheduledAt")


class ObservationRecordedPayload(StrictModel):
    observation: str = Field(min_length=1)
    targets: tuple[str, ...] = Field(min_length=1)
    values: dict[str, JsonValue] = Field(default_factory=dict)
    agent_id: str | None = Field(default=None, alias="agentId", min_length=1)
    aggregation: Literal["latest", "minimum", "maximum", "mean", "sum"] | None = (
        None
    )
    sample_count: int | None = Field(default=None, alias="sampleCount", ge=1)
    window_started_at: AwareDatetime | None = Field(
        default=None,
        alias="windowStartedAt",
    )
    window_ended_at: AwareDatetime | None = Field(
        default=None,
        alias="windowEndedAt",
    )

    @model_validator(mode="after")
    def window_is_complete_and_ordered(self) -> ObservationRecordedPayload:
        if (self.window_started_at is None) != (self.window_ended_at is None):
            raise ValueError("observation window requires both timestamps")
        if (
            self.window_started_at is not None
            and self.window_ended_at is not None
            and self.window_ended_at < self.window_started_at
        ):
            raise ValueError("observation window cannot end before it starts")
        return self


class DetectorEventPayload(StrictModel):
    """Why one observation detector emitted its configured event."""

    agent_id: str = Field(alias="agentId", min_length=1)
    observation: str = Field(min_length=1)
    detector: str = Field(min_length=1)
    detector_type: Literal["threshold", "anomaly"] = Field(alias="detectorType")
    aggregation: Literal["latest", "minimum", "maximum", "mean", "sum"]
    target: str = Field(min_length=1)
    path: str = Field(min_length=1)
    value: float
    operator: Literal["gt", "gte", "lt", "lte", "eq", "ne"] | None = None
    threshold: float | None = None
    baseline_mean: float | None = Field(default=None, alias="baselineMean")
    baseline_stddev: float | None = Field(default=None, alias="baselineStddev", ge=0)
    score: float | None = Field(default=None, ge=0)
    sample_count: int = Field(alias="sampleCount", ge=1)
    window_started_at: AwareDatetime = Field(alias="windowStartedAt")
    window_ended_at: AwareDatetime = Field(alias="windowEndedAt")

    @model_validator(mode="after")
    def details_match_detector(self) -> DetectorEventPayload:
        if self.window_ended_at < self.window_started_at:
            raise ValueError("detector window cannot end before it starts")
        if self.detector_type == "threshold":
            if self.operator is None or self.threshold is None:
                raise ValueError("threshold detector requires operator and threshold")
        elif (
            self.baseline_mean is None
            or self.baseline_stddev is None
            or self.score is None
        ):
            raise ValueError("anomaly detector requires baseline and score")
        return self


class TelemetryPipelineIssue(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, alias="agentId", min_length=1)
    observation: str | None = Field(default=None, min_length=1)


class TelemetryPipelineReport(StrictModel):
    samples: int = Field(default=0, ge=0)
    observations: int = Field(default=0, ge=0)
    detector_events: int = Field(default=0, alias="detectorEvents", ge=0)
    failures: int = Field(default=0, ge=0)
    issues: tuple[TelemetryPipelineIssue, ...] = ()


class AgentLifecycleState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    RESTARTING = "restarting"
    FAILED = "failed"


class AgentLifecyclePayload(StrictModel):
    agent_id: str = Field(alias="agentId", min_length=1)
    state: AgentLifecycleState
    previous_state: AgentLifecycleState | None = Field(
        default=None,
        alias="previousState",
    )
    attempt: int = Field(default=0, ge=0)


class AgentLifecycleTransition(StrictModel):
    """One supervised agent state change retained in a runtime report."""

    agent_id: str = Field(alias="agentId", min_length=1)
    state: AgentLifecycleState
    previous_state: AgentLifecycleState | None = Field(
        default=None,
        alias="previousState",
    )
    attempt: int = Field(default=0, ge=0)
    recorded_at: AwareDatetime = Field(alias="recordedAt")


class RuntimeFailurePayload(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, alias="agentId", min_length=1)
    recoverable: bool = False


class ContinuousInvocationRecord(StrictModel):
    """One event-to-agent invocation completed by the continuous runtime."""

    event_id: str = Field(alias="eventId", min_length=1)
    agent_id: str = Field(alias="agentId", min_length=1)
    trigger: str = Field(min_length=1)
    result: AgentInvocationResult


class ContinuousRuntimeIssue(StrictModel):
    """A trigger delivery that was skipped, dropped, or rejected."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    event_id: str | None = Field(default=None, alias="eventId", min_length=1)
    agent_id: str | None = Field(default=None, alias="agentId", min_length=1)
    trigger: str | None = Field(default=None, min_length=1)


class ContinuousRuntimeReport(StrictModel):
    """Thread-safe runtime counters and completed invocation records."""

    received: int = Field(default=0, ge=0)
    matched: int = Field(default=0, ge=0)
    enqueued: int = Field(default=0, ge=0)
    coalesced: int = Field(default=0, ge=0)
    dropped: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    invocations: tuple[ContinuousInvocationRecord, ...] = ()
    issues: tuple[ContinuousRuntimeIssue, ...] = ()
    lifecycle: tuple[AgentLifecycleTransition, ...] = ()


PayloadModel = TypeVar("PayloadModel", bound=BaseModel)


class RuntimeEvent(StrictModel):
    """One immutable, source-ordered event delivered within an experiment run."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        frozen=True,
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": RUNTIME_EVENT_SCHEMA_ID,
        },
    )

    contract_version: Literal["mininet-ai/runtime-event/v1alpha1"] = Field(
        default=RUNTIME_EVENT_CONTRACT_VERSION,
        alias="contractVersion",
    )
    event_id: str = Field(alias="eventId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    occurred_at: AwareDatetime = Field(alias="occurredAt")
    observed_at: AwareDatetime = Field(alias="observedAt")
    sequence: int = Field(ge=0)
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
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timing_is_monotonic(self) -> RuntimeEvent:
        if self.observed_at < self.occurred_at:
            raise ValueError("observedAt cannot precede occurredAt")
        return self

    def payload_as(self, model: type[PayloadModel]) -> PayloadModel:
        """Validate this event's payload as a known event-specific contract."""

        return model.model_validate(self.payload)
