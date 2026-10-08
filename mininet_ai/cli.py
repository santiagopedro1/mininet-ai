"""Command-line interface for compiling and operating experiments."""

from __future__ import annotations

import json
import logging
import os
import signal
import stat
import threading
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Annotated, Self, cast

import typer
from pydantic import JsonValue
from rich.console import Console
from rich.table import Table

from mininet_ai.agents import (
    AgnoAgentFactory,
    register_builtin_providers,
)
from mininet_ai.artifacts import default_artifact_root, reserve_artifacts
from mininet_ai.audit import (
    AuditEvent,
    AuditEventType,
    AuditRecorder,
)
from mininet_ai.compiler import DeploymentPlan, compile_experiment
from mininet_ai.coordination import CoordinationMessage, CoordinationOutcome
from mininet_ai.dependency_logging import RunDiagnostics
from mininet_ai.errors import MininetAIError
from mininet_ai.experiment import ExperimentRuntime, ExperimentRuntimeState
from mininet_ai.exports import export_managed, export_offline
from mininet_ai.log_output import RunLogFormatter, print_event
from mininet_ai.plugins import ProviderRegistries, discover_plugins
from mininet_ai.run_setup import reserve_run
from mininet_ai.runtime import (
    ContinuousInvocationRecord,
    LedgerAuditSink,
    PluginManifest,
    RuntimeEvent,
    SQLiteRunLedger,
    SQLiteSharedStateStore,
)
from mininet_ai.runtime.control import (
    DEFAULT_CONTROL_DIRECTORY,
    IntentClient,
    IntentServer,
)
from mininet_ai.saved_runs import RunEvidence
from mininet_ai.specification import (
    AgentBlueprint,
    CapabilityDefinition,
    Experiment,
)
from mininet_ai.substrates import (
    ExternallyStoppableRuntime,
    RuntimeSnapshot,
    SubstrateRuntime,
    create_substrate_runtime,
)

app = typer.Typer(
    name="mininet-ai",
    help="Compile and operate declarative Mininet-AI experiments.",
    no_args_is_help=True,
)
console = Console()
error_console = Console(stderr=True)
_FAILED_AUDIT_EVENTS = {
    AuditEventType.AGENT_FAILED,
    AuditEventType.MODEL_FAILED,
    AuditEventType.CAPABILITY_FAILED,
    AuditEventType.SHARED_STATE_FAILED,
}
_DEFAULT_INITIAL_INTENTS: list[str] = []


