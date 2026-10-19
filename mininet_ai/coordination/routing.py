"""Route external intents and explicit delegations through a compiled graph."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Condition, Lock
from typing import Literal, Protocol, Self, runtime_checkable
from uuid import uuid4

from pydantic import ConfigDict, Field, JsonValue

from mininet_ai.compiler import DeploymentPlan
from mininet_ai.coordination.admission import ActionAdmission
from mininet_ai.coordination.arbitration import (
    ArbitrationCandidate,
    ArbitrationPlan,
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
COORDINATION_OUTCOME_SCHEMA_ID = "urn:mininet-ai:schema:v1alpha1:coordination-outcome"


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

    contract_version: Literal["mininet-ai/coordination-outcome/v1alpha1"] = Field(
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


class InvocationLease:
    """One invocation allocation, releasable across threads exactly once."""

    def __init__(self, agent_id: str, release: Callable[[], None]) -> None:
        self.agent_id = agent_id
        self._release = release
        self._lock = Lock()
        self._released = False

    def close(self) -> None:
        with self._lock:
            if not self._released:
                self._released = True
                self._release()

    @property
    def active(self) -> bool:
        with self._lock:
            return not self._released

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@runtime_checkable
class BudgetedCoordinator(Coordinator, Protocol):
    """Admit entry agents before removing requests from bounded queues."""

    def reserve_invocation(self, agent_id: str) -> InvocationLease: ...
    def coordinate(
        self, request: CoordinationRequest, *, lease: InvocationLease | None = None
    ) -> CoordinationOutcome: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class PreparedAgentInvocation:
    """Reasoning output awaiting Mininet-owned action commitment."""

    context: AgentContext
    response: AgentResponse
    token: object


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
        self._admission = ActionAdmission(self._arbitrator.policy)
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
        self._max_concurrent_invocations = (
            plan.resource_limits.max_concurrent_invocations
        )
        self._slots = Condition()
        self._active = 0
        self._requests = 0
        self._closing = False
        self._active_agents = {agent.id: 0 for agent in plan.agents}
        self._agent_limits = {
            agent.id: agent.execution.max_concurrency for agent in plan.agents
        }
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_concurrent_invocations,
            thread_name_prefix="mininet-ai-agent",
        )

    def coordinate(
        self, request: CoordinationRequest, *, lease: InvocationLease | None = None
    ) -> CoordinationOutcome:
        """Route one request and drain every accepted delegation it causes."""

        try:
            return self._coordinate(request, lease=lease)
        finally:
            if lease is not None:
                lease.close()

    def close(self) -> None:
        with self._slots:
            self._closing = True
            self._slots.notify_all()
            self._slots.wait_for(lambda: self._requests == 0)
        self._executor.shutdown(wait=True)

    @contextmanager
    def _request_session(self):
        with self._slots:
            if self._closing:
                raise CoordinationRuntimeError(
                    "coordinator is closed", code="coordination.closed"
                )
            self._requests += 1
        try:
            yield
        finally:
            with self._slots:
                self._requests -= 1
                self._slots.notify_all()

    def _coordinate(
        self, request: CoordinationRequest, *, lease: InvocationLease | None = None
    ) -> CoordinationOutcome:
        channel = self._channel
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
        messages = [root]
        issues: list[CoordinationIssue] = []
        invocations: list[CoordinatedInvocation] = []
        reports: list[ArbitrationReport] = []
        history: list[ArbitrationCandidate] = []
        with self._request_session():
            channel.send(root)
            futures = {}
            submitted: set[str] = set()
            if lease is not None:
                if lease.agent_id != entry_agent:
                    raise CoordinationRuntimeError(
                        "entry lease belongs to another agent",
                        code="coordination.invocation.invalid-identity",
                    )
                channel.receive(
                    timeout_seconds=0, message_ids=frozenset({root.message_id})
                )
                futures[self._submit_invocation(root, history, lease)] = root
                submitted.add(root.message_id)
            while True:
                pending = [m for m in messages if m.message_id not in submitted]
                while (work := self._take_invocation(channel, pending)) is not None:
                    message, allocation = work
                    futures[self._submit_invocation(message, history, allocation)] = (
                        message
                    )
                    submitted.add(message.message_id)
                    pending.remove(message)
                if not futures:
                    if not pending:
                        break
                    with self._slots:
                        self._slots.wait_for(
                            lambda pending=pending: any(
                                self._available(m.target_agent_id) for m in pending
                            )
                        )
                    continue
                done, _ = wait(futures, timeout=0.05, return_when=FIRST_COMPLETED)
                for future in tuple(futures):
                    if future not in done:
                        continue
                    message = futures.pop(future)
                    try:
                        result, response, arbitration = future.result()
                    except Exception as error:
                        issues.append(
                            self._issue(
                                getattr(
                                    error, "code", "coordination.invocation.failed"
                                ),
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
                    invocations.append(
                        CoordinatedInvocation(message=message, result=result)
                    )
                    if arbitration is not None:
                        reports.append(arbitration)
                    if response is not None:
                        payload = CoordinationIntentPayload.model_validate(
                            message.payload
                        )
                        for delegation in response.delegations:
                            self._delegate(
                                message,
                                result.invocation_id,
                                payload,
                                delegation.id,
                                delegation.target_agent_id,
                                delegation.intent,
                                delegation.metadata,
                                messages,
                                issues,
                                channel,
                            )
        return CoordinationOutcome(
            runId=request.run_id,
            correlationId=request.correlation_id,
            requestedAgentId=request.requested_agent_id,
            entryAgentId=entry_agent,
            messages=tuple(messages),
            invocations=tuple(invocations),
            arbitration=ArbitrationReport(
                policy=self._arbitrator.policy,
                decisions=tuple(
                    decision.model_copy(
                        update={
                            "admission_sequence": sequence,
                            "commit_order": next(
                                (
                                    i
                                    for i, candidate in enumerate(history)
                                    if candidate.candidate_id == decision.candidate_id
                                ),
                                None,
                            )
                            if decision.disposition == "execute"
                            else None,
                        }
                    )
                    for sequence, decision in enumerate(
                        d for report in reports for d in report.decisions
                    )
                ),
            ),
            issues=tuple(issues),
        )

    def _available(self, agent_id: str) -> bool:
        return (
            self._active < self._max_concurrent_invocations
            and self._active_agents[agent_id] < self._agent_limits[agent_id]
        )

    def _submit_invocation(
        self,
        message: CoordinationMessage,
        history: list[ArbitrationCandidate],
        lease: InvocationLease,
    ) -> Future[
        tuple[AgentInvocationResult, AgentResponse | None, ArbitrationReport | None]
    ]:
        try:
            return self._executor.submit(
                copy_context().run, self._invoke_message, message, history, lease
            )
        except BaseException:
            lease.close()
            raise

    def reserve_invocation(self, agent_id: str) -> InvocationLease:
        with self._slots:
            self._slots.wait_for(lambda: self._closing or self._available(agent_id))
            if self._closing:
                raise CoordinationRuntimeError(
                    "coordinator is closed", code="coordination.closed"
                )
            return self._allocate_invocation(agent_id)

    def _allocate_invocation(self, agent_id: str) -> InvocationLease:
        self._active += 1
        self._active_agents[agent_id] += 1

        def release() -> None:
            with self._slots:
                self._active -= 1
                self._active_agents[agent_id] -= 1
                self._slots.notify_all()

        return InvocationLease(agent_id, release)

    def _take_invocation(
        self, channel: MessageChannel, pending: list[CoordinationMessage]
    ) -> tuple[CoordinationMessage, InvocationLease] | None:
        with self._slots:
            eligible = frozenset(
                m.message_id for m in pending if self._available(m.target_agent_id)
            )
            if not eligible:
                return None
            message = channel.receive(timeout_seconds=0, message_ids=eligible)
            if message is None:
                return None
            return message, self._allocate_invocation(message.target_agent_id)

    def _invoke_message(
        self,
        message: CoordinationMessage,
        history: list[ArbitrationCandidate],
        lease: InvocationLease,
    ) -> tuple[AgentInvocationResult, AgentResponse | None, ArbitrationReport | None]:
        with lease:
            if message.kind not in {
                CoordinationMessageKind.INTENT,
                CoordinationMessageKind.DELEGATION,
            }:
                raise CoordinationRuntimeError(
                    "invalid invocation message kind",
                    code="coordination.message.kind-invalid",
                )
            payload = CoordinationIntentPayload.model_validate(message.payload)
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
                allowedDestinations=self._graph.destinations(message.target_agent_id),
                hopCount=message.hop_count,
                metadata=payload.metadata,
            )
            prepared = self._invoker.prepare(
                message.run_id,
                message.target_agent_id,
                payload.intent,
                coordination=context,
            )
            identity = (
                prepared
                if isinstance(prepared, AgentInvocationResult)
                else prepared.context
            )
            if identity.run_id != message.run_id or identity.agent_id != (
                message.target_agent_id
            ):
                raise CoordinationRuntimeError(
                    "agent invoker returned a result for another run or agent",
                    code="coordination.invocation.invalid-identity",
                )
            if isinstance(prepared, AgentInvocationResult):
                return prepared, None, None
            return self._commit_prepared(prepared, history)

    def _commit_prepared(
        self, prepared: PreparedAgentInvocation, history: list[ArbitrationCandidate]
    ) -> tuple[AgentInvocationResult, AgentResponse | None, ArbitrationReport | None]:
        action_results: dict[str, ActionResult] = {}
        admitted: list[tuple[AgentContext, ActionProposal]] = []
        for proposal in prepared.response.proposals:
            candidate_id = self._candidate_id(prepared, proposal)
            rejection = self._invoker.validate(prepared, proposal)
            if rejection is None:
                admitted.append((prepared.context, proposal))
            else:
                action_results[candidate_id] = rejection
        arbitration = self._arbitrator.arbitrate(tuple(admitted))
        with self._admission.decision(
            prepared.context, prepared.response, arbitration.ordered, history
        ) as rejections:
            return self._finish_prepared(
                prepared, history, arbitration, action_results, rejections
            )

    def _finish_prepared(
        self,
        prepared: PreparedAgentInvocation,
        history: list[ArbitrationCandidate],
        arbitration: ArbitrationPlan,
        action_results: dict[str, ActionResult],
        rejections: dict[str, ArbitrationCandidate],
    ):
        decisions = {d.candidate_id: d for d in arbitration.report.decisions}
        for candidate in arbitration.ordered:
            if candidate.candidate_id in rejections:
                winner = rejections[candidate.candidate_id]
                action_results[candidate.candidate_id] = self._invoker.reject(
                    prepared,
                    candidate.proposal,
                    code="coordination.conflict.rejected",
                    message="action proposal conflicts with an earlier admitted action",
                )
                decisions[candidate.candidate_id] = decisions[
                    candidate.candidate_id
                ].model_copy(
                    update={
                        "disposition": "reject",
                        "commit_order": None,
                        "winner_candidate_id": winner.candidate_id,
                    }
                )
                continue
            rejection = self._invoker.validate(prepared, candidate.proposal)
            if rejection is not None:
                action_results[candidate.candidate_id] = rejection
                decisions[candidate.candidate_id] = decisions[
                    candidate.candidate_id
                ].model_copy(update={"disposition": "reject", "commit_order": None})
                continue
            with self._admission.admit(candidate, history) as winner:
                if winner is None:
                    action_results[candidate.candidate_id] = self._invoker.execute(
                        prepared, candidate.proposal
                    )
                else:
                    action_results[candidate.candidate_id] = self._invoker.reject(
                        prepared,
                        candidate.proposal,
                        code="coordination.conflict.rejected",
                        message="action proposal conflicts with an earlier admitted action",
                    )
                    decisions[candidate.candidate_id] = decisions[
                        candidate.candidate_id
                    ].model_copy(
                        update={
                            "disposition": "reject",
                            "commit_order": None,
                            "winner_candidate_id": winner.candidate_id,
                        }
                    )
        for candidate in arbitration.candidates:
            if candidate.candidate_id in arbitration.rejected:
                action_results[candidate.candidate_id] = self._invoker.reject(
                    prepared,
                    candidate.proposal,
                    code="coordination.conflict.rejected",
                    message="action proposal conflicts with an earlier admitted action",
                )
        results = tuple(
            action_results[self._candidate_id(prepared, proposal)]
            for proposal in prepared.response.proposals
        )
        result = self._invoker.complete(prepared, results)
        return (
            result,
            prepared.response if result.status.value == "succeeded" else None,
            arbitration.report.model_copy(
                update={"decisions": tuple(decisions.values())}
            ),
        )

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
        channel: MessageChannel,
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
            channel.send(delegated)
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
