"""Versioned normalized records for native bounded decisions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from mininet_ai.specification.models import StrictModel

BOUNDED_DECISION_VERSION = "mininet-ai/bounded-decision/v1alpha1"
AGENT_EXECUTION_VERSION = "mininet-ai/agent-execution/v1alpha1"


class BoundedDecisionRecord(StrictModel):
    api_version: Literal["mininet-ai/bounded-decision/v1alpha1"] = Field(
        default=BOUNDED_DECISION_VERSION, alias="apiVersion"
    )
    run_id: str = Field(alias="runId", min_length=1)
    agent_id: str = Field(alias="agentId", min_length=1)
    invocation_id: str = Field(alias="invocationId", min_length=1)
    correlation_id: str = Field(alias="correlationId", min_length=1)
    scenario: dict[str, Any]
    effective_configuration: dict[str, Any] = Field(alias="effectiveConfiguration")
    request: dict[str, Any]
    response: dict[str, Any]
    selected_choice: str = Field(alias="selectedChoice", min_length=1)
    model_provenance: dict[str, Any] = Field(alias="modelProvenance")
    timings: dict[str, Any]
    gaps: tuple[str, ...] = ()


class AgentExecutionRecord(StrictModel):
    api_version: Literal["mininet-ai/agent-execution/v1alpha1"] = Field(
        default=AGENT_EXECUTION_VERSION, alias="apiVersion"
    )
    execution_kind: Literal["agno", "bounded-decision"] = Field(alias="executionKind")
    outcome: Literal["succeeded", "failed"]
    runtime: dict[str, Any]
    agno_run_id: None = Field(default=None, alias="agnoRunId")
    session_id: None = Field(default=None, alias="sessionId")
    metrics: None = None
