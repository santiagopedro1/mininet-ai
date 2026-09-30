"""Route external intents and explicit delegations through a compiled graph."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import ConfigDict, Field, JsonValue

from mininet_ai.compiler import DeploymentPlan
from mininet_ai.coordination.arbitration import (
    ArbitrationCandidate,
    ArbitrationReport,
    ConflictArbitrator,
)
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
    ActionProposal,
    AgentContext,
    AgentCoordinationContext,
    AgentInvocationResult,
    AgentResponse,
)
from mininet_ai.specification.models import StrictModel
from mininet_ai.substrates import ActionResult

Clock = Callable[[], datetime]
MessageIdFactory = Callable[[], str]
COORDINATION_OUTCOME_CONTRACT_VERSION: Literal[
    "mininet-ai/coordination-outcome/v1alpha1"
] = "mininet-ai/coordination-outcome/v1alpha1"
COORDINATION_OUTCOME_SCHEMA_ID = (
    "urn:mininet-ai:schema:v1alpha1:coordination-outcome"
)


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

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": COORDINATION_OUTCOME_SCHEMA_ID,
        },
    )

    contract_version: Literal[
        "mininet-ai/coordination-outcome/v1alpha1"
    ] = Field(
        default=COORDINATION_OUTCOME_CONTRACT_VERSION,
        alias="contractVersion",
    )
    run_id: str = Field(alias="runId", min_length=1)
    correlation_id: str = Field(alias="correlationId", min_length=1)
    requested_agent_id: str = Field(alias="requestedAgentId", min_length=1)
    entry_agent_id: str = Field(alias="entryAgentId", min_length=1)
    messages: tuple[CoordinationMessage, ...] = ()
    invocations: tuple[CoordinatedInvocation, ...] = ()
    arbitration: ArbitrationReport | None = None
    issues: tuple[CoordinationIssue, ...] = ()


@runtime_checkable
class Coordinator(Protocol):
    """Coordinate one external request through the canonical runtime seam."""

    def coordinate(self, request: CoordinationRequest) -> CoordinationOutcome: ...


@dataclass(frozen=True)
class PreparedAgentInvocation:
    """Reasoning output awaiting Mininet-owned action commitment."""

    context: AgentContext
    response: AgentResponse
    token: object


@dataclass(frozen=True)
class _PendingInvocation:
    message: CoordinationMessage
    prepared: PreparedAgentInvocation | None = None
    result: AgentInvocationResult | None = None


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


class CoordinatedAgentExecutor(Protocol):
    """Prepare reasoning, execute admitted actions, and complete an invocation."""

    def prepare(
        self,
        run_id: str,
        agent_id: str,
        intent: str,
        *,
        coordination: AgentCoordinationContext | None = None,
    ) -> PreparedAgentInvocation | AgentInvocationResult: ...

    def execute(
        self,
        prepared: PreparedAgentInvocation,
        proposal: ActionProposal,
    ) -> ActionResult: ...

    def validate(
        self,
        prepared: PreparedAgentInvocation,
        proposal: ActionProposal,
    ) -> ActionResult | None: ...

    def reject(
        self,
        prepared: PreparedAgentInvocation,
        proposal: ActionProposal,
        *,
        code: str,
        message: str,
    ) -> ActionResult: ...

    def complete(
        self,
        prepared: PreparedAgentInvocation,
        action_results: tuple[ActionResult, ...],
    ) -> AgentInvocationResult: ...


class CoordinationRuntime:
    """Resolve entry routing and execute explicit, bounded delegations."""

    def __init__(
        self,
        plan: DeploymentPlan,
        invoker: CoordinatedAgentExecutor,
        *,
        channel: MessageChannel | None = None,
        clock: Clock = _utc_now,
        message_id_factory: MessageIdFactory = _message_id,
    ) -> None:
        self._graph = CoordinationGraph(plan)
        self._invoker = invoker
        self._arbitrator = ConflictArbitrator(plan)
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
        self._max_concurrent_actions = plan.resource_limits.max_concurrent_invocations
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
        pending: list[_PendingInvocation] = []
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
                prepared = self._invoker.prepare(
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
            identity = (
                prepared
                if isinstance(prepared, AgentInvocationResult)
                else prepared.context
            )
            if identity.run_id != message.run_id or identity.agent_id != (
                message.target_agent_id
            ):
                issues.append(
                    self._issue(
                        "coordination.invocation.invalid-identity",
                        "agent invoker returned a result for another run or agent",
                        message,
                    )
                )
                continue
            if isinstance(prepared, AgentInvocationResult):
                pending.append(_PendingInvocation(message=message, result=prepared))
                continue
            pending.append(_PendingInvocation(message=message, prepared=prepared))
            for delegation in prepared.response.delegations:
                self._delegate(
                    message,
                    prepared.context.invocation_id,
                    payload,
                    delegation.id,
                    delegation.target_agent_id,
                    delegation.intent,
                    delegation.metadata,
                    messages,
                    issues,
                )

        action_results: dict[str, ActionResult] = {}
        admitted: list[tuple[AgentContext, ActionProposal]] = []
        for item in pending:
            if item.prepared is None:
                continue
            for proposal in item.prepared.response.proposals:
                candidate_id = self._candidate_id(item.prepared, proposal)
                rejection = self._invoker.validate(item.prepared, proposal)
                if rejection is None:
                    admitted.append((item.prepared.context, proposal))
                else:
                    action_results[candidate_id] = rejection

        arbitration = self._arbitrator.arbitrate(tuple(admitted))
        prepared_by_candidate = {
            self._candidate_id(item.prepared, proposal): item.prepared
            for item in pending
            if item.prepared is not None
            for proposal in item.prepared.response.proposals
        }
        action_results.update(
            self._execute_admitted(
                arbitration.ordered,
                prepared_by_candidate,
            )
        )
        for candidate in arbitration.candidates:
            if candidate.candidate_id not in arbitration.rejected:
                continue
            prepared = prepared_by_candidate[candidate.candidate_id]
            action_results[candidate.candidate_id] = self._invoker.reject(
                prepared,
                candidate.proposal,
                code="coordination.conflict.rejected",
                message="action proposal conflicts with an earlier admitted action",
            )

        invocations: list[CoordinatedInvocation] = []
        for item in pending:
            if item.result is not None:
                invocations.append(
                    CoordinatedInvocation(message=item.message, result=item.result)
                )
                continue
            prepared = item.prepared
            if prepared is None:
                raise AssertionError("pending invocation has no outcome")
            results = tuple(
                action_results[self._candidate_id(prepared, proposal)]
                for proposal in prepared.response.proposals
            )
            try:
                result = self._invoker.complete(prepared, results)
            except Exception as error:
                issues.append(
                    self._issue(
                        "coordination.invocation.completion-failed",
                        str(error) or type(error).__name__,
                        item.message,
                    )
                )
                continue
            invocations.append(CoordinatedInvocation(message=item.message, result=result))

        return CoordinationOutcome(
            runId=request.run_id,
            correlationId=request.correlation_id,
            requestedAgentId=request.requested_agent_id,
            entryAgentId=entry_agent,
            messages=tuple(messages),
            invocations=tuple(invocations),
            arbitration=arbitration.report,
            issues=tuple(issues),
        )

    def _execute_admitted(
        self,
        candidates: tuple[ArbitrationCandidate, ...],
        prepared_by_candidate: dict[str, PreparedAgentInvocation],
    ) -> dict[str, ActionResult]:
        if not candidates:
            return {}
        futures: dict[str, Future[ActionResult]] = {}
        latest_by_conflict_key: dict[str, Future[ActionResult]] = {}
        with ThreadPoolExecutor(
            max_workers=min(self._max_concurrent_actions, len(candidates)),
            thread_name_prefix="mininet-ai-action",
        ) as executor:
            for candidate in candidates:
                dependencies = tuple(
                    dict.fromkeys(
                        latest_by_conflict_key[key]
                        for key in candidate.conflict_keys
                        if key in latest_by_conflict_key
                    )
                )
                future = executor.submit(
                    self._execute_after,
                    dependencies,
                    prepared_by_candidate[candidate.candidate_id],
                    candidate.proposal,
                )
                futures[candidate.candidate_id] = future
                for key in candidate.conflict_keys:
                    latest_by_conflict_key[key] = future
        return {
            candidate_id: future.result()
            for candidate_id, future in futures.items()
        }

    def _execute_after(
        self,
        dependencies: tuple[Future[ActionResult], ...],
        prepared: PreparedAgentInvocation,
        proposal: ActionProposal,
    ) -> ActionResult:
        for dependency in dependencies:
            dependency.result()
        return self._invoker.execute(prepared, proposal)

    def _delegate(
        self,
        parent: CoordinationMessage,
        parent_invocation_id: str,
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
            parentInvocationId=parent_invocation_id,
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
    def _candidate_id(
        prepared: PreparedAgentInvocation,
        proposal: ActionProposal,
    ) -> str:
        return f"{prepared.context.invocation_id}:{proposal.id}"

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
