"""Versioned contracts for one-shot agent and capability execution."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from mininet_ai.specification.models import (
    AttachmentLayer,
    ResourceKind,
    StrictModel,
)
from mininet_ai.substrates.runtime import ActionResult, ActionStatus


AGENT_RUNTIME_CONTRACT_VERSION = "mininet-ai/agent-runtime/v1alpha1"


class AgentRuntimeIssue(StrictModel):
    """Stable failure information returned by the agent runtime."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, alias="agentId")
    target: str | None = None


class InvocationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


class SharedStateEntry(StrictModel):
    """One versioned value visible in an agent's authorized shared scope."""

    value: JsonValue
    version: int = Field(ge=1)
    updated_by: str = Field(alias="updatedBy", min_length=1)
    updated_at: AwareDatetime = Field(alias="updatedAt")


class SharedStateSnapshot(StrictModel):
    """Shared operational state supplied to one agent invocation."""

    allowed_scopes: tuple[Literal["run", "deployment"], ...] = Field(
        default=(),
        alias="allowedScopes",
    )
    run: dict[str, SharedStateEntry] = Field(default_factory=dict)
    deployment: dict[str, SharedStateEntry] = Field(default_factory=dict)


class SharedStateUpdate(StrictModel):
    """A structured, optionally conditional shared-state mutation."""

    scope: Literal["run", "deployment"]
    operation: Literal["set", "delete"] = "set"
    key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    value: JsonValue | None = None
    expected_version: int | None = Field(
        default=None,
        alias="expectedVersion",
        ge=0,
    )

    @model_validator(mode="after")
    def operation_matches_value(self) -> SharedStateUpdate:
        supplied = "value" in self.model_fields_set
        if self.operation == "set" and not supplied:
            raise ValueError("a set update requires value")
        if self.operation == "delete" and supplied:
            raise ValueError("a delete update cannot contain value")
        return self


class SharedStateChange(StrictModel):
    """The committed result of one shared-state mutation."""

    scope: Literal["run", "deployment"]
    operation: Literal["set", "delete"]
    key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    version: int = Field(ge=1)
    value: JsonValue | None = None


