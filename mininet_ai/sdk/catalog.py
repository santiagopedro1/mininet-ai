"""Resolve execution-ready agent definitions from a deployment plan."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import ValidationError

from mininet_ai.compiler.models import AgentInstance, DeploymentPlan
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.specification.models import (
    AgentBlueprint,
    CapabilityDefinition,
    Experiment,
    Policy,
)


T = TypeVar("T")


@dataclass(frozen=True)
class AgentExecutionDefinition:
    """Everything required to construct one compiled agent instance."""

    instance: AgentInstance
    blueprint: AgentBlueprint
    capabilities: tuple[CapabilityDefinition, ...]
    policy: Policy


class ExecutionCatalog:
    """Validated lookup over the executable definitions embedded in a plan."""

    def __init__(self, plan: DeploymentPlan) -> None:
        try:
            experiment = Experiment.model_validate(plan.snapshot)
        except ValidationError as error:
            raise AgentRuntimeError(
                f"deployment plan snapshot is not executable: {error}",
                code="agent.catalog.invalid-snapshot",
            ) from error

        blueprint_definitions = self._resolved_definitions(
            experiment.blueprints,
            AgentBlueprint,
            kind="blueprint",
        )
        capability_definitions = self._resolved_definitions(
            experiment.capability_definitions,
            CapabilityDefinition,
            kind="capability",
        )
        blueprints = self._unique(
            blueprint_definitions,
            kind="blueprint",
            name=lambda item: item.metadata.name,
        )
        capabilities = self._unique(
            capability_definitions,
            kind="capability",
            name=lambda item: item.metadata.name,
        )
        instances = self._unique(
            plan.agents,
            kind="agent instance",
            name=lambda item: item.id,
        )

        resolved: dict[str, AgentExecutionDefinition] = {}
        for instance_id, instance in instances.items():
            try:
                blueprint = blueprints[instance.blueprint]
            except KeyError as error:
                raise AgentRuntimeError(
                    f"agent {instance_id!r} references missing blueprint "
                    f"{instance.blueprint!r}",
                    code="agent.catalog.blueprint-missing",
                    agent_id=instance_id,
                ) from error

            missing = [
                name for name in instance.capabilities if name not in capabilities
            ]
            if missing:
                raise AgentRuntimeError(
                    f"agent {instance_id!r} references missing capabilities: "
                    + ", ".join(sorted(missing)),
                    code="agent.catalog.capability-missing",
                    agent_id=instance_id,
                )
            resolved[instance_id] = AgentExecutionDefinition(
                instance=instance,
                blueprint=blueprint,
                capabilities=tuple(
                    capabilities[name] for name in instance.capabilities
                ),
                policy=plan.policies,
            )

        self._plan_digest = plan.digest
        self._agents = resolved

    @property
    def plan_digest(self) -> str:
        return self._plan_digest

    @property
    def agent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._agents))

    def resolve(self, agent_id: str) -> AgentExecutionDefinition:
        """Return one execution definition or raise a typed lookup error."""

        try:
            return self._agents[agent_id]
        except KeyError as error:
            raise AgentRuntimeError(
                f"unknown compiled agent instance {agent_id!r}",
                code="agent.instance.unknown",
                agent_id=agent_id,
            ) from error

    @staticmethod
    def _resolved_definitions(
        items: list[T | str], expected: type[T], *, kind: str
    ) -> list[T]:
        resolved = []
        for item in items:
            if not isinstance(item, expected):
                raise AgentRuntimeError(
                    f"deployment plan snapshot contains an unresolved {kind} "
                    f"reference {item!r}",
                    code="agent.catalog.unresolved-reference",
                )
            resolved.append(item)
        return resolved

    @staticmethod
    def _unique(
        items: Iterable[T],
        *,
        kind: str,
        name: Callable[[T], str],
    ) -> dict[str, T]:
        result: dict[str, T] = {}
        for item in items:
            item_name = name(item)
            if item_name in result:
                raise AgentRuntimeError(
                    f"duplicate {kind} name {item_name!r} in deployment plan",
                    code="agent.catalog.duplicate",
                )
            result[item_name] = item
        return result
