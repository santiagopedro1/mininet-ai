"""Ownership of one complete, continuously running experiment."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from uuid import uuid4

from pydantic import Field, JsonValue

from mininet_ai.agents.agno import AgnoAgentFactory
from mininet_ai.agents.runtime import OneShotAgentRuntime
from mininet_ai.audit import AuditRecorder
from mininet_ai.compiler import DeploymentPlan
from mininet_ai.errors import MininetAIError
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime.continuous import ContinuousAgentRuntime
from mininet_ai.runtime.contracts import (
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
from mininet_ai.runtime.telemetry import TelemetryPipeline
from mininet_ai.specification.models import StrictModel
from mininet_ai.substrates import RunInfo, SubstrateRuntime, TeardownResult


Clock = Callable[[], datetime]
EventIdFactory = Callable[[], str]


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


class ExperimentRuntimeError(MininetAIError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


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
    ) -> None:
        self._plan = plan
        self._substrate = substrate
        self._registries = registries
        self._audit = audit
        self._agent_factory = agent_factory
        self._shared_state = shared_state
        self._ledger = ledger
        self._plugins = plugins
        self._clock = clock
        self._event_id_factory = event_id_factory
        self._lock = Lock()
        self._state = ExperimentRuntimeState.CREATED
        self._run: RunInfo | None = None
        self._continuous: ContinuousAgentRuntime | None = None
        self._telemetry: TelemetryPipeline | None = None
        self._report: ExperimentRuntimeReport | None = None
        self._source_sequences: dict[str, int] = {}

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
            run = self._substrate.deploy(self._plan)
            self._run = run
            self._create_manifest(run)
            self._record_lifecycle("run.starting", {"state": "starting"})
            event_bus = InMemoryRuntimeEventBus(
                capacity=self._plan.resource_limits.max_queued_events,
                sink=(
                    LedgerEventSink(self._ledger)
                    if self._ledger is not None
                    else None
                ),
            )
            invoker = OneShotAgentRuntime(
                self._plan,
                self._substrate,
                self._registries,
                audit=self._audit,
                agent_factory=self._agent_factory,
                shared_state=self._shared_state,
            )
            continuous = ContinuousAgentRuntime(
                self._plan,
                run.id,
                invoker,
                event_bus=event_bus,
                clock=self._clock,
                event_id_factory=self._event_id_factory,
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
            continuous.start()
            telemetry.start()
            with self._lock:
                self._state = ExperimentRuntimeState.RUNNING
            self._record_lifecycle("run.running", {"state": "running"})
            return run
        except Exception as error:
            self._cleanup_start_failure()
            with self._lock:
                self._state = ExperimentRuntimeState.FAILED
            if isinstance(error, MininetAIError):
                raise
            raise ExperimentRuntimeError(
                f"could not start experiment runtime: {error}",
                code="experiment.start.failed",
            ) from error

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
            if self._report is not None:
                return self._report
            if self._state != ExperimentRuntimeState.RUNNING:
                raise ExperimentRuntimeError(
                    f"cannot stop experiment runtime from {self._state.value}",
                    code="experiment.lifecycle.invalid",
                )
            self._state = ExperimentRuntimeState.STOPPING
        issues: list[ExperimentRuntimeIssue] = []
        self._record_lifecycle(
            "run.stopping",
            {"state": "stopping"},
            issues=issues,
        )
        telemetry_report = self._stop_telemetry(timeout_seconds, issues)
        continuous_report = self._stop_continuous(
            drain,
            timeout_seconds,
            issues,
        )
        teardown = self._teardown(issues)
        final_state = (
            ExperimentRuntimeState.FAILED
            if issues
            else ExperimentRuntimeState.STOPPED
        )
        self._record_lifecycle(
            "run.failed" if issues else "run.stopped",
            {"state": final_state.value, "issues": len(issues)},
            issues=issues,
        )
        if issues:
            final_state = ExperimentRuntimeState.FAILED
        with self._lock:
            self._state = final_state
        report = ExperimentRuntimeReport(
            run=self.run,
            state=final_state,
            continuous=continuous_report,
            telemetry=telemetry_report,
            teardown=teardown,
            issues=tuple(issues),
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
    ) -> TelemetryPipelineReport:
        if self._telemetry is None:
            return TelemetryPipelineReport()
        try:
            return self._telemetry.stop(timeout_seconds=timeout_seconds)
        except Exception as error:
            issues.append(self._issue("telemetry", error))
            return self._telemetry.report()

    def _stop_continuous(
        self,
        drain: bool,
        timeout_seconds: float,
        issues: list[ExperimentRuntimeIssue],
    ) -> ContinuousRuntimeReport:
        if self._continuous is None:
            return ContinuousRuntimeReport()
        try:
            return self._continuous.stop(
                drain=drain,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:
            issues.append(self._issue("continuous", error))
            return self._continuous.report()

    def _teardown(
        self,
        issues: list[ExperimentRuntimeIssue],
    ) -> TeardownResult | None:
        if self._run is None:
            return None
        try:
            return self._substrate.teardown(self._run.id)
        except Exception as error:
            issues.append(self._issue("substrate", error))
            return None

    def _cleanup_start_failure(self) -> None:
        issues: list[ExperimentRuntimeIssue] = []
        if self._telemetry is not None:
            try:
                self._telemetry.stop()
            except Exception:
                pass
        if self._continuous is not None:
            try:
                self._continuous.stop(drain=False)
            except Exception:
                pass
        self._teardown(issues)

    @staticmethod
    def _issue(phase: str, error: Exception) -> ExperimentRuntimeIssue:
        code = getattr(error, "code", None)
        return ExperimentRuntimeIssue(
            phase=phase,
            code=(code if isinstance(code, str) and code else f"{phase}.failed"),
            message=str(error) or type(error).__name__,
        )