class AgentContext(StrictModel):
    """The complete scope visible to one agent invocation."""

    invocation_id: str = Field(alias="invocationId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    agent_id: str = Field(alias="agentId", min_length=1)
    deployment: str = Field(min_length=1)
    layer: AttachmentLayer
    custom_layer: str | None = Field(default=None, alias="customLayer")
    target_kind: ResourceKind = Field(alias="targetKind")
    targets: tuple[str, ...] = Field(min_length=1)
    capabilities: tuple[str, ...] = ()
    observations: dict[str, JsonValue] = Field(default_factory=dict)
    shared_state: SharedStateSnapshot = Field(
        default_factory=SharedStateSnapshot,
        alias="sharedState",
    )
    intent: str = Field(min_length=1)
    priority: int = 0
    invoked_at: AwareDatetime = Field(alias="invokedAt")

    @model_validator(mode="after")
    def scope_is_unambiguous(self) -> AgentContext:
        if self.layer == AttachmentLayer.CUSTOM and not self.custom_layer:
            raise ValueError("a custom attachment layer requires customLayer")
        if self.layer != AttachmentLayer.CUSTOM and self.custom_layer is not None:
            raise ValueError("customLayer is only valid for a custom layer")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("agent context targets must be unique")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("agent context capabilities must be unique")
        return self


class ModelRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelMessage(StrictModel):
    role: ModelRole
    content: str
    name: str | None = None


class ModelRequest(StrictModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    messages: tuple[ModelMessage, ...] = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    response_schema: dict[str, JsonValue] | None = Field(
        default=None,
        alias="responseSchema",
    )
    timeout_seconds: float = Field(default=30, alias="timeoutSeconds", gt=0)


class TokenUsage(StrictModel):
    input_tokens: int = Field(default=0, alias="inputTokens", ge=0)
    output_tokens: int = Field(default=0, alias="outputTokens", ge=0)
    total_tokens: int = Field(default=0, alias="totalTokens", ge=0)

    @model_validator(mode="after")
    def total_covers_reported_tokens(self) -> TokenUsage:
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("totalTokens cannot be smaller than input plus output")
        return self


class ModelResponse(StrictModel):
    content: str | None = None
    structured_output: JsonValue | None = Field(
        default=None,
        alias="structuredOutput",
    )
    usage: TokenUsage = Field(default_factory=TokenUsage)
    provider_request_id: str | None = Field(
        default=None,
        alias="providerRequestId",
    )
    finish_reason: str | None = Field(default=None, alias="finishReason")

    @model_validator(mode="after")
    def response_has_content(self) -> ModelResponse:
        if self.content is None and self.structured_output is None:
            raise ValueError("a model response requires content or structuredOutput")
        return self


class ModelProviderError(Exception):
    """Typed failure raised while invoking or normalizing a model backend."""

    def __init__(self, message: str, *, code: str) -> None:
        if not message or not code:
            raise ValueError("model provider errors require a code and message")
        super().__init__(message)
        self.code = code


class ActionProposal(StrictModel):
    id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    target: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    reason: str | None = Field(default=None, min_length=1)
    timeout_seconds: float = Field(default=30, alias="timeoutSeconds", gt=0)


class CapabilityOutcome(StrictModel):
    """Provider output before the runtime adds identity and completion time."""

    changed: bool = False
    output: dict[str, JsonValue] = Field(default_factory=dict)


class CapabilityProviderError(Exception):
    """Typed failure raised by a capability adapter for the engine to return."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status: ActionStatus = ActionStatus.FAILED,
    ) -> None:
        if not message or not code:
            raise ValueError("capability provider errors require a code and message")
        if status == ActionStatus.SUCCEEDED:
            raise ValueError("a capability provider error cannot be successful")
        super().__init__(message)
        self.code = code
        self.status = status


class AgentResponse(StrictModel):
    message: str | None = Field(default=None, min_length=1)
    proposals: tuple[ActionProposal, ...] = ()
    shared_state_updates: tuple[SharedStateUpdate, ...] = Field(
        default=(),
        alias="sharedStateUpdates",
    )
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def response_has_an_outcome(self) -> AgentResponse:
        if self.message is None and not self.proposals and not self.shared_state_updates:
            raise ValueError(
                "an agent response requires a message, proposal, or shared-state update"
            )
        proposal_ids = [proposal.id for proposal in self.proposals]
        if len(set(proposal_ids)) != len(proposal_ids):
            raise ValueError("action proposal ids must be unique")
        return self


class AgentProviderError(Exception):
    """Typed failure raised while constructing or invoking an agent adapter."""

    def __init__(self, message: str, *, code: str) -> None:
        if not message or not code:
            raise ValueError("agent provider errors require a code and message")
        super().__init__(message)
        self.code = code


class AgentInvocationResult(StrictModel):
    invocation_id: str = Field(alias="invocationId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    agent_id: str = Field(alias="agentId", min_length=1)
    status: InvocationStatus
    started_at: AwareDatetime = Field(alias="startedAt")
    completed_at: AwareDatetime = Field(alias="completedAt")
    response: AgentResponse | None = None
    action_results: tuple[ActionResult, ...] = Field(
        default=(),
        alias="actionResults",
    )
    shared_state_changes: tuple[SharedStateChange, ...] = Field(
        default=(),
        alias="sharedStateChanges",
    )
    issue: AgentRuntimeIssue | None = None

    @model_validator(mode="after")
    def outcome_matches_status(self) -> AgentInvocationResult:
        if self.completed_at < self.started_at:
            raise ValueError("completedAt cannot be earlier than startedAt")
        if self.status == InvocationStatus.SUCCEEDED:
            if self.issue is not None:
                raise ValueError("a successful invocation cannot contain an issue")
            if self.response is None:
                raise ValueError("a successful invocation requires a response")
        elif self.issue is None:
            raise ValueError("an unsuccessful invocation requires an issue")
        return self


@runtime_checkable
class AgentProvider(Protocol):
    """Adapter seam for a user-authored or declarative agent."""

    contract_version: str

    def invoke(self, context: AgentContext) -> AgentResponse:
        """Run one bounded invocation and return proposals or a message."""
        ...


@runtime_checkable
class ModelProvider(Protocol):
    """Adapter seam for a model backend."""

    contract_version: str

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one normalized model response."""
        ...


@runtime_checkable
class CapabilityProvider(Protocol):
    """Adapter seam for an authorized capability implementation."""

    contract_version: str

    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> CapabilityOutcome | Mapping[str, JsonValue] | ActionResult:
        """Execute one already-authorized proposal."""
        ...
