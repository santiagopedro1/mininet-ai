"""Normalized records produced by the Agno execution module."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from mininet_ai.sdk import AgentResponse
from mininet_ai.sdk.contracts import TokenMetrics
from mininet_ai.specification.models import StrictModel


class ModelRunMetrics(TokenMetrics):
    role: str = Field(min_length=1)
    model: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    provider_metrics: dict[str, JsonValue] = Field(
        default_factory=dict,
        alias="providerMetrics",
    )


class AgentRunMetrics(TokenMetrics):
    duration_seconds: float | None = Field(
        default=None,
        alias="durationSeconds",
        ge=0,
    )
    time_to_first_token_seconds: float | None = Field(
        default=None,
        alias="timeToFirstTokenSeconds",
        ge=0,
    )
    models: tuple[ModelRunMetrics, ...] = ()
    additional: dict[str, JsonValue] = Field(default_factory=dict)


class AgnoMemorySettings(StrictModel):
    """Effective Agno memory behavior recorded with an invocation."""

    local_state: bool = Field(alias="localState")
    local_max_entries: int | None = Field(
        default=None,
        alias="localMaxEntries",
        ge=1,
    )
    conversation_history: bool = Field(alias="conversationHistory")
    history_messages: int | None = Field(
        default=None,
        alias="historyMessages",
        ge=1,
    )
    session_summaries: bool = Field(alias="sessionSummaries")
    learned_memory: bool = Field(alias="learnedMemory")
    learned_scope: Literal["run", "agent"] | None = Field(
        default=None,
        alias="learnedScope",
    )
    learned_mode: Literal["automatic", "agentic"] | None = Field(
        default=None,
        alias="learnedMode",
    )


class AgnoExecutionResult(StrictModel):
    """Agno output translated into Mininet-owned, serializable values."""

    agno_run_id: str = Field(alias="agnoRunId", min_length=1)
    runtime_version: str = Field(alias="runtimeVersion", min_length=1)
    session_id: str | None = Field(default=None, alias="sessionId", min_length=1)
    user_id: str | None = Field(default=None, alias="userId", min_length=1)
    model: str | None = Field(default=None, min_length=1)
    model_provider: str | None = Field(
        default=None,
        alias="modelProvider",
        min_length=1,
    )
    model_queueing_seconds: float = Field(
        default=0,
        alias="modelQueueingSeconds",
        ge=0,
    )
    response: AgentResponse
    metrics: AgentRunMetrics = Field(default_factory=AgentRunMetrics)
    memory: AgnoMemorySettings
