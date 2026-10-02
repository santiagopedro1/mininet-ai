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
from rich.console import Console
from rich.table import Table

from mininet_ai.agents import (
    AgnoAgentFactory,
    register_builtin_providers,
)
from mininet_ai.audit import (
    AuditEvent,
    AuditEventType,
    AuditRecorder,
)
from mininet_ai.compiler import DeploymentPlan, compile_experiment
from mininet_ai.coordination import CoordinationMessage, CoordinationOutcome
from mininet_ai.errors import MininetAIError
from mininet_ai.experiment import ExperimentRuntime, ExperimentRuntimeState
from mininet_ai.plugins import ProviderRegistries, discover_plugins
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

    def __init__(self, path: Path, *, verbose: bool) -> None:
        self.path = path
        self._verbose = verbose
        descriptor: int | None = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
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
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self._logger.addHandler(handler)

    def info(self, message: str) -> None:
        self._logger.info(message)
        if self._verbose:
            error_console.print(f"[dim]•[/dim] {message}")

    def error(self, message: str) -> None:
        self._logger.error(message)
        if self._verbose:
            error_console.print(f"[bold red]×[/bold red] {message}")

    def audit(self, event: AuditEvent) -> None:
        message = (
            f"{event.type.value}: agent={event.agent_id} "
            f"invocation={event.invocation_id}"
        )
        if event.type in _FAILED_AUDIT_EVENTS:
            code = event.data.get("code")
            detail = event.data.get("message")
            if isinstance(code, str) and code:
                message += f" code={code}"
            if isinstance(detail, str) and detail:
                message += f" message={detail}"
        if event.type in _FAILED_AUDIT_EVENTS:
            self.error(message)
        else:
            self.info(message)

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
        Path,
        typer.Option(
            "--ledger-db",
            help="Persistent experiment ledger database.",
        ),
    ] = Path(".mininet-ai/runs.sqlite3"),
    agno_db: Annotated[
        Path,
        typer.Option(
            "--agno-db",
            help="Private SQLite database for Agno sessions and memory.",
        ),
    ] = Path(".mininet-ai/agno.sqlite3"),
    shared_state_db: Annotated[
        Path,
        typer.Option(
            "--shared-state-db",
            help="Private SQLite database for shared operational state.",
        ),
    ] = Path(".mininet-ai/shared-state.sqlite3"),
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
        Path,
        typer.Option(
            "--log-file",
            help="Append human-readable run progress to this file.",
        ),
    ] = Path(".mininet-ai/run.log"),
    control_dir: Annotated[
        Path,
        typer.Option("--control-dir", help="Private directory for local intent submission."),
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

    substrate = _runtime_or_exit(deployment_plan.substrate)
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

    progress = _operation_or_exit(lambda: _RunProgress(log_file, verbose=verbose))
    progress.info(
        f"Prepared experiment {deployment_plan.metadata.name} "
        f"({len(deployment_plan.resources)} resources, "
        f"{len(deployment_plan.agents)} agents)"
    )
    ledger = None
    state_store = None
    owner = None
    intent_server = None
    report = None
    try:
        progress.info(f"Opening run ledger {ledger_db}")
        ledger = _operation_or_exit(lambda: SQLiteRunLedger(ledger_db))
        progress.info(f"Opening shared state database {shared_state_db}")
        state_store = _operation_or_exit(
            lambda: SQLiteSharedStateStore(shared_state_db)
        )
        progress.info(
            f"Starting {deployment_plan.substrate} substrate and runtime services"
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
            intent_server = IntentServer(
                owner,
                control_dir,
                on_submit=lambda event: progress.info(
                    f"Queued terminal intent {event.event_id} for agent {event.subject}"
                ),
            )
            _operation_or_exit(intent_server.__enter__)
            progress.info(f"Run {run_info.id} is active on {run_info.substrate}")
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
    finally:
        try:
            if intent_server is not None:
                intent_server.close()
        finally:
            try:
                if owner is not None and owner.state == ExperimentRuntimeState.RUNNING:
                    progress.info("Stop requested; draining work and tearing down")
                    report = _operation_or_exit(owner.stop)
                    progress.info(
                        f"Run {report.run.id} stopped with "
                        f"{report.continuous.completed} completed and "
                        f"{report.continuous.failed} failed invocations"
                    )
            finally:
                if state_store is not None:
                    state_store.close()
                if ledger is not None:
                    ledger.close()
                progress.close()
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


def _snapshot_or_exit(substrate: str, run_id: str) -> RuntimeSnapshot:
    runtime = _runtime_or_exit(substrate)
    return _operation_or_exit(lambda: runtime.inspect(run_id))


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
    experiment: Annotated[
        Path,
        typer.Argument(
            exists=False, dir_okay=False, readable=True, help="Experiment YAML file."
        ),
    ],
    run_id: Annotated[str, typer.Argument(help="Running substrate identifier.")],
    agent_id: Annotated[
        str, typer.Argument(help="Compiled agent instance identifier.")
    ],
    intent: Annotated[
        str,
        typer.Option("--intent", "-i", help="Manual intent to queue in the foreground owner."),
    ],
    control_dir: Annotated[
        Path,
        typer.Option("--control-dir", help="The foreground owner's private control directory."),
    ] = DEFAULT_CONTROL_DIRECTORY,
    timeout_seconds: Annotated[
        float,
        typer.Option(
            "--timeout", min=0.001,
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
        typer.Option("--shared-state-db", help="Deprecated: configure shared state on run."),
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

    if any(value is not None for value in (audit_log, agno_db, shared_state_db)) or discover:
        error_console.print(
            "[yellow]Deprecated invoke configuration options are ignored; "
            "the foreground owner uses the databases, audit, and plugins configured on run.[/yellow]"
        )
    deployment_plan = _compile_or_exit(experiment)
    event = _operation_or_exit(
        lambda: IntentClient(control_dir).submit(
            run_id, agent_id, intent, plan_digest=deployment_plan.digest,
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
