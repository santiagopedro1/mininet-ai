"""Ownership of one complete, continuously running experiment."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from pydantic import Field, JsonValue

from mininet_ai.agents.agno import AgnoAgentFactory
from mininet_ai.agents.bounded import DecisionTransport, NativeSystemOneClient
from mininet_ai.agents.runtime import OneShotAgentRuntime
from mininet_ai.audit import AuditRecorder
from mininet_ai.compiler import DeploymentPlan
from mininet_ai.coordination import CoordinationRuntime
from mininet_ai.durations import duration_seconds
from mininet_ai.errors import AgentRuntimeError, MininetAIError, describe_failure
from mininet_ai.outcomes import CleanupOutcome, OperationFailure, OperationOutcome
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime.continuous import ContinuousAgentRuntime, ContinuousRuntimeState
from mininet_ai.runtime.contracts import (
    ContinuousInvocationRecord,
    ContinuousRuntimeIssue,
    ContinuousRuntimeReport,
    ManualIntentPayload,
    RuntimeEvent,
    RuntimeEventType,
    TelemetryPipelineReport,
)
from mininet_ai.runtime.events import InMemoryRuntimeEventBus
from mininet_ai.runtime.ledger import (
    LedgerEntry,
    LedgerEventSink,
    LedgerRecordCategory,
    PluginManifest,
    RunLedger,
    RunManifest,
)
from mininet_ai.runtime.state import SharedStateStore
from mininet_ai.runtime.telemetry import TelemetryPipeline, TelemetryPipelineState
from mininet_ai.sdk import ExecutionCatalog
from mininet_ai.specification.models import StrictModel
from mininet_ai.substrates import RunInfo, SubstrateRuntime, TeardownResult

Clock = Callable[[], datetime]
EventIdFactory = Callable[[], str]
MonotonicClock = Callable[[], float]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _event_id() -> str:
    return f"event-{uuid4()}"


class ExperimentRuntimeState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class ExperimentRuntimeIssue(StrictModel):
    phase: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ExperimentRuntimeReport(StrictModel):
    run: RunInfo
    state: ExperimentRuntimeState
    continuous: ContinuousRuntimeReport
    telemetry: TelemetryPipelineReport
    teardown: TeardownResult | None = None
    issues: tuple[ExperimentRuntimeIssue, ...] = ()
    operation: OperationOutcome = OperationOutcome.UNKNOWN
    cleanup: CleanupOutcome = Field(default_factory=CleanupOutcome)
    teardown_error: OperationFailure | None = None
    previous_issues: tuple[ExperimentRuntimeIssue, ...] = ()


class ExperimentRuntimeError(MininetAIError):
    def __init__(
        self, message: str, *, code: str, cleanup: CleanupOutcome | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cleanup = cleanup or CleanupOutcome()
        self.operation = OperationOutcome.FAILED


class ExperimentRuntime:
    """Own substrate, agents, telemetry, events, and orderly shutdown."""

    def __init__(
        self,
        plan: DeploymentPlan,
        substrate: SubstrateRuntime,
        registries: ProviderRegistries,
        *,
        audit: AuditRecorder | None = None,
        agent_factory: AgnoAgentFactory | None = None,
        shared_state: SharedStateStore | None = None,
        ledger: RunLedger | None = None,
        plugins: tuple[PluginManifest, ...] = (),
        clock: Clock = _utc_now,
        event_id_factory: EventIdFactory = _event_id,
        invocation_listener: Callable[[ContinuousInvocationRecord], None] | None = None,
        decision_transport: object | None = None,
        monotonic_clock: MonotonicClock = monotonic,
        runtime_issue_listener: Callable[[ContinuousRuntimeIssue], None] | None = None,
    ) -> None:
        self._plan = plan
        self._substrate = substrate
        self._registries = registries
        self._audit = audit
        self._agent_factory = agent_factory or (
            AgnoAgentFactory()
            if any(agent.decision_scenario is None for agent in plan.agents)
            else None
        )
        self._decision_transport = decision_transport
        self._monotonic = monotonic_clock
        self._bounded_startup: dict[str, dict[str, JsonValue]] = {}
        self._shared_state = shared_state
        self._ledger = ledger
        self._plugins = plugins
        self._clock = clock
        self._event_id_factory = event_id_factory
        self._invocation_listener = invocation_listener
        self._runtime_issue_listener = runtime_issue_listener
        self._lock = Lock()
        self._state = ExperimentRuntimeState.CREATED
        self._run: RunInfo | None = None
        self._continuous: ContinuousAgentRuntime | None = None
        self._one_shot: OneShotAgentRuntime | None = None
        self._telemetry: TelemetryPipeline | None = None
        self._continuous_started = False
        self._telemetry_started = False
        self._writers_stopped = False
        self._report: ExperimentRuntimeReport | None = None
        self._cleanup = CleanupOutcome()
        self._start_failed = False
        self._operation_failed = False
        self._teardown_error: OperationFailure | None = None
        self._source_sequences: dict[str, int] = {}

    @property
    def cleanup(self) -> CleanupOutcome:
        """Latest substrate cleanup evidence, including failed startup."""
        return self._cleanup

    @property
    def teardown_error(self) -> OperationFailure | None:
        """Latest teardown failure, including cleanup during failed startup."""
        return self._teardown_error

    @property
    def writers_stopped(self) -> bool:
        """Verified managed cleanup, separate from experiment success/failure."""
        with self._lock:
            return self._writers_stopped

    @property
    def state(self) -> ExperimentRuntimeState:
        with self._lock:
            return self._state

    @property
    def run(self) -> RunInfo:
        with self._lock:
            if self._run is None:
                raise ExperimentRuntimeError(
                    "experiment runtime has not started",
                    code="experiment.not-started",
                )
            return self._run

    @property
    def agent_ids(self) -> tuple[str, ...]:
        """Compiled instance IDs in deployment-plan order."""
        return tuple(agent.id for agent in self._plan.agents)

    @property
    def manual_agent_ids(self) -> tuple[str, ...]:
        """Instances eligible for manual intents, before coordination routing."""
        return tuple(
            agent.id
            for agent in self._plan.agents
            if any(trigger.type == "manual" for trigger in agent.triggers)
        )

    def cancel_bounded_decision(self, agent_id: str) -> bool:
        """Revoke active local admission authority without claiming remote stop."""
        with self._lock:
            runtime = self._one_shot
        if runtime is None or not any(
            agent.id == agent_id and agent.decision_scenario is not None
            for agent in self._plan.agents
        ):
            return False
        return runtime.cancel_bounded_decision(agent_id)

    def start(self) -> RunInfo:
        """Deploy and start all continuous experiment producers and consumers."""

        with self._lock:
            if self._state != ExperimentRuntimeState.CREATED:
                raise ExperimentRuntimeError(
                    f"experiment runtime is already {self._state.value}",
                    code="experiment.lifecycle.invalid",
                )
            self._state = ExperimentRuntimeState.STARTING
        try:
            self._verify_bounded_startup()
            run = self._substrate.deploy(self._plan)
            self._run = run
            self._create_manifest(run)
            self._record_lifecycle("run.starting", {"state": "starting"})
            event_bus = InMemoryRuntimeEventBus(
                capacity=self._plan.resource_limits.max_queued_events,
                sink=(
                    LedgerEventSink(self._ledger) if self._ledger is not None else None
                ),
            )
            invoker = OneShotAgentRuntime(
                self._plan,
                self._substrate,
                self._registries,
                audit=self._audit,
                agent_factory=self._agent_factory,
                shared_state=self._shared_state,
                decision_transport=cast(
                    DecisionTransport | None, self._decision_transport
                ),
                bounded_startup=self._bounded_startup,
                monotonic_clock=self._monotonic,
            )
            self._one_shot = invoker
            coordinator = CoordinationRuntime(self._plan, invoker)
            continuous = ContinuousAgentRuntime(
                self._plan,
                run.id,
                coordinator,
                event_bus=event_bus,
                invocation_listener=self._invocation_listener,
                clock=self._clock,
                event_id_factory=self._event_id_factory,
                monotonic_clock=self._monotonic,
                issue_listener=self._runtime_issue_listener,
            )
            telemetry = TelemetryPipeline(
                self._plan,
                run.id,
                self._substrate,
                continuous,
                clock=self._clock,
                event_id_factory=self._event_id_factory,
            )
            self._continuous = continuous
            self._telemetry = telemetry
            self._continuous_started = True
            continuous.start()
            self._telemetry_started = True
            telemetry.start()
            with self._lock:
                self._state = ExperimentRuntimeState.RUNNING
            self._record_lifecycle("run.running", {"state": "running"})
            return run
        except BaseException as error:
            self._start_failed = True
            self._cleanup = getattr(error, "cleanup", CleanupOutcome())
            self._cleanup_start_failure()
            with self._lock:
                self._state = ExperimentRuntimeState.FAILED
            if not isinstance(error, Exception) or isinstance(error, MininetAIError):
                if isinstance(error, MininetAIError):
                    error.with_cleanup(
                        self._cleanup, teardown_error=self._teardown_error
                    )
                raise
            raise ExperimentRuntimeError(
                f"could not start experiment runtime: {error}",
                code="experiment.start.failed",
                cleanup=self._cleanup,
            ).with_cleanup(
                self._cleanup, teardown_error=self._teardown_error
            ) from error

    def _verify_bounded_startup(self) -> None:
        bounded = [
            agent for agent in self._plan.agents if agent.decision_scenario is not None
        ]
        if not bounded:
            return
        from mininet_ai.transports.http import HttpxJsonTransport

        transport = cast(
            DecisionTransport,
            self._decision_transport or HttpxJsonTransport(),
        )
        catalog = ExecutionCatalog(self._plan)
        for agent in bounded:
            definition = catalog.resolve(agent.id)
            model = definition.blueprint.model
            if model is None or not hasattr(model, "parameters"):
                raise AgentRuntimeError(
                    "bounded startup requires resolved model settings",
                    code="agent.bounded-decision.startup-configuration-invalid",
                    agent_id=agent.id,
                )
            scenario = agent.decision_scenario
            if scenario is None:
                raise AgentRuntimeError(
                    "bounded startup requires a decision scenario",
                    code="agent.bounded-decision.startup-configuration-invalid",
                    agent_id=agent.id,
                )
            model = cast(Any, model)
            choices = {
                item["id"]: tuple(item["proposals"]) for item in scenario["choices"]
            }
            client = NativeSystemOneClient(model.parameters["endpoint"], transport)
            try:
                self._bounded_startup[agent.id] = client.verify_startup(
                    model_name=model.name,
                    expected_identity=model.parameters["expectedIdentity"],
                    instructions=scenario["instructions"],
                    criteria={
                        item["id"]: item["description"] for item in scenario["choices"]
                    },
                    choices=choices,
                    interface_timeout_seconds=duration_seconds(
                        scenario["timing"]["decisionDeadline"]
                    ),
                )
            except AgentRuntimeError as error:
                error.agent_id = agent.id
                raise

    def submit_intent(
        self,
        agent_id: str,
        intent: str,
        *,
        source: str = "operator",
    ) -> RuntimeEvent:
        """Publish one manual intent through the normalized runtime event path."""

        if not agent_id or not intent or not source:
            raise ValueError("manual intent requires agent, intent, and source")
        continuous = self._require_running()
        agent = next(
            (agent for agent in self._plan.agents if agent.id == agent_id), None
        )
        if agent is None:
            raise ExperimentRuntimeError(
                f"unknown agent {agent_id!r}; valid agents: {', '.join(self.agent_ids)}",
                code="experiment.intent.invalid-agent",
            )
        if agent_id not in self.manual_agent_ids:
            raise ExperimentRuntimeError(
                f"agent {agent_id!r} has no manual trigger",
                code="experiment.intent.invalid-agent",
            )
        with self._lock:
            sequence = self._source_sequences.get(source, 0)
            self._source_sequences[source] = sequence + 1
        occurred_at = self._clock()
        payload = ManualIntentPayload(agentId=agent_id, intent=intent)
        event = RuntimeEvent(
            eventId=self._event_id_factory(),
            runId=self.run.id,
            type=RuntimeEventType.MANUAL_INTENT,
            source=source,
            subject=agent_id,
            occurredAt=occurred_at,
            observedAt=occurred_at,
            sequence=sequence,
            payload=payload.model_dump(mode="json", by_alias=True),
        )
        continuous.publish(event)
        return event

    def pause_agent(self, agent_id: str) -> None:
        self._require_running().pause(agent_id)

    def resume_agent(self, agent_id: str) -> None:
        self._require_running().resume(agent_id)

    def stop(
        self,
        *,
        drain: bool = True,
        timeout_seconds: float = 30,
    ) -> ExperimentRuntimeReport:
        """Stop all owned runtime layers and return their final reports."""

        if timeout_seconds <= 0:
            raise ValueError("experiment stop timeout must be positive")
        with self._lock:
            if self._report is not None and self._writers_stopped:
                return self._report
            retrying = (
                self._state == ExperimentRuntimeState.FAILED
                and self._run is not None
                and not self._writers_stopped
            )
            if self._state != ExperimentRuntimeState.RUNNING and not retrying:
                raise ExperimentRuntimeError(
                    f"cannot stop experiment runtime from {self._state.value}",
                    code="experiment.lifecycle.invalid",
                )
            self._state = ExperimentRuntimeState.STOPPING
        previous_issues = (
            self._report.previous_issues + self._report.issues
            if self._report is not None
            else ()
        )
        issues: list[ExperimentRuntimeIssue] = []
        self._record_lifecycle(
            "run.stop-retrying" if retrying else "run.stopping",
            {"state": "stopping", "retry": retrying},
            issues=issues,
        )
        telemetry_report, telemetry_stopped = self._stop_telemetry(
            timeout_seconds,
            issues,
        )
        continuous_report, continuous_stopped = self._stop_continuous(
            drain,
            timeout_seconds,
            issues,
        )
        safe_to_teardown = telemetry_stopped and continuous_stopped
        teardown = self._teardown(issues) if safe_to_teardown else None
        self._operation_failed = (
            self._operation_failed
            or self._start_failed
            # Worker-drain and substrate cleanup uncertainty can be resolved by
            # retry; they do not retroactively fail completed experiment work.
            or any(issue.phase in {"agents", "ledger"} for issue in issues)
            or bool(continuous_report.failed)
        )
        operation = (
            OperationOutcome.FAILED
            if self._operation_failed
            else OperationOutcome.SUCCEEDED
        )
        if teardown is None or not teardown.succeeded:
            issues.append(
                ExperimentRuntimeIssue(
                    phase="cleanup",
                    code="experiment.cleanup.unverified",
                    message="cleanup is not verified; inspect run status and seek administrator help for unresolved resources",
                )
            )
        final_state = (
            ExperimentRuntimeState.FAILED
            if issues or operation == OperationOutcome.FAILED
            else ExperimentRuntimeState.STOPPED
        )
        self._record_lifecycle(
            "run.failed"
            if final_state == ExperimentRuntimeState.FAILED
            else "run.stopped",
            {"state": final_state.value, "issues": len(issues)},
            issues=issues,
        )
        if any(issue.phase == "ledger" for issue in issues):
            self._operation_failed = True
            operation = OperationOutcome.FAILED
        if issues:
            final_state = ExperimentRuntimeState.FAILED
        with self._lock:
            self._state = final_state
            self._writers_stopped = (
                safe_to_teardown
                and not issues
                and teardown is not None
                and teardown.succeeded
            )
        report = ExperimentRuntimeReport(
            run=(teardown.run if teardown is not None else self.run),
            state=final_state,
            continuous=continuous_report,
            telemetry=telemetry_report,
            teardown=teardown,
            issues=tuple(issues),
            operation=operation,
            cleanup=self._cleanup,
            teardown_error=self._teardown_error,
            previous_issues=previous_issues,
        )
        with self._lock:
            self._report = report
        return report

    def _require_running(self) -> ContinuousAgentRuntime:
        with self._lock:
            if (
                self._state != ExperimentRuntimeState.RUNNING
                or self._continuous is None
            ):
                raise ExperimentRuntimeError(
                    "experiment runtime is not running",
                    code="experiment.not-running",
                )
            return self._continuous

    def _create_manifest(self, run: RunInfo) -> None:
        if self._ledger is None:
            return
        self._ledger.create_run(
            RunManifest.from_plan(
                run.id,
                self._plan,
                created_at=run.started_at,
                plugins=self._plugins,
                configuration={"mode": "continuous"},
            )
        )

    def _record_lifecycle(
        self,
        event_type: str,
        data: dict[str, JsonValue],
        *,
        issues: list[ExperimentRuntimeIssue] | None = None,
    ) -> None:
        if self._ledger is None or self._run is None:
            return
        try:
            self._ledger.append(
                LedgerEntry(
                    runId=self._run.id,
                    category=LedgerRecordCategory.RUN_LIFECYCLE,
                    type=event_type,
                    recordedAt=self._clock(),
                    data=data,
                )
            )
        except Exception as error:
            if issues is None:
                raise
            issues.append(self._issue("ledger", error))

    def _stop_telemetry(
        self,
        timeout_seconds: float,
        issues: list[ExperimentRuntimeIssue],
    ) -> tuple[TelemetryPipelineReport, bool]:
        if self._telemetry is None:
            return TelemetryPipelineReport(), True
        try:
            report = self._telemetry.stop(timeout_seconds=timeout_seconds)
            self._telemetry_started = False
            return report, True
        except Exception as error:
            issues.append(self._issue("telemetry", error))
            return self._telemetry.report(), False

    def _stop_continuous(
        self,
        drain: bool,
        timeout_seconds: float,
        issues: list[ExperimentRuntimeIssue],
    ) -> tuple[ContinuousRuntimeReport, bool]:
        if self._continuous is None:
            return ContinuousRuntimeReport(), True
        try:
            report = self._continuous.stop(
                drain=drain,
                timeout_seconds=timeout_seconds,
            )
            self._continuous_started = False
            return report, True
        except Exception as error:
            issues.append(self._issue("continuous", error))
            return self._continuous.report(), False

    def _teardown(
        self,
        issues: list[ExperimentRuntimeIssue],
    ) -> TeardownResult | None:
        self._teardown_error = None
        try:
            if self._agent_factory is not None:
                self._agent_factory.close()
        except Exception as error:
            issues.append(self._issue("agents", error))
        if self._run is None:
            return None
        try:
            result = self._substrate.teardown(self._run.id)
            self._cleanup = result.cleanup
            return result
        except Exception as error:
            self._teardown_error = describe_failure(error)
            self._cleanup = self._teardown_error.cleanup
            issues.append(self._issue("substrate", error))
            return None

    def _cleanup_start_failure(self) -> None:
        issues: list[ExperimentRuntimeIssue] = []
        safe_to_teardown = True
        if (
            self._telemetry is not None
            and self._telemetry_started
            and self._telemetry.state != TelemetryPipelineState.CREATED
        ):
            try:
                self._telemetry.stop()
                self._telemetry_started = False
            except Exception as error:
                safe_to_teardown = False
                issues.append(self._issue("telemetry", error))
        if (
            self._continuous is not None
            and self._continuous_started
            and self._continuous.state != ContinuousRuntimeState.CREATED
        ):
            try:
                self._continuous.stop(drain=False)
                self._continuous_started = False
            except Exception as error:
                safe_to_teardown = False
                issues.append(self._issue("continuous", error))
        if safe_to_teardown:
            teardown = self._teardown(issues)
            with self._lock:
                self._writers_stopped = not issues and (
                    (self._run is None and self._cleanup.verified)
                    or (teardown is not None and teardown.succeeded)
                )

    @staticmethod
    def _issue(phase: str, error: Exception) -> ExperimentRuntimeIssue:
        code = getattr(error, "code", None)
        return ExperimentRuntimeIssue(
            phase=phase,
            code=(code if isinstance(code, str) and code else f"{phase}.failed"),
            message=str(error) or type(error).__name__,
        )
