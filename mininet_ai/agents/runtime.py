"""Bounded orchestration for one manually requested agent invocation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError

from mininet_ai.agents.agno import AgnoAgentFactory, AgnoAgentProvider
from mininet_ai.agents.bounded import (
    BoundedDecisionAdapter,
    DecisionAuthority,
    DecisionTransport,
)
from mininet_ai.audit import (
    AuditedCapabilityExecutor,
    AuditEventType,
    AuditRecorder,
)
from mininet_ai.capabilities import (
    CapabilityEngine,
    PostconditionVerifier,
    SubstrateActionProvider,
    SubstrateObservationProvider,
)
from mininet_ai.compiler import DeploymentPlan
from mininet_ai.coordination.routing import PreparedAgentInvocation
from mininet_ai.durations import duration_seconds
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.plugins import (
    ProviderKind,
    ProviderPlugin,
    ProviderRegistries,
)
from mininet_ai.runtime import (
    SharedScope,
    SharedStateAccess,
    SharedStateError,
    SharedStateStore,
)
from mininet_ai.sdk import (
    ActionProposal,
    AgentContext,
    AgentCoordinationContext,
    AgentInvocationResult,
    AgentResponse,
    AgentRuntimeIssue,
    CapabilityProvider,
    ExecutionCatalog,
    InvocationStatus,
    InvocationTimings,
    SharedStateChange,
    SharedStateSnapshot,
    SharedStateUpdate,
)
from mininet_ai.sdk.catalog import AgentExecutionDefinition
from mininet_ai.specification.models import CapabilityDefinition
from mininet_ai.substrates import (
    ActionResult,
    ActionStatus,
    ObservationQuery,
    RunState,
    RuntimeIssue,
    SubstrateRuntime,
)

Clock = Callable[[], datetime]
InvocationIdFactory = Callable[[], str]
MonotonicClock = Callable[[], float]
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class AgentExecutionAdapter(Protocol):
    """Runtime-specific reasoning and persistence behind Mininet's lifecycle."""

    def run(self, context: AgentContext) -> AgentExecution: ...

    def record_action_results(
        self,
        context: AgentContext,
        results: tuple[ActionResult, ...],
    ) -> dict[str, JsonValue] | None: ...


ExecutionFactory = Callable[[AgentExecutionDefinition], AgentExecutionAdapter]


class AgentExecution(Protocol):
    """Normalized execution details consumed by Mininet's shared lifecycle."""

    @property
    def response(self) -> AgentResponse: ...

    @property
    def model_queueing_seconds(self) -> float: ...

    @property
    def runtime_name(self) -> str: ...

    @property
    def runtime_data(self) -> dict[str, JsonValue]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _invocation_id() -> str:
    return f"invoke-{uuid4()}"


@dataclass
class _OneShotPreparation:
    context: AgentContext
    response: AgentResponse
    provider: AgentExecutionAdapter
    started_at: datetime
    started_tick: float
    context_seconds: float
    model_queueing_seconds: float
    reasoning_seconds: float
    state_changes: tuple[SharedStateChange, ...]
    authority: DecisionAuthority | None = None
    action_total_seconds: float = 0
    action_timing_lock: Lock = field(default_factory=Lock)
    action_start_lock: Lock = field(default_factory=Lock)


def register_builtin_providers(
    registries: ProviderRegistries,
    substrate: SubstrateRuntime,
) -> None:
    """Register built-in capability providers used by the agent runtime."""
    substrate_action: ProviderPlugin[
        CapabilityDefinition,
        CapabilityProvider,
    ] = ProviderPlugin(
        kind=ProviderKind.CAPABILITY,
        factory=lambda definition: SubstrateActionProvider(definition, substrate),
    )
    registries.capabilities.register("substrate.action", substrate_action)
    registries.capabilities.register(
        "substrate.observation",
        ProviderPlugin(
            kind=ProviderKind.CAPABILITY,
            factory=lambda definition: SubstrateObservationProvider(
                definition,
                substrate,
            ),
        ),
    )
    if substrate.name == "fake":
        registries.capabilities.register("fake.openflow", substrate_action)


