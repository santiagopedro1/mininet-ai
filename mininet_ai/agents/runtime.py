"""Bounded orchestration for one manually requested agent invocation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError

from mininet_ai.agents.agno import AgnoAgentFactory, AgnoAgentProvider
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


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _invocation_id() -> str:
    return f"invoke-{uuid4()}"


@dataclass
class _OneShotPreparation:
    context: AgentContext
    response: AgentResponse
    provider: AgnoAgentProvider
    started_at: datetime
    started_tick: float
    context_seconds: float
    model_queueing_seconds: float
    reasoning_seconds: float
    state_changes: tuple[SharedStateChange, ...]
    action_total_seconds: float = 0
    action_timing_lock: Lock = field(default_factory=Lock)


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
        shared_state: SharedStateStore | None = None,
        clock: Clock = _utc_now,
        monotonic_clock: MonotonicClock = monotonic,
        invocation_id_factory: InvocationIdFactory = _invocation_id,
    ) -> None:
        self._catalog = ExecutionCatalog(plan)
        self._substrate = substrate
        self._audit = audit
        self._agent_factory = agent_factory or AgnoAgentFactory()
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
            self.execute(prepared, proposal)
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
        """Run reasoning and shared-state updates without committing actions."""

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
        try:
            provider = AgnoAgentProvider(
                definition,
                factory=self._agent_factory,
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
            return self._failure(
                context,
                started_at,
                error,
                timings=self._timings(
                    started_tick,
                    context_seconds=context_seconds,
                    reasoning_seconds=reasoning_seconds,
                ),
            )
        reasoning_seconds = self._elapsed(reasoning_tick)
        if self._audit is not None:
            execution_data = execution.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
            execution_data.pop("response")
            self._audit.record(
                AuditEventType.AGENT_COMPLETED,
                context,
                {
                    "response": response.model_dump(mode="json", by_alias=True),
                    "runtime": {"name": "agno", **execution_data},
                },
            )

        try:
            state_changes = self._apply_shared_state(
                context,
                access,
                response.shared_state_updates,
            )
        except SharedStateError as error:
            if self._audit is not None:
                self._audit.record(
                    AuditEventType.SHARED_STATE_FAILED,
                    context,
                    self._error_data(error),
                )
            return self._state_failure(
                context,
                started_at,
                response,
                error,
                timings=self._timings(
                    started_tick,
                    context_seconds=context_seconds,
                    model_queueing_seconds=execution.model_queueing_seconds,
                    reasoning_seconds=reasoning_seconds,
                ),
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
            state_changes=state_changes,
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
        action_tick = self._monotonic()
        result = self._capabilities.execute(prepared.context, proposal)
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
        return self._capabilities.validate(prepared.context, proposal)

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
        effect_seconds = (
            sum(effect_latencies) if effect_latencies else None
        )
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
        try:
            state.provider.record_action_results(state.context, action_results)
        except Exception as error:
            if self._audit is not None:
                self._audit.record(
                    AuditEventType.AGENT_FAILED,
                    state.context,
                    self._error_data(error),
                )
            return AgentInvocationResult(
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
        return self._result(
            state.context,
            state.started_at,
            state.response,
            action_results,
            state.state_changes,
            timings,
        )

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
        changes = self._shared_state.apply(access, updates)
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
                InvocationStatus.REJECTED
                if error.conflict
                else InvocationStatus.FAILED
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
        timings: InvocationTimings,
    ) -> AgentInvocationResult:
        return AgentInvocationResult(
            invocationId=context.invocation_id,
            runId=context.run_id,
            agentId=context.agent_id,
            status=InvocationStatus.FAILED,
            startedAt=started_at,
            completedAt=self._clock(),
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
