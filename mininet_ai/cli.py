"""Command-line interface for validating and planning experiments."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mininet_ai.compiler import DeploymentPlan, compile_experiment
from mininet_ai.errors import MininetAIError
from mininet_ai.specification import (
    AgentBlueprint,
    CapabilityDefinition,
    Experiment,
)


app = typer.Typer(
    name="mininet-ai",
    help="Validate and compile declarative Mininet-AI experiments.",
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


def _compile_or_exit(path: Path) -> DeploymentPlan:
    try:
        return compile_experiment(path)
    except MininetAIError as error:
        error_console.print(f"[bold red]Error:[/bold red] {error}")
        raise typer.Exit(code=1) from error


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
        console.print_json(
            deployment_plan.model_dump_json(by_alias=True, exclude_none=True)
        )
    else:
        _print_text_plan(deployment_plan)


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
    }
    console.print_json(json.dumps(models[name].model_json_schema(by_alias=True)))


if __name__ == "__main__":
    app()
