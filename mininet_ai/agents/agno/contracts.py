"""Normalized records produced by the Agno execution module."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from mininet_ai.sdk import AgentResponse
from mininet_ai.specification.models import StrictModel


class TokenMetrics(StrictModel):
    input_tokens: int = Field(default=0, alias="inputTokens", ge=0)
    output_tokens: int = Field(default=0, alias="outputTokens", ge=0)
    total_tokens: int = Field(default=0, alias="totalTokens", ge=0)
    audio_input_tokens: int = Field(default=0, alias="audioInputTokens", ge=0)
    audio_output_tokens: int = Field(default=0, alias="audioOutputTokens", ge=0)
    audio_total_tokens: int = Field(default=0, alias="audioTotalTokens", ge=0)
    cache_read_tokens: int = Field(default=0, alias="cacheReadTokens", ge=0)
    cache_write_tokens: int = Field(default=0, alias="cacheWriteTokens", ge=0)
    reasoning_tokens: int = Field(default=0, alias="reasoningTokens", ge=0)
    cost: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def totals_cover_reported_tokens(self) -> TokenMetrics:
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("totalTokens cannot be smaller than input plus output")
        if self.audio_total_tokens < (
            self.audio_input_tokens + self.audio_output_tokens
        ):
            raise ValueError(
                "audioTotalTokens cannot be smaller than audio input plus output"
            )
        return self


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
    response: AgentResponse
    metrics: AgentRunMetrics = Field(default_factory=AgentRunMetrics)
    memory: AgnoMemorySettings
