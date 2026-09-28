"""Route external intents and explicit delegations through a compiled graph."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol
from uuid import uuid4

from pydantic import Field, JsonValue

from mininet_ai.compiler import DeploymentPlan
from mininet_ai.coordination.channel import (
    InMemoryMessageChannel,
    MessageChannel,
    MessageChannelError,
)
from mininet_ai.coordination.contracts import (
    CoordinationIntentPayload,
    CoordinationMessage,
    CoordinationMessageKind,
)
from mininet_ai.coordination.graph import CoordinationGraph, CoordinationGraphError
from mininet_ai.errors import MininetAIError
from mininet_ai.sdk import (
    AgentCoordinationContext,
    AgentInvocationResult,
    InvocationStatus,
)
from mininet_ai.specification.models import StrictModel


Clock = Callable[[], datetime]
MessageIdFactory = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _message_id() -> str:
    return f"message-{uuid4()}"


class CoordinationRuntimeError(MininetAIError):
    """A coordination request could not be routed safely."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class CoordinationRequest(StrictModel):
    """One externally submitted intent and its routing identity."""

    run_id: str = Field(alias="runId", min_length=1)
    requested_agent_id: str = Field(alias="requestedAgentId", min_length=1)
    intent: str = Field(min_length=1)
    correlation_id: str = Field(alias="correlationId", min_length=1)
    triggering_event_id: str | None = Field(
        default=None,
        alias="triggeringEventId",
        min_length=1,
    )


