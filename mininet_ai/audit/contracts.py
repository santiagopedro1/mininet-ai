"""Machine-readable contract for one agent-runtime audit record."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field, JsonValue

from mininet_ai.specification.models import StrictModel


AUDIT_CONTRACT_VERSION: Literal["mininet-ai/audit/v1alpha1"] = (
    "mininet-ai/audit/v1alpha1"
)


class AuditEventType(StrEnum):
    AGENT_STARTED = "agent.invocation.started"
    AGENT_COMPLETED = "agent.invocation.completed"
    AGENT_FAILED = "agent.invocation.failed"
    MODEL_STARTED = "model.request.started"
    MODEL_COMPLETED = "model.request.completed"
    MODEL_FAILED = "model.request.failed"
    CAPABILITY_STARTED = "capability.execution.started"
    CAPABILITY_COMPLETED = "capability.execution.completed"
    CAPABILITY_FAILED = "capability.execution.failed"


class AuditEvent(StrictModel):
    """One correlated, append-only record from an agent invocation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    contract_version: Literal["mininet-ai/audit/v1alpha1"] = Field(
        default=AUDIT_CONTRACT_VERSION,
        alias="contractVersion",
    )
    recorded_at: AwareDatetime = Field(alias="recordedAt")
    type: AuditEventType
    run_id: str = Field(alias="runId", min_length=1)
    invocation_id: str = Field(alias="invocationId", min_length=1)
    agent_id: str = Field(alias="agentId", min_length=1)
    data: dict[str, JsonValue] = Field(default_factory=dict)