class OneShotAgentRuntime:
    """Observe, invoke, authorize, execute, and normalize one agent run."""

    def __init__(
        self,
        plan: DeploymentPlan,
        substrate: SubstrateRuntime,
        registries: ProviderRegistries,
        *,
        audit: AuditRecorder | None = None,
        agent_factory: AgnoAgentFactory | None = None,
        execution_factory: ExecutionFactory | None = None,
        decision_transport: object | None = None,
        bounded_startup: dict[str, dict[str, JsonValue]] | None = None,
        shared_state: SharedStateStore | None = None,
        clock: Clock = _utc_now,
        monotonic_clock: MonotonicClock = monotonic,
        invocation_id_factory: InvocationIdFactory = _invocation_id,
    ) -> None:
        self._catalog = ExecutionCatalog(plan)
        self._substrate = substrate
        self._audit = audit
        has_agno = any(
            (definition := self._catalog.resolve(agent_id)).blueprint.execution is None
            or definition.blueprint.execution.kind == "agno"
            for agent_id in self._catalog.agent_ids
        )
        self._agent_factory = agent_factory or (
            AgnoAgentFactory() if has_agno else None
        )
        self._decision_transport = decision_transport
        self._bounded_startup = bounded_startup or {}
        self._execution_factory = execution_factory or self._create_execution_adapter
        self._shared_state = shared_state
        self._agent_instances = plan.agents
        if shared_state is None and any(
            agent.memory.shared is not None for agent in plan.agents
        ):
            raise AgentRuntimeError(
                "the deployment plan declares shared state but no store is configured",
                code="state.store.required",
            )
        self._clock = clock
        self._monotonic = monotonic_clock
        self._invocation_id_factory = invocation_id_factory
        self._authority_lock = Lock()
        self._active_authorities: dict[str, tuple[DecisionAuthority, object, str]] = {}
        capability_engine = CapabilityEngine(
            self._catalog,
            registries.capabilities,
            clock=clock,
            verifier=PostconditionVerifier(
                substrate,
                clock=clock,
                monotonic_clock=monotonic_clock,
            ),
        )
        self._capabilities = (
            AuditedCapabilityExecutor(capability_engine, audit)
            if audit is not None
            else capability_engine
        )

    def _create_execution_adapter(
        self, definition: AgentExecutionDefinition
    ) -> AgentExecutionAdapter:
        selection = definition.blueprint.execution
        if selection is not None and selection.kind == "bounded-decision":
            from mininet_ai.transports.http import HttpxJsonTransport

            return BoundedDecisionAdapter(
                definition,
                cast(
                    DecisionTransport,
                    self._decision_transport or HttpxJsonTransport(),
                ),
                self._bounded_startup.get(definition.instance.id),
                monotonic_clock=self._monotonic,
                substrate=self._substrate,
            )
        if self._agent_factory is None:
            self._agent_factory = AgnoAgentFactory()
        return AgnoAgentProvider(definition, factory=self._agent_factory)

    def invoke(
        self,
        run_id: str,
        agent_id: str,
        intent: str,
        *,
        coordination: AgentCoordinationContext | None = None,
    ) -> AgentInvocationResult:
        prepared = self.prepare(
            run_id,
            agent_id,
            intent,
            coordination=coordination,
        )
        if isinstance(prepared, AgentInvocationResult):
            return prepared
        action_results = tuple(
            self.validate(prepared, proposal) or self.execute(prepared, proposal)
            for proposal in prepared.response.proposals
        )
        return self.complete(prepared, action_results)

    def prepare(
        self,
        run_id: str,
        agent_id: str,
        intent: str,
        *,
        coordination: AgentCoordinationContext | None = None,
    ) -> PreparedAgentInvocation | AgentInvocationResult:
        """Run reasoning without publishing action outcomes or shared state."""

        started_at = self._clock()
        started_tick = self._monotonic()
        invocation_id = self._invocation_id_factory()
        if not intent:
            raise AgentRuntimeError(
                "agent intent cannot be empty",
                code="agent.intent.invalid",
                agent_id=agent_id,
                invocation_id=invocation_id,
            )
        if not invocation_id:
            raise AgentRuntimeError(
                "invocation id factory returned an empty value",
                code="agent.invocation.invalid-id",
                agent_id=agent_id,
            )
        context_tick = self._monotonic()
        definition = self._catalog.resolve(agent_id)
        snapshot = self._substrate.inspect(run_id)
        if snapshot.run.id != run_id:
            raise AgentRuntimeError(
                "substrate returned state for a different run",
                code="agent.run.invalid-identity",
                agent_id=agent_id,
                invocation_id=invocation_id,
            )
        if snapshot.run.plan_digest != self._catalog.plan_digest:
            raise AgentRuntimeError(
                f"run {run_id!r} was not deployed from the supplied plan",
                code="agent.run.plan-mismatch",
                agent_id=agent_id,
                invocation_id=invocation_id,
            )
        if snapshot.run.state != RunState.RUNNING:
            raise AgentRuntimeError(
                f"run {run_id!r} is {snapshot.run.state.value}, not running",
                code="agent.run.not-running",
                agent_id=agent_id,
                invocation_id=invocation_id,
            )
        observations = self._observe(run_id, definition)
        access = self._shared_access(run_id, definition)
        shared_state = (
            self._shared_state.snapshot(access)
            if self._shared_state is not None and access.limits
            else SharedStateSnapshot()
        )
        context = self._context(
            definition,
            invocation_id=invocation_id,
            run_id=run_id,
            intent=intent,
            invoked_at=started_at,
            observations=observations,
            shared_state=shared_state,
            coordination=coordination,
        )
        context_seconds = self._elapsed(context_tick)
        if self._audit is not None:
            self._audit.record(
                AuditEventType.AGENT_STARTED,
                context,
                {"context": context.model_dump(mode="json", by_alias=True)},
            )
        reasoning_tick = self._monotonic()
        provider: AgentExecutionAdapter | None = None
        authority: DecisionAuthority | None = None
        try:
            provider = self._execution_factory(definition)
            if definition.instance.decision_scenario is not None:
                metadata = coordination.metadata if coordination is not None else {}
                scheduled_tick = metadata.get("deadlineStartedAt")
                configured_deadline = duration_seconds(
                    definition.instance.decision_scenario["timing"]["decisionDeadline"]
                )
                deadline = (
                    float(scheduled_tick) + configured_deadline
                    if isinstance(scheduled_tick, (int, float))
                    else self._monotonic() + configured_deadline
                )
                authority = DecisionAuthority(deadline, self._monotonic)
                bind_authority = getattr(provider, "bind_authority", None)
                if callable(bind_authority):
                    bind_authority(authority)
                    with self._authority_lock:
                        self._active_authorities[agent_id] = (
                            authority,
                            provider,
                            invocation_id,
                        )
            execution = provider.run(context)
            response = execution.response
        except Exception as error:
            reasoning_seconds = self._elapsed(reasoning_tick)
            if self._audit is not None:
                self._audit.record(
                    AuditEventType.AGENT_FAILED,
                    context,
                    self._error_data(error),
                )
            failure_response = (
                getattr(provider, "failure_response", None)
                if provider is not None
                else None
            )
            failed_response = (
                cast(AgentResponse | None, failure_response(error))
                if callable(failure_response)
                else None
            )
            failed = self._failure(
                context,
                started_at,
                error,
                response=failed_response,
                timings=self._timings(
                    started_tick,
                    context_seconds=context_seconds,
                    reasoning_seconds=reasoning_seconds,
                ),
            )
            self._audit_finalized(context, failed)
            self._release_authority(agent_id, authority)
            return failed
        reasoning_seconds = self._elapsed(reasoning_tick)
        if self._audit is not None:
            self._audit.record(
                AuditEventType.AGENT_COMPLETED,
                context,
                {
                    "response": response.model_dump(mode="json", by_alias=True),
                    "runtime": {
                        "name": execution.runtime_name,
                        **execution.runtime_data,
                    },
                },
            )

        state = _OneShotPreparation(
            context=context,
            response=response,
            provider=provider,
            started_at=started_at,
            started_tick=started_tick,
            context_seconds=context_seconds,
            model_queueing_seconds=execution.model_queueing_seconds,
            reasoning_seconds=reasoning_seconds,
            state_changes=(),
            authority=getattr(execution, "authority", authority),
        )
        return PreparedAgentInvocation(
            context=context,
            response=response,
            token=state,
        )

    def execute(
        self,
        prepared: PreparedAgentInvocation,
        proposal: ActionProposal,
    ) -> ActionResult:
        """Commit one admitted proposal through the capability engine."""

        state = self._prepared_state(prepared)
        with state.action_start_lock:
            action_tick = self._monotonic()
            if state.authority is None:
                result = self._capabilities.execute(prepared.context, proposal)
            else:
                started, result = state.authority.execute_if_authorized(
                    lambda: self._capabilities.execute(prepared.context, proposal)
                )
                if not started:
                    reason = state.authority.revocation or "timeout"
                    return ActionResult(
                        run_id=prepared.context.run_id,
                        request_id=proposal.id,
                        status=ActionStatus.FAILED,
                        completed_at=self._clock(),
                        issue=RuntimeIssue(
                            code=(
                                "agent.bounded-decision.cancelled"
                                if reason == "cancelled"
                                else "agent.bounded-decision.timeout"
                            ),
                            message=f"decision authority revoked before action start: {reason}",
                            target=proposal.target,
                        ),
                    )
        elapsed = self._elapsed(action_tick)
        with state.action_timing_lock:
            state.action_total_seconds += elapsed
        return result

    def validate(
        self,
        prepared: PreparedAgentInvocation,
        proposal: ActionProposal,
    ) -> ActionResult | None:
        """Validate one proposal before it enters conflict arbitration."""

        self._prepared_state(prepared)
        try:
            self._check_shared_state(prepared)
        except SharedStateError as error:
            return self.reject(prepared, proposal, code=error.code, message=str(error))
        return self._capabilities.validate(prepared.context, proposal)

    def _check_shared_state(self, prepared: PreparedAgentInvocation) -> None:
        context = prepared.context
        updates = prepared.response.shared_state_updates
        if self._shared_state is None:
            if updates:
                raise SharedStateError(
                    "no shared-state store is configured", code="state.store.required"
                )
            return
        access = self._shared_access(
            context.run_id, self._catalog.resolve(context.agent_id)
        )
        current = self._shared_state.snapshot(access)
        for update in updates:
            access.namespace(update.scope)
        for read in prepared.response.shared_state_reads:
            access.namespace(read.scope)
        identities = {
            (read.scope, read.key) for read in prepared.response.shared_state_reads
        }
        identities.update((u.scope, u.key) for u in updates)
        explicit = {(u.scope, u.key): u.expected_version for u in updates}
        for scope, key in identities:
            now = getattr(current, scope).get(key)
            expected = explicit.get((scope, key))
            observed = self._snapshot_version(context, scope, key)
            if expected is None:
                expected = observed
            actual = now.version if now is not None else 0
            if actual != expected or expected != observed:
                raise SharedStateError(
                    f"shared-state version conflict for {scope}:{key}; expected {expected}, found {actual}",
                    code="state.version.conflict",
                    conflict=True,
                )

    @staticmethod
    def _snapshot_version(context: AgentContext, scope: str, key: str) -> int:
        entry = getattr(context.shared_state, scope).get(key)
        return entry.version if entry is not None else 0

    def reject(
        self,
        prepared: PreparedAgentInvocation,
        proposal: ActionProposal,
        *,
        code: str,
        message: str,
    ) -> ActionResult:
        """Normalize an arbitration rejection without calling a provider."""

        self._prepared_state(prepared)
        return ActionResult(
            run_id=prepared.context.run_id,
            request_id=proposal.id,
            status=ActionStatus.REJECTED,
            completed_at=self._clock(),
            issue=RuntimeIssue(
                code=code,
                message=message,
                target=proposal.target,
            ),
        )

    def complete(
        self,
        prepared: PreparedAgentInvocation,
        action_results: tuple[ActionResult, ...],
    ) -> AgentInvocationResult:
        """Finalize timings, session state, and status after arbitration."""

        state = self._prepared_state(prepared)
        expected_ids = tuple(proposal.id for proposal in state.response.proposals)
        actual_ids = tuple(result.request_id for result in action_results)
        if actual_ids != expected_ids:
            raise AgentRuntimeError(
                "completed action results do not match prepared proposals",
                code="agent.actions.result-mismatch",
                agent_id=state.context.agent_id,
                invocation_id=state.context.invocation_id,
            )
        if state.action_total_seconds == 0:
            action_tick = self._monotonic()
            state.action_total_seconds += self._elapsed(action_tick)
        effect_latencies = tuple(
            result.effect_latency_seconds
            for result in action_results
            if result.effect_latency_seconds is not None
        )
        effect_seconds = sum(effect_latencies) if effect_latencies else None
        action_seconds = max(
            0.0,
            state.action_total_seconds - (effect_seconds or 0.0),
        )
        timings = self._timings(
            state.started_tick,
            context_seconds=state.context_seconds,
            model_queueing_seconds=state.model_queueing_seconds,
            reasoning_seconds=state.reasoning_seconds,
            action_seconds=action_seconds,
            effect_seconds=effect_seconds,
        )
        if all(result.status == ActionStatus.SUCCEEDED for result in action_results):
            try:
                if (
                    state.response.shared_state_updates
                    or state.response.shared_state_reads
                ):
                    self._check_shared_state(prepared)
                definition = self._catalog.resolve(state.context.agent_id)
                state.state_changes = self._apply_shared_state(
                    state.context,
                    self._shared_access(state.context.run_id, definition),
                    state.response.shared_state_updates,
                )
            except SharedStateError as error:
                if self._audit is not None:
                    self._audit.record(
                        AuditEventType.SHARED_STATE_FAILED,
                        state.context,
                        self._error_data(error),
                    )
                failure = self._state_failure(
                    state.context,
                    state.started_at,
                    state.response,
                    error,
                    timings=timings,
                ).model_copy(update={"action_results": action_results})
                self._audit_finalized(state.context, failure)
                self._release_authority(state.context.agent_id, state.authority)
                return failure
        try:
            runtime_update = state.provider.record_action_results(
                state.context, action_results
            )
            if runtime_update is not None:
                update_data = cast(dict[str, Any], runtime_update)
                execution_record = cast(
                    dict[str, Any],
                    dict(cast(Any, state.response.metadata).get("executionRecord", {})),
                )
                execution_record["runtime"] = update_data.get("runtime", {})
                execution_record["outcome"] = update_data.get(
                    "outcome", execution_record.get("outcome")
                )
                state.response = state.response.model_copy(
                    update={
                        "metadata": {
                            **state.response.metadata,
                            "executionRecord": execution_record,
                        }
                    }
                )
                bounded_decision = (
                    cast(Any, execution_record)
                    .get("runtime", {})
                    .get("boundedDecision", {})
                )
                correctness_failure = bounded_decision.get("correctnessFailure")
                if execution_record.get("outcome") == "failed" and correctness_failure:
                    failed_result = AgentInvocationResult(
                        invocationId=state.context.invocation_id,
                        runId=state.context.run_id,
                        agentId=state.context.agent_id,
                        status=InvocationStatus.FAILED,
                        startedAt=state.started_at,
                        completedAt=self._clock(),
                        response=state.response,
                        actionResults=action_results,
                        sharedStateChanges=state.state_changes,
                        timings=timings,
                        issue=AgentRuntimeIssue(
                            code=correctness_failure["code"],
                            message=correctness_failure["message"],
                            agentId=state.context.agent_id,
                        ),
                    )
                    self._audit_finalized(state.context, failed_result)
                    self._release_authority(state.context.agent_id, state.authority)
                    return failed_result
        except Exception as error:
            if self._audit is not None:
                self._audit.record(
                    AuditEventType.AGENT_FAILED,
                    state.context,
                    self._error_data(error),
                )
            failed = AgentInvocationResult(
                invocationId=state.context.invocation_id,
                runId=state.context.run_id,
                agentId=state.context.agent_id,
                status=InvocationStatus.FAILED,
                startedAt=state.started_at,
                completedAt=self._clock(),
                response=state.response,
                actionResults=action_results,
                sharedStateChanges=state.state_changes,
                timings=timings,
                issue=AgentRuntimeIssue(
                    code=self._error_code(error),
                    message=str(error) or type(error).__name__,
                    agentId=state.context.agent_id,
                ),
            )
            self._audit_finalized(state.context, failed)
            self._release_authority(state.context.agent_id, state.authority)
            return failed
        result = self._result(
            state.context,
            state.started_at,
            state.response,
            action_results,
            state.state_changes,
            timings,
        )
        self._audit_finalized(state.context, result)
        self._release_authority(state.context.agent_id, state.authority)
        return result

    def _audit_finalized(
        self, context: AgentContext, result: AgentInvocationResult
    ) -> None:
        """Append lifecycle-complete details without changing audit envelopes."""
        if self._audit is None:
            return
        response = (
            result.response.model_dump(mode="json", by_alias=True)
            if result.response is not None
            else None
        )
        metadata = (
            cast(Any, result.response.metadata) if result.response is not None else {}
        )
        execution_record = metadata.get("executionRecord")
        data: dict[str, JsonValue] = {
            "phase": "lifecycle-finalized",
            "invocationId": result.invocation_id,
            "status": result.status.value,
            "response": response,
            "executionRecord": execution_record,
            "actionResults": [
                action.model_dump(mode="json", by_alias=True)
                for action in result.action_results
            ],
            "sharedStateChanges": [
                change.model_dump(mode="json", by_alias=True, exclude_none=True)
                for change in result.shared_state_changes
            ],
        }
        if result.issue is not None:
            data["issue"] = result.issue.model_dump(mode="json", by_alias=True)
        event_type = (
            AuditEventType.AGENT_COMPLETED
            if result.status == InvocationStatus.SUCCEEDED
            else AuditEventType.AGENT_FAILED
        )
        self._audit.record(event_type, context, data)

    def cancel_bounded_decision(self, agent_id: str) -> bool:
        """Revoke local commitment authority; this does not prove remote stop."""
        with self._authority_lock:
            active = self._active_authorities.get(agent_id)
        if active is None:
            return False
        authority, provider, invocation_id = active
        cancel = getattr(provider, "cancel_local", None)
        if callable(cancel):
            cancel(invocation_id)
        else:
            authority.revoke("cancelled")
        return True

    def _release_authority(
        self, agent_id: str, authority: DecisionAuthority | None
    ) -> None:
        if authority is None:
            return
        with self._authority_lock:
            active = self._active_authorities.get(agent_id)
            if active is not None and active[0] is authority:
                del self._active_authorities[agent_id]

    @staticmethod
    def _prepared_state(prepared: PreparedAgentInvocation) -> _OneShotPreparation:
        state = prepared.token
        if (
            not isinstance(state, _OneShotPreparation)
            or state.context != prepared.context
            or state.response != prepared.response
        ):
            raise AgentRuntimeError(
                "prepared invocation does not belong to this agent runtime",
                code="agent.invocation.invalid-preparation",
                agent_id=prepared.context.agent_id,
                invocation_id=prepared.context.invocation_id,
            )
        return state

    def _observe(
        self,
        run_id: str,
        definition: AgentExecutionDefinition,
    ) -> dict[str, JsonValue]:
        observations: dict[str, JsonValue] = {}
        for name in definition.instance.observes:
            query = ObservationQuery(
                name=name,
                targets=definition.instance.attachment.targets,
            )
            result = self._substrate.observe(
                run_id,
                query,
            )
            if result.run_id != run_id or result.query != query:
                raise AgentRuntimeError(
                    f"observation {name!r} returned mismatched identity",
                    code="agent.observation.invalid-identity",
                    agent_id=definition.instance.id,
                )
            try:
                observations[name] = _JSON_OBJECT.validate_python(result.values)
            except (TypeError, ValueError, ValidationError) as error:
                raise AgentRuntimeError(
                    f"observation {name!r} returned non-JSON data: {error}",
                    code="agent.observation.invalid",
                    agent_id=definition.instance.id,
                ) from error
        return observations

    @staticmethod
    def _context(
        definition: AgentExecutionDefinition,
        *,
        invocation_id: str,
        run_id: str,
        intent: str,
        invoked_at: datetime,
        observations: dict[str, JsonValue],
        shared_state: SharedStateSnapshot,
        coordination: AgentCoordinationContext | None,
    ) -> AgentContext:
        instance = definition.instance
        attachment = instance.attachment
        return AgentContext(
            invocationId=invocation_id,
            runId=run_id,
            agentId=instance.id,
            deployment=instance.deployment,
            layer=attachment.layer,
            customLayer=attachment.custom_layer,
            targetKind=attachment.target_kind,
            targets=attachment.targets,
            capabilities=instance.capabilities,
            observations=observations,
            sharedState=shared_state,
            coordination=coordination,
            intent=intent,
            priority=instance.priority,
            invokedAt=invoked_at,
        )

    def _result(
        self,
        context: AgentContext,
        started_at: datetime,
        response: AgentResponse,
        action_results: tuple[ActionResult, ...],
        state_changes: tuple[SharedStateChange, ...],
        timings: InvocationTimings,
    ) -> AgentInvocationResult:
        unsuccessful = next(
            (
                result
                for result in action_results
                if result.status != ActionStatus.SUCCEEDED
            ),
            None,
        )
        if unsuccessful is None:
            return AgentInvocationResult(
                invocationId=context.invocation_id,
                runId=context.run_id,
                agentId=context.agent_id,
                status=InvocationStatus.SUCCEEDED,
                startedAt=started_at,
                completedAt=self._clock(),
                response=response,
                actionResults=action_results,
                sharedStateChanges=state_changes,
                timings=timings,
            )
        issue = unsuccessful.issue
        if issue is None:
            raise AssertionError("unsuccessful action result has no issue")
        status = (
            InvocationStatus.REJECTED
            if unsuccessful.status == ActionStatus.REJECTED
            else InvocationStatus.FAILED
        )
        return AgentInvocationResult(
            invocationId=context.invocation_id,
            runId=context.run_id,
            agentId=context.agent_id,
            status=status,
            startedAt=started_at,
            completedAt=self._clock(),
            response=response,
            actionResults=action_results,
            sharedStateChanges=state_changes,
            timings=timings,
            issue=AgentRuntimeIssue(
                code=issue.code,
                message=issue.message,
                agentId=context.agent_id,
                target=issue.target,
            ),
        )

    def _shared_access(
        self,
        run_id: str,
        definition: AgentExecutionDefinition,
    ) -> SharedStateAccess:
        configuration = definition.instance.memory.shared
        limits: dict[SharedScope, int] = {}
        if configuration is not None:
            for scope in configuration.scopes:
                candidates = []
                for instance in self._agent_instances:
                    shared = instance.memory.shared
                    if shared is None or scope not in shared.scopes:
                        continue
                    if (
                        scope == "deployment"
                        and instance.deployment != definition.instance.deployment
                    ):
                        continue
                    candidates.append(shared.max_entries)
                limits[scope] = min(candidates)
        return SharedStateAccess(
            run_id=run_id,
            deployment=definition.instance.deployment,
            agent_id=definition.instance.id,
            limits=limits,
        )

    def _apply_shared_state(
        self,
        context: AgentContext,
        access: SharedStateAccess,
        updates: tuple[SharedStateUpdate, ...],
    ) -> tuple[SharedStateChange, ...]:
        if not updates:
            return ()
        if self._shared_state is None:
            raise SharedStateError(
                "agent proposed shared-state updates without a configured store",
                code="state.store.required",
            )
        # Missing expectations are bound to the invocation's snapshot, never
        # interpreted as an unconditional overwrite of a concurrent decision.
        versioned = tuple(
            update.model_copy(
                update={
                    "expected_version": (
                        update.expected_version
                        if update.expected_version is not None
                        else (self._snapshot_version(context, update.scope, update.key))
                    )
                }
            )
            for update in updates
        )
        changes = self._shared_state.apply(access, versioned)
        if self._audit is not None:
            self._audit.record(
                AuditEventType.SHARED_STATE_UPDATED,
                context,
                {
                    "changes": [
                        change.model_dump(
                            mode="json",
                            by_alias=True,
                            exclude_none=True,
                        )
                        for change in changes
                    ]
                },
            )
        return changes

    def _state_failure(
        self,
        context: AgentContext,
        started_at: datetime,
        response: AgentResponse,
        error: SharedStateError,
        *,
        timings: InvocationTimings,
    ) -> AgentInvocationResult:
        return AgentInvocationResult(
            invocationId=context.invocation_id,
            runId=context.run_id,
            agentId=context.agent_id,
            status=(
                InvocationStatus.REJECTED if error.conflict else InvocationStatus.FAILED
            ),
            startedAt=started_at,
            completedAt=self._clock(),
            response=response,
            timings=timings,
            issue=AgentRuntimeIssue(
                code=error.code,
                message=str(error),
                agentId=context.agent_id,
            ),
        )

    def _failure(
        self,
        context: AgentContext,
        started_at: datetime,
        error: Exception,
        *,
        response: AgentResponse | None = None,
        timings: InvocationTimings,
    ) -> AgentInvocationResult:
        return AgentInvocationResult(
            invocationId=context.invocation_id,
            runId=context.run_id,
            agentId=context.agent_id,
            status=InvocationStatus.FAILED,
            startedAt=started_at,
            completedAt=self._clock(),
            response=response,
            timings=timings,
            issue=AgentRuntimeIssue(
                code=self._error_code(error),
                message=str(error) or type(error).__name__,
                agentId=context.agent_id,
            ),
        )

    def _elapsed(self, started: float) -> float:
        return max(0.0, self._monotonic() - started)

    def _timings(
        self,
        started: float,
        *,
        context_seconds: float,
        model_queueing_seconds: float | None = None,
        reasoning_seconds: float,
        action_seconds: float = 0,
        effect_seconds: float | None = None,
    ) -> InvocationTimings:
        return InvocationTimings(
            contextBuildSeconds=context_seconds,
            modelQueueingSeconds=model_queueing_seconds,
            reasoningSeconds=reasoning_seconds,
            actionExecutionSeconds=action_seconds,
            actionEffectSeconds=effect_seconds,
            totalSeconds=self._elapsed(started),
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        code = getattr(error, "code", None)
        if isinstance(code, str) and code:
            return code
        if isinstance(error, TimeoutError):
            return "agent.invocation.timeout"
        return "agent.invocation.failed"

    @classmethod
    def _error_data(cls, error: Exception) -> dict[str, JsonValue]:
        return {
            "code": cls._error_code(error),
            "errorType": type(error).__name__,
            "message": str(error) or type(error).__name__,
        }
