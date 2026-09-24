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


class RuntimeFailurePayload(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, alias="agentId", min_length=1)
    recoverable: bool = False


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