class _RunLogError(MininetAIError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "run.log.open-failed"


class _RunProgress:
    """Mirror run progress to an owner-only log and optionally stderr."""

    def __init__(
        self,
        path: Path,
        *,
        verbose: bool,
        run_id: str | None = None,
        substrate: str = "",
        network_capabilities: frozenset[str] = frozenset(),
    ) -> None:
        self.path = path
        self._verbose = verbose
        self._run_identity = json.dumps(run_id)
        self._substrate = substrate
        self._network_capabilities = network_capabilities
        self._lock = threading.RLock()
        self._capabilities: dict[tuple[str, str], str] = {}
        descriptor: int | None = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise _RunLogError(f"run log {path} must be an owner-only regular file")
            self._stream = os.fdopen(
                descriptor,
                "a",
                encoding="utf-8",
                buffering=1,
            )
            descriptor = None
        except _RunLogError:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise _RunLogError(f"could not open run log {path}: {error}") from error

        self._logger = logging.getLogger(f"mininet-ai.run.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = logging.StreamHandler(self._stream)
        handler.setFormatter(RunLogFormatter())
        self._logger.addHandler(handler)

    def emit(
        self,
        message: str,
        *,
        level: int = logging.INFO,
        source: str = "Run",
        visible: bool = False,
        summary: str | None = None,
    ) -> None:
        with self._lock:
            self._logger.log(
                level,
                message,
                extra={"run_identity": self._run_identity, "source": source},
            )
            if self._verbose or visible or level >= logging.WARNING:
                print_event(
                    error_console,
                    logging.getLevelName(level),
                    source,
                    message if self._verbose or summary is None else summary,
                )

    def info(self, message: str, *, visible: bool = False) -> None:
        self.emit(message, visible=visible)

    def error(self, message: str) -> None:
        self.emit(message, level=logging.ERROR)

    def audit(self, event: AuditEvent) -> None:
        with self._lock:
            self._audit(event)

    def _audit(self, event: AuditEvent) -> None:
        message = (
            f"{event.type.value}: run={event.run_id} agent={event.agent_id} "
            f"invocation={event.invocation_id}"
        )
        details: dict[str, JsonValue] = {}

        def include(value: JsonValue, *keys: str) -> None:
            if isinstance(value, dict):
                for key in keys:
                    if key in value and value[key] is not None:
                        details[key] = value[key]

        if event.type == AuditEventType.AGENT_STARTED:
            include(event.data.get("context"), "intent")
        elif event.type == AuditEventType.AGENT_COMPLETED:
            response = event.data.get("response")
            include(response, "message")
            if isinstance(response, dict):
                for key in ("proposals", "delegations", "sharedStateUpdates"):
                    values = response.get(key)
                    if isinstance(values, list):
                        details[key] = len(values)
            runtime = event.data.get("runtime")
            include(runtime, "model", "modelProvider", "agnoRunId", "sessionId")
            if isinstance(runtime, dict):
                include(
                    runtime.get("metrics"),
                    "durationSeconds",
                    "inputTokens",
                    "outputTokens",
                    "totalTokens",
                )
        elif event.type == AuditEventType.CAPABILITY_STARTED:
            include(event.data.get("proposal"), "id", "capability", "target", "reason")
        elif event.type == AuditEventType.CAPABILITY_COMPLETED:
            result = event.data.get("result")
            include(result, "request_id", "status", "changed", "effectLatencySeconds")
            if isinstance(result, dict):
                include(result.get("issue"), "code", "message")
        for key in ("code", "message", "errorType"):
            include(event.data, key)
        message += "".join(
            f" {key}={json.dumps(value, ensure_ascii=True, separators=(',', ':'))}"
            for key, value in details.items()
        )
        failed = event.type in _FAILED_AUDIT_EVENTS or (
            event.type == AuditEventType.CAPABILITY_COMPLETED
            and details.get("status") not in (None, "succeeded")
        )
        source = f"Agent:{event.agent_id}"
        visible = event.type == AuditEventType.AGENT_COMPLETED
        summary = str(
            details.get("message")
            or "Reasoning completed; see subsequent action results"
        )
        if event.type in {
            AuditEventType.CAPABILITY_STARTED,
            AuditEventType.CAPABILITY_COMPLETED,
            AuditEventType.CAPABILITY_FAILED,
        }:
            key = (event.agent_id, event.invocation_id)
            if event.type == AuditEventType.CAPABILITY_STARTED:
                self._capabilities[key] = str(details.get("capability", ""))
            capability = self._capabilities.get(key, "")
            network = capability in self._network_capabilities
            source = (
                "Mininet" if network and self._substrate == "mininet-ovs" else "Run"
            )
            if details.get("status") == "rejected" and str(
                details.get("code", "")
            ).startswith("capability."):
                source = "Run"
            if event.type != AuditEventType.CAPABILITY_STARTED:
                self._capabilities.pop(key, None)
            visible = (
                event.type == AuditEventType.CAPABILITY_COMPLETED
                and network
                and details.get("changed") is True
                and not failed
            )
            summary = f"{capability or 'Capability'}: {details.get('status', 'failed' if failed else 'started')} agent={event.agent_id}"
            if details.get("message"):
                summary += f" message={details['message']}"
        elif failed:
            summary = f"{event.type.value}: {details.get('message', 'failed')}"
        self.emit(
            message,
            level=logging.ERROR if failed else logging.INFO,
            source=source,
            visible=visible,
            summary=summary,
        )

    def close(self) -> None:
        for handler in tuple(self._logger.handlers):
            handler.flush()
            handler.close()
            self._logger.removeHandler(handler)
        self._stream.close()


class _ReportingAuditSink:
    """Persist audit events to the ledger and mirror concise progress."""

    def __init__(
        self,
        ledger: SQLiteRunLedger,
        progress: _RunProgress,
        *,
        on_failure: Callable[[], None],
    ) -> None:
        self._ledger = LedgerAuditSink(ledger)
        self._progress = progress
        self._on_failure = on_failure

    def write(self, event: AuditEvent) -> None:
        self._ledger.write(event)
        self._progress.audit(event)
        if event.type in _FAILED_AUDIT_EVENTS:
            self._progress.info("Runtime failure detected; requesting automatic stop")
            self._on_failure()


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class SchemaName(StrEnum):
    EXPERIMENT = "experiment"
    AGENT_BLUEPRINT = "agent-blueprint"
    CAPABILITY = "capability"
    DEPLOYMENT_PLAN = "deployment-plan"
    RUNTIME_EVENT = "runtime-event"
    COORDINATION_MESSAGE = "coordination-message"
    COORDINATION_OUTCOME = "coordination-outcome"


class _SignalLatch:
    """Translate foreground termination signals into orderly teardown."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._previous: dict[signal.Signals, signal.Handlers] = {}

    def __enter__(self) -> Self:
        for number in (signal.SIGINT, signal.SIGTERM):
            self._previous[number] = cast(signal.Handlers, signal.getsignal(number))
            signal.signal(number, self._request_stop)
        return self

    def __exit__(self, *error: object) -> None:
        for number, handler in self._previous.items():
            signal.signal(number, handler)

    def _request_stop(self, number: int, frame: FrameType | None) -> None:
        del number, frame
        self.request_stop()

    def request_stop(self) -> None:
        """Wake the foreground owner for signal- or runtime-requested stop."""

        self._event.set()

    def wait(self) -> None:
        self._event.wait()


class _InitialIntentTracker:
    """Request stop after every registered initial intent reaches a result."""

    def __init__(
        self,
        expected: int,
        progress: _RunProgress,
        request_stop: Callable[[], None],
    ) -> None:
        self._expected = expected
        self._progress = progress
        self._request_stop = request_stop
        self._lock = threading.Lock()
        self._tracked: set[str] = set()
        self._completed: set[str] = set()
        self._sealed = False
        self._stop_requested = False

    def track(self, event_id: str) -> None:
        with self._lock:
            self._tracked.add(event_id)
        self._stop_if_complete()

    def complete(self, record: ContinuousInvocationRecord) -> None:
        with self._lock:
            self._completed.add(record.event_id)
        self._stop_if_complete()

    def seal(self) -> None:
        with self._lock:
            self._sealed = True
        self._stop_if_complete()

    def _stop_if_complete(self) -> None:
        with self._lock:
            should_stop = (
                self._sealed
                and not self._stop_requested
                and len(self._tracked) == self._expected
                and self._tracked <= self._completed
            )
            if should_stop:
                self._stop_requested = True
        if should_stop:
            self._progress.info(
                f"All {self._expected} initial intents finished; "
                "requesting automatic stop"
            )
            self._request_stop()


def _compile_or_exit(path: Path) -> DeploymentPlan:
    try:
        return compile_experiment(path)
    except MininetAIError as error:
        error_console.print(f"[bold red]Error:[/bold red] {error}")
        raise typer.Exit(code=1) from error


def _runtime_or_exit(substrate: str) -> SubstrateRuntime:
    try:
        return create_substrate_runtime(substrate)
    except (LookupError, TypeError, ValueError) as error:
        error_console.print(f"[bold red]Error:[/bold red] {error}")
        raise typer.Exit(code=1) from error


def _operation_or_exit(operation):
    try:
        return operation()
    except MininetAIError as error:
        error_code = getattr(error, "code", None)
        code = f" ({error_code})" if error_code else ""
        error_console.print(f"[bold red]Error{code}:[/bold red] {error}")
        raise typer.Exit(code=1) from error


def _print_json(model) -> None:
    console.print_json(model.model_dump_json(by_alias=True, exclude_none=True))


@app.command()
def validate(
    experiment: Annotated[
        Path,
        typer.Argument(
            exists=False, dir_okay=False, readable=True, help="Experiment YAML file."
        ),
    ],
) -> None:
    """Validate schema, references, selectors, and substrate compatibility."""

    plan = _compile_or_exit(experiment)
    console.print(
        f"[green]Valid[/green] {plan.metadata.name}: "
        f"{len(plan.resources)} resources, {len(plan.agents)} agent instances"
    )


def _print_text_plan(plan: DeploymentPlan) -> None:
    console.print(f"[bold]{plan.metadata.name}[/bold]")
    console.print(f"Substrate: {plan.substrate}")
    console.print(f"Digest: {plan.digest}")
    console.print(f"Resources: {len(plan.resources)}")
    console.print(f"Agent instances: {len(plan.agents)}\n")

    table = Table("Instance", "Attachment", "Runtime", "Observes", "Capabilities")
    for instance in plan.agents:
        attachment = instance.attachment
        layer = attachment.custom_layer or attachment.layer.value
        target = ", ".join(attachment.targets)
        table.add_row(
            instance.id,
            f"{layer}/{attachment.target_kind.value}/{target}",
            attachment.runtime,
            "\n".join(instance.observes) or "—",
            "\n".join(instance.capabilities) or "—",
        )
    console.print(table)

    if plan.coordination.edges:
        console.print(f"\nCoordination: {plan.coordination.mode.value}")
        for edge in plan.coordination.edges:
            console.print(f"  {edge.source} --{edge.relationship}--> {edge.target}")


@app.command()
def plan(
    experiment: Annotated[
        Path,
        typer.Argument(
            exists=False, dir_okay=False, readable=True, help="Experiment YAML file."
        ),
    ],
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Output format.")
    ] = OutputFormat.TEXT,
) -> None:
    """Compile an experiment into an inspectable deployment plan."""

    deployment_plan = _compile_or_exit(experiment)
    if output_format == OutputFormat.JSON:
        _print_json(deployment_plan)
    else:
        _print_text_plan(deployment_plan)


@app.command()
def run(
    experiment: Annotated[
        Path,
        typer.Argument(
            exists=False, dir_okay=False, readable=True, help="Experiment YAML file."
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Compile and print the plan without creating network resources.",
        ),
    ] = False,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Output format.")
    ] = OutputFormat.TEXT,
    initial_intents: Annotated[
        list[str],
        typer.Option(
            "--intent",
            help="Initial manual intent as AGENT=TEXT; may be repeated.",
        ),
    ] = _DEFAULT_INITIAL_INTENTS,
    stop_after_intents: Annotated[
        bool,
        typer.Option(
            "--stop-after-intents",
            help="Stop after every initial --intent reaches a final result.",
        ),
    ] = False,
    ledger_db: Annotated[
        Path | None,
        typer.Option(
            "--ledger-db",
            help="Persistent experiment ledger database.",
        ),
    ] = None,
    agno_db: Annotated[
        Path | None,
        typer.Option(
            "--agno-db",
            help="Private SQLite database for Agno sessions and memory.",
        ),
    ] = None,
    shared_state_db: Annotated[
        Path | None,
        typer.Option(
            "--shared-state-db",
            help="Private SQLite database for shared operational state.",
        ),
    ] = None,
    discover: Annotated[
        bool,
        typer.Option(
            "--discover-plugins",
            help="Load installed capability plugins.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show live lifecycle, invocation, model, and capability progress.",
        ),
    ] = False,
    log_file: Annotated[
        Path | None,
        typer.Option(
            "--log-file",
            help="Append human-readable run progress to this file.",
        ),
    ] = None,
    artifact_root: Annotated[
        Path | None,
        typer.Option(
            "--artifact-root",
            help="Persistent live storage root (default: effective-user local state, not the project directory).",
        ),
    ] = None,
    control_dir: Annotated[
        Path | None,
        typer.Option(
            "--control-dir", help="Private directory for local intent submission."
        ),
    ] = DEFAULT_CONTROL_DIRECTORY,
) -> None:
    """Own a complete continuous experiment until interrupted or stopped."""

    deployment_plan = _compile_or_exit(experiment)
    if dry_run:
        if output_format == OutputFormat.JSON:
            _print_json(deployment_plan)
        else:
            _print_text_plan(deployment_plan)
        return

    root_selection = "--artifact-root" if artifact_root is not None else "default"
    artifact_root = (artifact_root or default_artifact_root()).absolute()
    print_event(
        error_console,
        "INFO",
        "Run",
        f"Storage root ({root_selection}): {artifact_root}",
    )

    try:
        reserved_id, substrate = reserve_run(deployment_plan.substrate)
    except (LookupError, TypeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    loaded = (
        _operation_or_exit(lambda: discover_plugins(registries)) if discover else ()
    )
    parsed_intents = []
    for declaration in initial_intents:
        agent_id, separator, intent = declaration.partition("=")
        if not separator or not agent_id or not intent:
            raise typer.BadParameter(
                "initial intents must use AGENT=TEXT",
                param_hint="--intent",
            )
        parsed_intents.append((agent_id, intent))
    if stop_after_intents and not parsed_intents:
        raise typer.BadParameter(
            "requires at least one --intent",
            param_hint="--stop-after-intents",
        )

    persistent_memory = any(
        agent.memory.learned is not None and agent.memory.learned.scope == "agent"
        for agent in deployment_plan.agents
    )
    legacy_memory = artifact_root.absolute() / "agno.sqlite3"
    if persistent_memory and agno_db is None and legacy_memory.exists():
        raise typer.BadParameter(
            f"Existing learned-memory store found at {legacy_memory}. "
            f"Use --agno-db {legacy_memory} to retain its agent memory, or explicitly "
            "select a new --agno-db to start fresh. No automatic migration is performed.",
            param_hint="--agno-db",
        )
    artifacts = None
    if reserved_id is None:
        if any(
            path is None for path in (log_file, ledger_db, shared_state_db, agno_db)
        ):
            raise typer.BadParameter(
                "This registered adapter does not support built-in identity reservation. "
                "Provide --log-file, --ledger-db, --shared-state-db, and --agno-db explicitly; "
                "its zero-argument factory and runtime protocol remain unchanged."
            )
        artifact_description = (
            "explicit file paths (adapter assigns identity during deployment)"
        )
    else:
        artifacts = _operation_or_exit(
            lambda: reserve_artifacts(
                artifact_root,
                reserved_id,
                persistent_memory=persistent_memory and agno_db is None,
            )
        )
        artifact_description = str(artifacts.directory)
        log_file = log_file or artifacts.log
        ledger_db = ledger_db or artifacts.ledger
        shared_state_db = shared_state_db or artifacts.shared_state
        agno_db = agno_db or artifacts.agno
    assert (
        log_file is not None
        and ledger_db is not None
        and shared_state_db is not None
        and agno_db is not None
    )
    log_file = log_file.absolute()
    ledger_db = ledger_db.absolute()
    shared_state_db = shared_state_db.absolute()
    agno_db = agno_db.absolute()
    print_event(
        error_console,
        "INFO",
        "Run",
        f"Artifacts: {artifact_description}\nLog: {log_file}\nLedger: {ledger_db}\n"
        f"Shared state: {shared_state_db}\nAgno sessions/memory: {agno_db}",
    )
    if persistent_memory:
        print_event(
            error_console,
            "INFO",
            "Run",
            "Learned memory uses the selected Agno path; stores in other roots are not automatically reused or imported.",
        )
    evidence = None
    if artifacts is not None and reserved_id is not None:
        verified_writers = (
            not loaded
            and all(
                item["implementation"]["type"] == "declarative"
                for item in deployment_plan.snapshot.get("blueprints", [])
            )
            and all(
                item.get("provider")
                in {None, "substrate.action", "substrate.observation", "fake.openflow"}
                and item["metadata"]["name"] != "host.process.start"
                for item in deployment_plan.snapshot.get("capabilityDefinitions", [])
            )
        )
        evidence = _operation_or_exit(
            lambda: RunEvidence(
                artifacts.directory,
                reserved_id,
                {
                    "log": log_file,
                    "ledger": ledger_db,
                    "shared_state": shared_state_db,
                    "agno": agno_db,
                    "artifacts": artifacts.directory / "artifacts",
                },
                verified_writers=verified_writers,
            )
        )
    try:
        progress = _operation_or_exit(
            lambda: _RunProgress(
                log_file,
                verbose=verbose,
                run_id=reserved_id,
                substrate=deployment_plan.substrate,
                network_capabilities=frozenset(
                    item["metadata"]["name"]
                    for item in deployment_plan.snapshot.get(
                        "capabilityDefinitions", []
                    )
                    if item.get("provider") in {"substrate.action", "fake.openflow"}
                    and not item["metadata"]["name"].startswith("host.process.")
                ),
            )
        )
    except BaseException:
        if evidence is not None:
            evidence.close()
        raise
    progress.info(
        f"Artifacts: {artifact_description}; log={log_file}; ledger={ledger_db}; shared-state={shared_state_db}; Agno={agno_db}"
    )
    progress.info(
        f"Reserved run {reserved_id}; Prepared experiment {deployment_plan.metadata.name} "
        f"({len(deployment_plan.resources)} resources, "
        f"{len(deployment_plan.agents)} agents)"
    )
    ledger = None
    state_store = None
    owner = None
    intent_server = None
    report = None
    operation_failed = False
    diagnostics = None
    try:
        if reserved_id is not None:
            diagnostics = RunDiagnostics(
                reserved_id,
                progress.emit,
                mininet=deployment_plan.substrate == "mininet-ovs",
            ).__enter__()
        progress.info(f"Opening run ledger {ledger_db}")
        ledger = _operation_or_exit(lambda: SQLiteRunLedger(ledger_db))
        progress.info(f"Opening shared state database {shared_state_db}")
        state_store = _operation_or_exit(
            lambda: SQLiteSharedStateStore(shared_state_db)
        )
        progress.info(
            f"Starting {deployment_plan.substrate} substrate and runtime services",
            visible=True,
        )
        stop_latch = _SignalLatch()
        intent_tracker = (
            _InitialIntentTracker(
                len(parsed_intents),
                progress,
                stop_latch.request_stop,
            )
            if stop_after_intents
            else None
        )
        owner = ExperimentRuntime(
            deployment_plan,
            substrate,
            registries,
            audit=AuditRecorder(
                _ReportingAuditSink(
                    ledger,
                    progress,
                    on_failure=stop_latch.request_stop,
                )
            ),
            agent_factory=_operation_or_exit(
                lambda: AgnoAgentFactory(database_path=agno_db)
            ),
            shared_state=state_store,
            ledger=ledger,
            plugins=tuple(
                PluginManifest(group=plugin.group, name=plugin.name)
                for plugin in loaded
            ),
            invocation_listener=(
                intent_tracker.complete if intent_tracker is not None else None
            ),
        )
        with stop_latch:
            run_info = _operation_or_exit(owner.start)
            if evidence is not None:
                evidence.active()
            if reserved_id is not None and run_info.id != reserved_id:
                raise MininetAIError("deployed run ID differs from reserved identity")
            intent_server = IntentServer(
                owner,
                control_dir,
                on_submit=lambda event: progress.info(
                    f"Queued terminal intent {event.event_id} for agent {event.subject}"
                ),
            )
            _operation_or_exit(intent_server.__enter__)
            progress.info(f"Control endpoint: {intent_server.path}", visible=True)
            progress.info(
                f"Run {run_info.id} is active on {run_info.substrate}", visible=True
            )
            for agent_id, intent in parsed_intents:
                event = _operation_or_exit(
                    lambda agent_id=agent_id, intent=intent: owner.submit_intent(
                        agent_id,
                        intent,
                    )
                )
                progress.info(f"Queued intent {event.event_id} for agent {agent_id}")
                if intent_tracker is not None:
                    intent_tracker.track(event.event_id)
            if intent_tracker is not None:
                intent_tracker.seal()
            if output_format == OutputFormat.TEXT:
                wait_message = (
                    "Stopping after initial intents finish; press Ctrl+C "
                    "to stop sooner."
                    if stop_after_intents
                    else "Press Ctrl+C to stop."
                )
                console.print(
                    f"[green]Running[/green] {run_info.id} on "
                    f"{run_info.substrate}. {wait_message}\n"
                    f"Log: {log_file}"
                )
            stop_latch.wait()
    except BaseException as error:
        operation_failed = True
        progress.error(f"Run {reserved_id} startup/operation failed: {error}")
        error_console.print(
            "Run failed; diagnostic output retained. For storage errors use --artifact-root on a private local filesystem supporting SQLite locking."
        )
        raise
    finally:
        try:
            if intent_server is not None:
                intent_server.close()
        finally:
            try:
                if owner is not None and owner.state == ExperimentRuntimeState.RUNNING:
                    progress.info(
                        "Stop requested; draining work and tearing down", visible=True
                    )
                    try:
                        report = _operation_or_exit(owner.stop)
                    except BaseException as error:
                        progress.error(f"Cleanup failed for run {reserved_id}: {error}")
                        raise
                    if report.issues:
                        for issue in report.issues:
                            progress.error(
                                f"Cleanup failed for run {report.run.id}: {issue.code}: {issue.message}"
                            )
                    else:
                        progress.info(
                            f"Run {report.run.id} stopped with "
                            f"{report.continuous.completed} completed and "
                            f"{report.continuous.failed} failed invocations",
                            visible=True,
                        )
            finally:
                try:
                    try:
                        try:
                            if state_store is not None:
                                state_store.close()
                        finally:
                            if ledger is not None:
                                ledger.close()
                    finally:
                        try:
                            if diagnostics is not None:
                                diagnostics.__exit__()
                        finally:
                            progress.close()
                    if evidence is not None:
                        evidence.finish(
                            finalized=owner is not None and owner.writers_stopped,
                            outcome=(
                                "failed"
                                if operation_failed
                                or report is None
                                or report.state == ExperimentRuntimeState.FAILED
                                or report.continuous.failed
                                else "succeeded"
                            ),
                        )
                finally:
                    if evidence is not None:
                        evidence.close()
    if report is None:
        return
    if output_format == OutputFormat.JSON:
        _print_json(report)
    else:
        released = (
            len(report.teardown.released_resources)
            if report.teardown is not None
            else 0
        )
        console.print(
            f"[green]Stopped[/green] {report.run.id}; "
            f"{report.continuous.completed} invocations, "
            f"released {released} resources"
        )
    if report.state == ExperimentRuntimeState.FAILED or report.continuous.failed > 0:
        raise typer.Exit(code=1)


@app.command("export")
def export_results(
    run_id: Annotated[str, typer.Argument(help="Finalized run identity.")],
    destination: Annotated[
        Path, typer.Option(help="Exact new bundle directory; parent must exist.")
    ],
    artifact_root: Annotated[
        Path | None,
        typer.Option(
            help="Source storage root; defaults to effective-user local state."
        ),
    ] = None,
    acknowledge_sensitive_data: Annotated[
        bool,
        typer.Option(
            help="Acknowledge that shared results are not sanitized and may expose sensitive data."
        ),
    ] = False,
) -> None:
    """Export finalized managed evidence as host-readable JSON, logs and artifacts."""
    if not acknowledge_sensitive_data:
        raise typer.BadParameter(
            "export requires --acknowledge-sensitive-data; evidence is not sanitized"
        )
    complete = _operation_or_exit(
        lambda: export_managed(
            artifact_root or default_artifact_root(), run_id, destination
        )
    )
    console.print(f"Exported: {destination.absolute()}")
    if not complete:
        error_console.print(
            "Warning: export complete, but experiment evidence is incomplete; see manifest.json"
        )


def _snapshot_or_exit(substrate: str, run_id: str) -> RuntimeSnapshot:
    runtime = _runtime_or_exit(substrate)
    return _operation_or_exit(lambda: runtime.inspect(run_id))


@app.command("export-offline")
def export_offline_results(
    run_id: Annotated[
        str, typer.Argument(help="Run identity to filter from prepared snapshots.")
    ],
    destination: Annotated[
        Path, typer.Option(help="Exact new bundle directory; parent must exist.")
    ],
    ledger_db: Annotated[
        Path | None, typer.Option(help="Private, consistent offline ledger snapshot.")
    ] = None,
    shared_state_db: Annotated[
        Path | None,
        typer.Option(help="Private, consistent offline shared-state snapshot."),
    ] = None,
    log_file: Annotated[
        Path | None, typer.Option(help="Private offline run log.")
    ] = None,
    artifacts_dir: Annotated[
        Path | None, typer.Option(help="Private offline artifact directory.")
    ] = None,
    acknowledge_sensitive_data: Annotated[
        bool,
        typer.Option(
            help="Acknowledge that exported evidence is sensitive and not sanitized."
        ),
    ] = False,
    acknowledge_offline_consistency: Annotated[
        bool,
        typer.Option(
            help="Take responsibility for stopping writers and preparing consistent private snapshots."
        ),
    ] = False,
) -> None:
    """Export operator-prepared offline snapshots, not a force-live export."""
    if not acknowledge_sensitive_data or not acknowledge_offline_consistency:
        raise typer.BadParameter(
            "offline export requires --acknowledge-sensitive-data and --acknowledge-offline-consistency"
        )
    sources = {
        role: path
        for role, path in {
            "ledger": ledger_db,
            "shared_state": shared_state_db,
            "log": log_file,
            "artifacts": artifacts_dir,
        }.items()
        if path is not None
    }
    complete = _operation_or_exit(lambda: export_offline(run_id, sources, destination))
    console.print(f"Exported offline snapshots: {destination.absolute()}")
    if not complete:
        error_console.print(
            "Warning: export complete, but experiment evidence is incomplete; see manifest.json"
        )


@app.command()
def status(
    run_id: Annotated[str, typer.Argument(help="Substrate run identifier.")],
    substrate: Annotated[
        str, typer.Option("--substrate", help="Runtime adapter name.")
    ] = "mininet-ovs",
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Output format.")
    ] = OutputFormat.TEXT,
) -> None:
    """Show lifecycle state for a substrate run."""

    snapshot = _snapshot_or_exit(substrate, run_id)
    if output_format == OutputFormat.JSON:
        _print_json(snapshot)
        return
    info = snapshot.run
    console.print(f"[bold]{info.id}[/bold]")
    console.print(f"Substrate: {info.substrate}")
    console.print(f"State: {info.state.value}")
    console.print(f"Plan digest: {info.plan_digest}")
    console.print(f"Started: {info.started_at.isoformat()}")
    if info.stopped_at is not None:
        console.print(f"Stopped: {info.stopped_at.isoformat()}")
    if info.issue is not None:
        console.print(f"Issue: {info.issue.code}: {info.issue.message}")


@app.command()
def topology(
    run_id: Annotated[str, typer.Argument(help="Substrate run identifier.")],
    substrate: Annotated[
        str, typer.Option("--substrate", help="Runtime adapter name.")
    ] = "mininet-ovs",
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Output format.")
    ] = OutputFormat.TEXT,
) -> None:
    """Show the normalized resource graph for a substrate run."""

    snapshot = _snapshot_or_exit(substrate, run_id)
    if output_format == OutputFormat.JSON:
        _print_json(snapshot)
        return
    table = Table("Resource", "Kind", "State", "Parent")
    for resource in snapshot.resources:
        table.add_row(
            resource.name,
            resource.kind.value,
            resource.state.value,
            str(resource.attributes.get("parent") or "—"),
        )
    console.print(table)


@app.command()
def stop(
    run_id: Annotated[str, typer.Argument(help="Substrate run identifier.")],
    substrate: Annotated[
        str, typer.Option("--substrate", help="Runtime adapter name.")
    ] = "mininet-ovs",
    timeout_seconds: Annotated[
        float,
        typer.Option(
            "--timeout",
            min=0.001,
            help="Maximum seconds to wait for owner teardown.",
        ),
    ] = 30,
) -> None:
    """Stop an owned run, recovering it first if the owner has exited."""

    runtime = _runtime_or_exit(substrate)
    if isinstance(runtime, ExternallyStoppableRuntime):
        result = _operation_or_exit(
            lambda: runtime.request_stop(
                run_id,
                timeout_seconds=timeout_seconds,
            )
        )
    else:
        result = _operation_or_exit(lambda: runtime.teardown(run_id))
    console.print(
        f"[green]Stopped[/green] {result.run.id}; "
        f"released {len(result.released_resources)} resources"
    )


@app.command()
def invoke(
    run_id: Annotated[str, typer.Argument(help="Running substrate identifier.")],
    agent_id: Annotated[
        str, typer.Argument(help="Compiled agent instance identifier.")
    ],
    intent: Annotated[
        str,
        typer.Option(
            "--intent", "-i", help="Manual intent to queue in the foreground owner."
        ),
    ],
    control_dir: Annotated[
        Path | None,
        typer.Option(
            "--control-dir", help="The foreground owner's private control directory."
        ),
    ] = DEFAULT_CONTROL_DIRECTORY,
    timeout_seconds: Annotated[
        float,
        typer.Option(
            "--timeout",
            min=0.001,
            help="Seconds to wait for intent acceptance, not completion.",
        ),
    ] = 5,
    audit_log: Annotated[
        Path | None,
        typer.Option("--audit-log", help="Deprecated: audit is recorded by the owner."),
    ] = None,
    agno_db: Annotated[
        Path | None,
        typer.Option("--agno-db", help="Deprecated: configure sessions on run."),
    ] = None,
    shared_state_db: Annotated[
        Path | None,
        typer.Option(
            "--shared-state-db", help="Deprecated: configure shared state on run."
        ),
    ] = None,
    discover: Annotated[
        bool,
        typer.Option("--discover-plugins", help="Deprecated: discover plugins on run."),
    ] = False,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Accepted event format.")
    ] = OutputFormat.TEXT,
) -> None:
    """Queue an intent in an already-running owner, including from another terminal."""

    if (
        any(value is not None for value in (audit_log, agno_db, shared_state_db))
        or discover
    ):
        error_console.print(
            "[yellow]Deprecated invoke configuration options are ignored; "
            "the foreground owner uses the databases, audit, and plugins configured on run.[/yellow]"
        )
    event = _operation_or_exit(
        lambda: IntentClient(control_dir).submit(
            run_id,
            agent_id,
            intent,
            timeout_seconds=timeout_seconds,
        )
    )
    if output_format == OutputFormat.JSON:
        _print_json(event)
    else:
        console.print(
            f"[green]Queued[/green] {event.event_id} for {agent_id}; "
            "follow the owner's run log for results"
        )


@app.command()
def agents(
    run_id: Annotated[str, typer.Argument(help="Running substrate identifier.")],
    control_dir: Annotated[
        Path | None,
        typer.Option(
            "--control-dir", help="The foreground owner's private control directory."
        ),
    ] = DEFAULT_CONTROL_DIRECTORY,
    timeout_seconds: Annotated[
        float,
        typer.Option(
            "--timeout", min=0.001, help="Seconds to wait for agent discovery."
        ),
    ] = 5,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Agent discovery format.")
    ] = OutputFormat.TEXT,
) -> None:
    """List live owner agent IDs and whether they accept manual intents."""
    description = _operation_or_exit(
        lambda: IntentClient(control_dir).describe(
            run_id, timeout_seconds=timeout_seconds
        )
    )
    if output_format == OutputFormat.JSON:
        _print_json(description)
        return
    console.print(f"[bold]{description.run_id}[/bold]")
    console.print(f"Plan digest: {description.plan_digest}")
    table = Table("Agent", "Manual intents")
    for agent_id in description.agents:
        table.add_row(
            agent_id, "yes" if agent_id in description.manual_agents else "no"
        )
    console.print(table)


@app.command("schema")
def print_schema(
    name: Annotated[
        SchemaName, typer.Argument(help="Schema to print.")
    ] = SchemaName.EXPERIMENT,
) -> None:
    """Print a JSON Schema for a current public document."""

    models = {
        SchemaName.EXPERIMENT: Experiment,
        SchemaName.AGENT_BLUEPRINT: AgentBlueprint,
        SchemaName.CAPABILITY: CapabilityDefinition,
        SchemaName.DEPLOYMENT_PLAN: DeploymentPlan,
        SchemaName.RUNTIME_EVENT: RuntimeEvent,
        SchemaName.COORDINATION_MESSAGE: CoordinationMessage,
        SchemaName.COORDINATION_OUTCOME: CoordinationOutcome,
    }
    console.print_json(json.dumps(models[name].model_json_schema(by_alias=True)))


if __name__ == "__main__":
    app()
