"""Command-line interface for compiling and operating experiments."""

from __future__ import annotations

import json
import signal
import threading
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Self

import typer
from rich.console import Console
from rich.table import Table

from mininet_ai.compiler import DeploymentPlan, compile_experiment
from mininet_ai.errors import MininetAIError, RuntimeOperationError
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


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class SchemaName(StrEnum):
    EXPERIMENT = "experiment"
    AGENT_BLUEPRINT = "agent-blueprint"
    CAPABILITY = "capability"
    DEPLOYMENT_PLAN = "deployment-plan"


class _SignalLatch:
    """Translate foreground termination signals into orderly teardown."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._previous: dict[signal.Signals, signal.Handlers] = {}

    def __enter__(self) -> Self:
        for number in (signal.SIGINT, signal.SIGTERM):
            self._previous[number] = signal.getsignal(number)
            signal.signal(number, self._request_stop)
        return self

    def __exit__(self, *error: object) -> None:
        for number, handler in self._previous.items():
            signal.signal(number, handler)

    def _request_stop(self, number: int, frame: FrameType | None) -> None:
        del number, frame
        self._event.set()

    def wait(self) -> None:
        self._event.wait()


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
        code = f" ({error.code})" if isinstance(error, RuntimeOperationError) else ""
        error_console.print(f"[bold red]Error{code}:[/bold red] {error}")
        raise typer.Exit(code=1) from error


def _print_json(model) -> None:
    console.print_json(model.model_dump_json(by_alias=True, exclude_none=True))


@app.command()
def validate(
    experiment: Path = typer.Argument(
        ..., exists=False, dir_okay=False, readable=True, help="Experiment YAML file."
    ),
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
    experiment: Path = typer.Argument(
        ..., exists=False, dir_okay=False, readable=True, help="Experiment YAML file."
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help="Output format."
    ),
) -> None:
    """Compile an experiment into an inspectable deployment plan."""

    deployment_plan = _compile_or_exit(experiment)
    if output_format == OutputFormat.JSON:
        _print_json(deployment_plan)
    else:
        _print_text_plan(deployment_plan)


@app.command()
def run(
    experiment: Path = typer.Argument(
        ..., exists=False, dir_okay=False, readable=True, help="Experiment YAML file."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Compile and print the plan without creating network resources.",
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help="Output format for dry-run."
    ),
) -> None:
    """Deploy an experiment in the foreground until interrupted or stopped."""

    deployment_plan = _compile_or_exit(experiment)
    if dry_run:
        if output_format == OutputFormat.JSON:
            _print_json(deployment_plan)
        else:
            _print_text_plan(deployment_plan)
        return

    runtime = _runtime_or_exit(deployment_plan.substrate)
    with _SignalLatch() as stop_latch:
        run_info = _operation_or_exit(lambda: runtime.deploy(deployment_plan))
        console.print(
            f"[green]Running[/green] {run_info.id} on {run_info.substrate}. "
            "Press Ctrl+C to stop."
        )
        try:
            stop_latch.wait()
        finally:
            result = _operation_or_exit(lambda: runtime.teardown(run_info.id))
    console.print(
        f"[green]Stopped[/green] {result.run.id}; "
        f"released {len(result.released_resources)} resources"
    )


def _snapshot_or_exit(substrate: str, run_id: str) -> RuntimeSnapshot:
    runtime = _runtime_or_exit(substrate)
    return _operation_or_exit(lambda: runtime.inspect(run_id))


@app.command()
def status(
    run_id: str = typer.Argument(..., help="Substrate run identifier."),
    substrate: str = typer.Option(
        "mininet-ovs", "--substrate", help="Runtime adapter name."
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help="Output format."
    ),
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
    run_id: str = typer.Argument(..., help="Substrate run identifier."),
    substrate: str = typer.Option(
        "mininet-ovs", "--substrate", help="Runtime adapter name."
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TEXT, "--format", "-f", help="Output format."
    ),
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
    run_id: str = typer.Argument(..., help="Substrate run identifier."),
    substrate: str = typer.Option(
        "mininet-ovs", "--substrate", help="Runtime adapter name."
    ),
    timeout_seconds: float = typer.Option(
        30,
        "--timeout",
        min=0.001,
        help="Maximum seconds to wait for owner teardown.",
    ),
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


@app.command("schema")
def print_schema(
    name: SchemaName = typer.Argument(
        SchemaName.EXPERIMENT, help="Schema to print."
    ),
) -> None:
    """Print a JSON Schema for a public v1alpha1 document."""

    models = {
        SchemaName.EXPERIMENT: Experiment,
        SchemaName.AGENT_BLUEPRINT: AgentBlueprint,
        SchemaName.CAPABILITY: CapabilityDefinition,
        SchemaName.DEPLOYMENT_PLAN: DeploymentPlan,
    }
    console.print_json(json.dumps(models[name].model_json_schema(by_alias=True)))


if __name__ == "__main__":
    app()
