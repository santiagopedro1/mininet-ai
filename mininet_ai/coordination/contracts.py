"""Versioned contracts for messages delivered through coordination graphs."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field, JsonValue, model_validator

from mininet_ai.specification.models import StrictModel


COORDINATION_MESSAGE_CONTRACT_VERSION: Literal[
    "mininet-ai/coordination-message/v1alpha1"
] = "mininet-ai/coordination-message/v1alpha1"
COORDINATION_MESSAGE_SCHEMA_ID = (
    "urn:mininet-ai:schema:v1alpha1:coordination-message"
)


class CoordinationMessageKind(StrEnum):
    INTENT = "intent"
    DELEGATION = "delegation"
    RESULT = "result"


class CoordinationIntentPayload(StrictModel):
    """Intent data carried by entry and delegation messages."""

    intent: str = Field(min_length=1)
    requested_agent_id: str = Field(alias="requestedAgentId", min_length=1)
    delegation_id: str | None = Field(
        default=None,
        alias="delegationId",
        min_length=1,
    )
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CoordinationMessage(StrictModel):
    """One immutable, correlated message accepted for at-most-once delivery."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        frozen=True,
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": COORDINATION_MESSAGE_SCHEMA_ID,
        },
    )

    contract_version: Literal[
        "mininet-ai/coordination-message/v1alpha1"
    ] = Field(
        default=COORDINATION_MESSAGE_CONTRACT_VERSION,
        alias="contractVersion",
    )
    message_id: str = Field(alias="messageId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    kind: CoordinationMessageKind
    source_agent_id: str | None = Field(
        default=None,
        alias="sourceAgentId",
        min_length=1,
    )
    target_agent_id: str = Field(alias="targetAgentId", min_length=1)
    correlation_id: str = Field(alias="correlationId", min_length=1)
    causation_id: str | None = Field(
        default=None,
        alias="causationId",
        min_length=1,
    )
    parent_invocation_id: str | None = Field(
        default=None,
        alias="parentInvocationId",
        min_length=1,
    )
    triggering_event_id: str | None = Field(
        default=None,
        alias="triggeringEventId",
        min_length=1,
    )
    created_at: AwareDatetime = Field(alias="createdAt")
    hop_count: int = Field(default=0, alias="hopCount", ge=0)
    traversed_message_ids: tuple[str, ...] = Field(
        default=(),
        alias="traversedMessageIds",
    )
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def identities_match_message_kind(self) -> CoordinationMessage:
        if (
            self.kind != CoordinationMessageKind.INTENT
            and self.source_agent_id is None
        ):
            raise ValueError(f"{self.kind.value} messages require sourceAgentId")
        if self.source_agent_id == self.target_agent_id:
            raise ValueError("coordination messages cannot target their source agent")
        return self

    @model_validator(mode="after")
    def traversal_is_consistent(self) -> CoordinationMessage:
        if len(set(self.traversed_message_ids)) != len(
            self.traversed_message_ids
        ):
            raise ValueError("traversedMessageIds must be unique")
        if self.message_id in self.traversed_message_ids:
            raise ValueError("a message cannot appear in its own traversal history")
        if self.hop_count != len(self.traversed_message_ids):
            raise ValueError("hopCount must equal the traversal history length")
        if self.hop_count > 0 and self.causation_id is None:
            raise ValueError("messages after the entry hop require causationId")
        return self