class CoordinationIssue(StrictModel):
    """One message or invocation that could not be completed."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    message_id: str | None = Field(default=None, alias="messageId", min_length=1)
    source_agent_id: str | None = Field(
        default=None,
        alias="sourceAgentId",
        min_length=1,
    )
    target_agent_id: str | None = Field(
        default=None,
        alias="targetAgentId",
        min_length=1,
    )


class CoordinatedInvocation(StrictModel):
    """One accepted message and the invocation it caused."""

    message: CoordinationMessage
    result: AgentInvocationResult


class CoordinationOutcome(StrictModel):
    """Ordered routing evidence for one external coordination request."""

    run_id: str = Field(alias="runId", min_length=1)
    correlation_id: str = Field(alias="correlationId", min_length=1)
    requested_agent_id: str = Field(alias="requestedAgentId", min_length=1)
    entry_agent_id: str = Field(alias="entryAgentId", min_length=1)
    messages: tuple[CoordinationMessage, ...] = ()
    invocations: tuple[CoordinatedInvocation, ...] = ()
    issues: tuple[CoordinationIssue, ...] = ()


class CoordinatedAgentInvoker(Protocol):
    """Invoke one agent with graph-authorized routing context."""

    def invoke(
        self,
        run_id: str,
        agent_id: str,
        intent: str,
        *,
        coordination: AgentCoordinationContext | None = None,
    ) -> AgentInvocationResult: ...


class CoordinationRuntime:
    """Resolve entry routing and execute explicit, bounded delegations."""

    def __init__(
        self,
        plan: DeploymentPlan,
        invoker: CoordinatedAgentInvoker,
        *,
        channel: MessageChannel | None = None,
        clock: Clock = _utc_now,
        message_id_factory: MessageIdFactory = _message_id,
    ) -> None:
        self._graph = CoordinationGraph(plan)
        self._invoker = invoker
        self._channel = channel or InMemoryMessageChannel(
            capacity=plan.resource_limits.max_queued_events
        )
        if self._channel.capacity > plan.resource_limits.max_queued_events:
            raise CoordinationRuntimeError(
                "message channel capacity exceeds the deployment plan limit",
                code="coordination.channel.limit-exceeded",
            )
        self._clock = clock
        self._message_id_factory = message_id_factory
        self._coordinate_lock = Lock()

    def coordinate(self, request: CoordinationRequest) -> CoordinationOutcome:
        """Route one request and drain every accepted delegation it causes."""

        with self._coordinate_lock:
            return self._coordinate(request)

    def _coordinate(self, request: CoordinationRequest) -> CoordinationOutcome:
        entry_agent = self._graph.entry_agent(request.requested_agent_id)
        root = CoordinationMessage(
            messageId=self._next_message_id(),
            runId=request.run_id,
            kind=CoordinationMessageKind.INTENT,
            targetAgentId=entry_agent,
            correlationId=request.correlation_id,
            triggeringEventId=request.triggering_event_id,
            createdAt=self._clock(),
            payload=CoordinationIntentPayload(
                intent=request.intent,
                requestedAgentId=request.requested_agent_id,
            ).model_dump(mode="json", by_alias=True),
        )
        self._channel.send(root)
        messages = [root]
        invocations: list[CoordinatedInvocation] = []
        issues: list[CoordinationIssue] = []

        while (message := self._channel.receive(timeout_seconds=0)) is not None:
            if message.kind not in {
                CoordinationMessageKind.INTENT,
                CoordinationMessageKind.DELEGATION,
            }:
                issues.append(
                    self._issue(
                        "coordination.message.kind-invalid",
                        f"cannot invoke an agent from {message.kind.value!r} message",
                        message,
                    )
                )
                continue
            try:
                payload = CoordinationIntentPayload.model_validate(message.payload)
            except ValueError as error:
                issues.append(
                    self._issue(
                        "coordination.message.payload-invalid",
                        str(error),
                        message,
                    )
                )
                continue
            context = AgentCoordinationContext(
                messageId=message.message_id,
                correlationId=message.correlation_id,
                causationId=message.causation_id,
                kind=(
                    "intent"
                    if message.kind == CoordinationMessageKind.INTENT
                    else "delegation"
                ),
                sourceAgentId=message.source_agent_id,
                requestedAgentId=payload.requested_agent_id,
                allowedDestinations=self._graph.destinations(
                    message.target_agent_id
                ),
                hopCount=message.hop_count,
                metadata=payload.metadata,
            )
            try:
                result = self._invoker.invoke(
                    message.run_id,
                    message.target_agent_id,
                    payload.intent,
                    coordination=context,
                )
            except Exception as error:
                issues.append(
                    self._issue(
                        "coordination.invocation.failed",
                        str(error) or type(error).__name__,
                        message,
                    )
                )
                continue
            if (
                result.run_id != message.run_id
                or result.agent_id != message.target_agent_id
            ):
                issues.append(
                    self._issue(
                        "coordination.invocation.invalid-identity",
                        "agent invoker returned a result for another run or agent",
                        message,
                    )
                )
                continue
            invocations.append(CoordinatedInvocation(message=message, result=result))
            if (
                result.status != InvocationStatus.SUCCEEDED
                or result.response is None
            ):
                continue
            for delegation in result.response.delegations:
                self._delegate(
                    message,
                    result,
                    payload,
                    delegation.id,
                    delegation.target_agent_id,
                    delegation.intent,
                    delegation.metadata,
                    messages,
                    issues,
                )

        return CoordinationOutcome(
            runId=request.run_id,
            correlationId=request.correlation_id,
            requestedAgentId=request.requested_agent_id,
            entryAgentId=entry_agent,
            messages=tuple(messages),
            invocations=tuple(invocations),
            issues=tuple(issues),
        )

    def _delegate(
        self,
        parent: CoordinationMessage,
        result: AgentInvocationResult,
        parent_payload: CoordinationIntentPayload,
        delegation_id: str,
        target_agent_id: str,
        intent: str,
        metadata: dict[str, JsonValue],
        messages: list[CoordinationMessage],
        issues: list[CoordinationIssue],
    ) -> None:
        try:
            self._graph.authorize(parent.target_agent_id, target_agent_id)
        except CoordinationGraphError as error:
            issues.append(
                CoordinationIssue(
                    code=error.code,
                    message=str(error),
                    messageId=parent.message_id,
                    sourceAgentId=parent.target_agent_id,
                    targetAgentId=target_agent_id,
                )
            )
            return
        next_hop = parent.hop_count + 1
        if next_hop > self._graph.hop_limit:
            issues.append(
                CoordinationIssue(
                    code="coordination.message.hop-limit-exceeded",
                    message=(
                        f"delegation from {parent.target_agent_id!r} to "
                        f"{target_agent_id!r} exceeds hop limit "
                        f"{self._graph.hop_limit}"
                    ),
                    messageId=parent.message_id,
                    sourceAgentId=parent.target_agent_id,
                    targetAgentId=target_agent_id,
                )
            )
            return
        delegated = CoordinationMessage(
            messageId=self._next_message_id(),
            runId=parent.run_id,
            kind=CoordinationMessageKind.DELEGATION,
            sourceAgentId=parent.target_agent_id,
            targetAgentId=target_agent_id,
            correlationId=parent.correlation_id,
            causationId=parent.message_id,
            parentInvocationId=result.invocation_id,
            triggeringEventId=parent.triggering_event_id,
            createdAt=self._clock(),
            hopCount=next_hop,
            traversedMessageIds=(
                *parent.traversed_message_ids,
                parent.message_id,
            ),
            payload=CoordinationIntentPayload(
                intent=intent,
                requestedAgentId=parent_payload.requested_agent_id,
                delegationId=delegation_id,
                metadata=metadata,
            ).model_dump(mode="json", by_alias=True),
        )
        try:
            self._channel.send(delegated)
        except MessageChannelError as error:
            issues.append(
                CoordinationIssue(
                    code=error.code,
                    message=str(error),
                    messageId=delegated.message_id,
                    sourceAgentId=delegated.source_agent_id,
                    targetAgentId=delegated.target_agent_id,
                )
            )
            return
        messages.append(delegated)

    def _next_message_id(self) -> str:
        message_id = self._message_id_factory()
        if not message_id:
            raise CoordinationRuntimeError(
                "message ID factory returned an empty value",
                code="coordination.message.invalid-id",
            )
        return message_id

    @staticmethod
    def _issue(
        code: str,
        reason: str,
        message: CoordinationMessage,
    ) -> CoordinationIssue:
        return CoordinationIssue(
            code=code,
            message=reason,
            messageId=message.message_id,
            sourceAgentId=message.source_agent_id,
            targetAgentId=message.target_agent_id,
        )
