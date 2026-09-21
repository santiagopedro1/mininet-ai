"""Compile a validated experiment into a deterministic deployment plan."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from mininet_ai.compiler.models import (
    AgentInstance,
    Attachment,
    CoordinationEdge,
    CoordinationPlan,
    DeploymentPlan,
    PlannedResource,
)
from mininet_ai.errors import CompilationError
from mininet_ai.specification.loader import LoadedExperiment, load_experiment
from mininet_ai.specification.models import (
    AgentBlueprint,
    AgentDeployment,
    AttachmentLayer,
    CapabilityDefinition,
    Cardinality,
    CoordinationMode,
    Experiment,
    ResourceKind,
)
from mininet_ai.substrates.fake import FakeSubstrateDriver


def _unique_by_name(items: Iterable[object], category: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        name = getattr(getattr(item, "metadata", item), "name")
        if name in result:
            raise CompilationError(f"duplicate {category} name: {name!r}")
        result[name] = item
    return result


def _build_resources(loaded: LoadedExperiment) -> tuple[PlannedResource, ...]:
    resources: dict[str, PlannedResource] = {}
    for resource in loaded.topology.resources:
        if resource.name in resources:
            raise CompilationError(f"duplicate resource name: {resource.name!r}")
        resources[resource.name] = PlannedResource(**resource.model_dump())

    for resource in loaded.topology.resources:
        if resource.parent and resource.parent not in resources:
            raise CompilationError(
                f"resource {resource.name!r} has unknown parent {resource.parent!r}"
            )

    parents = {
        resource.name: resource.parent
        for resource in loaded.topology.resources
        if resource.parent
    }
    for resource_name in parents:
        chain: set[str] = set()
        current: str | None = resource_name
        while current in parents:
            if current in chain:
                raise CompilationError(
                    f"resource parent cycle detected at {current!r}"
                )
            chain.add(current)
            current = parents[current]

    for link in loaded.topology.links:
        if link.name in resources:
            raise CompilationError(f"duplicate resource name: {link.name!r}")
        for endpoint in link.endpoints:
            if endpoint not in resources:
                raise CompilationError(
                    f"link {link.name!r} has unknown endpoint {endpoint!r}"
                )
        resources[link.name] = PlannedResource(
            name=link.name,
            kind=ResourceKind.LINK,
            labels=link.labels,
            attributes=link.attributes,
            endpoints=link.endpoints,
        )
    return tuple(sorted(resources.values(), key=lambda item: (item.kind.value, item.name)))


def _select(deployment: AgentDeployment, resources: tuple[PlannedResource, ...]) -> list[PlannedResource]:
    selector = deployment.placement.targets
    selected = [resource for resource in resources if resource.kind == selector.kind]
    if selector.names:
        requested = set(selector.names)
        matching_names = {resource.name for resource in selected}
        invalid = requested - matching_names
        if invalid:
            raise CompilationError(
                f"agent {deployment.name!r} selects names that are missing or not "
                f"{selector.kind.value} resources: {', '.join(sorted(invalid))}"
            )
        selected = [resource for resource in selected if resource.name in requested]
    for key, value in selector.match_labels.items():
        selected = [resource for resource in selected if resource.labels.get(key) == value]
    if not selected:
        raise CompilationError(
            f"agent {deployment.name!r} selector matched no {selector.kind.value} resources"
        )
    return sorted(selected, key=lambda item: item.name)


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")


def _target_groups(
    deployment: AgentDeployment, selected: list[PlannedResource]
) -> list[tuple[str, tuple[PlannedResource, ...]]]:
    cardinality = deployment.placement.cardinality
    if cardinality == Cardinality.SINGLETON:
        if len(selected) != 1:
            raise CompilationError(
                f"singleton agent {deployment.name!r} must select exactly one resource; "
                f"matched {len(selected)}"
            )
        return [(deployment.name, (selected[0],))]
    if cardinality == Cardinality.PER_TARGET:
        return [
            (f"{deployment.name}@{resource.name}", (resource,))
            for resource in selected
        ]

    label = deployment.placement.group_by
    groups: dict[str, list[PlannedResource]] = defaultdict(list)
    for resource in selected:
        if label not in resource.labels:
            raise CompilationError(
                f"agent {deployment.name!r} groups by missing label {label!r} "
                f"on resource {resource.name!r}"
            )
        groups[resource.labels[label]].append(resource)
    return [
        (
            f"{deployment.name}@{label}-{_slug(value)}",
            tuple(sorted(group, key=lambda item: item.name)),
        )
        for value, group in sorted(groups.items())
    ]


def _compile_instances(
    loaded: LoadedExperiment,
    resources: tuple[PlannedResource, ...],
    blueprints: dict[str, AgentBlueprint],
    capabilities: dict[str, CapabilityDefinition],
    driver: FakeSubstrateDriver,
) -> tuple[AgentInstance, ...]:
    _unique_by_name(loaded.experiment.agents, "agent deployment")
    instances: list[AgentInstance] = []
    seen_ids: set[str] = set()

    for deployment in loaded.experiment.agents:
        if deployment.blueprint not in blueprints:
            raise CompilationError(
                f"agent {deployment.name!r} references unknown blueprint "
                f"{deployment.blueprint!r}"
            )
        missing = [name for name in deployment.capabilities if name not in capabilities]
        if missing:
            raise CompilationError(
                f"agent {deployment.name!r} references unknown capabilities: "
                + ", ".join(sorted(missing))
            )

        selected = _select(deployment, resources)
        if (
            deployment.placement.layer == AttachmentLayer.OBSERVER
            and deployment.capabilities
        ):
            raise CompilationError(
                f"observer agent {deployment.name!r} cannot have capabilities"
            )
        for instance_id, targets in _target_groups(deployment, selected):
            if instance_id in seen_ids:
                raise CompilationError(f"duplicate compiled agent id: {instance_id!r}")
            seen_ids.add(instance_id)
            layer = deployment.placement.layer
            layer_name = deployment.placement.custom_layer or layer.value
            target_kind = targets[0].kind

            error = driver.validate_attachment(
                layer=layer,
                custom_layer=deployment.placement.custom_layer,
                target_kind=target_kind,
                runtime=deployment.placement.runtime,
            )
            if error:
                raise CompilationError(f"agent {deployment.name!r}: {error}")

            for observation in deployment.observe:
                error = driver.validate_observation(layer=layer, name=observation)
                if error:
                    raise CompilationError(f"agent {deployment.name!r}: {error}")

            privileges: set[str] = set()
            for capability_name in deployment.capabilities:
                capability = capabilities[capability_name]
                if target_kind not in capability.targets:
                    raise CompilationError(
                        f"agent {deployment.name!r}: capability {capability_name!r} "
                        f"cannot target {target_kind.value!r}"
                    )
                if layer_name not in capability.layers:
                    raise CompilationError(
                        f"agent {deployment.name!r}: capability {capability_name!r} "
                        f"is not available at layer {layer_name!r}"
                    )
                privileges.update(capability.effects)

            instances.append(
                AgentInstance(
                    id=instance_id,
                    deployment=deployment.name,
                    blueprint=blueprints[deployment.blueprint].metadata.name,
                    attachment=Attachment(
                        layer=layer,
                        **{
                            "custom-layer": deployment.placement.custom_layer,
                            "targets": tuple(target.name for target in targets),
                            "target-kind": target_kind,
                            "runtime": deployment.placement.runtime,
                        },
                    ),
                    observes=tuple(sorted(set(deployment.observe))),
                    capabilities=tuple(sorted(set(deployment.capabilities))),
                    privileges=tuple(sorted(privileges)),
                    priority=deployment.priority,
                )
            )

    limit = loaded.experiment.resource_limits.max_instances
    if len(instances) > limit:
        raise CompilationError(
            f"compiled {len(instances)} agent instances, exceeding max-instances {limit}"
        )
    return tuple(sorted(instances, key=lambda item: item.id))


def _coordination(
    loaded: LoadedExperiment,
    resources: tuple[PlannedResource, ...],
    instances: tuple[AgentInstance, ...],
) -> CoordinationPlan:
    spec = loaded.experiment.coordination
    by_deployment: dict[str, list[AgentInstance]] = defaultdict(list)
    for instance in instances:
        by_deployment[instance.deployment].append(instance)
    known = set(by_deployment)
    edges: set[tuple[str, str, str]] = set()

    if spec.mode == CoordinationMode.CENTRALIZED:
        if spec.coordinator not in known:
            raise CompilationError(f"unknown coordinator deployment: {spec.coordinator!r}")
        coordinators = by_deployment[spec.coordinator]
        if len(coordinators) != 1:
            raise CompilationError("centralized coordinator must compile to one instance")
        coordinator = coordinators[0]
        for target in instances:
            if target.id != coordinator.id:
                edges.add((coordinator.id, target.id, "coordinates"))

    elif spec.mode == CoordinationMode.HIERARCHICAL:
        for relationship in spec.relationships:
            if relationship.source not in known:
                raise CompilationError(
                    f"unknown coordination source deployment: {relationship.source!r}"
                )
            for target_name in relationship.targets:
                if target_name not in known:
                    raise CompilationError(
                        f"unknown coordination target deployment: {target_name!r}"
                    )
                for source in by_deployment[relationship.source]:
                    for target in by_deployment[target_name]:
                        edges.add((source.id, target.id, "parent"))

    elif spec.mode == CoordinationMode.DISTRIBUTED:
        pairs: list[tuple[AgentInstance, AgentInstance]] = []
        if spec.peers == "all":
            for index, source in enumerate(instances):
                pairs.extend((source, target) for target in instances[index + 1 :])
        else:
            links = [resource for resource in resources if resource.endpoints]
            neighbors = {
                frozenset(resource.endpoints) for resource in links if resource.endpoints
            }
            for index, source in enumerate(instances):
                for target in instances[index + 1 :]:
                    if any(
                        frozenset((left, right)) in neighbors
                        for left in source.attachment.targets
                        for right in target.attachment.targets
                    ):
                        pairs.append((source, target))
        for source, target in pairs:
            edges.add((source.id, target.id, "peer"))
            edges.add((target.id, source.id, "peer"))

    return CoordinationPlan(
        mode=spec.mode,
        edges=tuple(
            CoordinationEdge(source=source, target=target, relationship=relationship)
            for source, target, relationship in sorted(edges)
        ),
    )


def _snapshot(loaded: LoadedExperiment) -> dict[str, object]:
    experiment = loaded.experiment.model_dump(by_alias=True, mode="json")
    experiment["substrate"]["topology"] = loaded.topology.model_dump(
        by_alias=True, mode="json"
    )
    experiment["blueprints"] = [
        blueprint.model_dump(by_alias=True, mode="json")
        for blueprint in sorted(loaded.blueprints, key=lambda item: item.metadata.name)
    ]
    experiment["capabilityDefinitions"] = [
        capability.model_dump(by_alias=True, mode="json")
        for capability in sorted(loaded.capabilities, key=lambda item: item.metadata.name)
    ]
    return experiment


def compile_experiment(
    source: str | Path | Experiment | LoadedExperiment,
) -> DeploymentPlan:
    """Compile an experiment without creating or changing a live network."""

    if isinstance(source, LoadedExperiment):
        loaded = source
    elif isinstance(source, Experiment):
        from mininet_ai.specification.loader import resolve_experiment

        loaded = resolve_experiment(source)
    else:
        loaded = load_experiment(source)
    if loaded.experiment.substrate.driver != "fake":
        raise CompilationError(
            f"unknown substrate driver {loaded.experiment.substrate.driver!r}; "
            "Phase 1 provides only 'fake'"
        )
    blueprints = _unique_by_name(loaded.blueprints, "blueprint")
    capabilities = _unique_by_name(loaded.capabilities, "capability")
    resources = _build_resources(loaded)
    driver = FakeSubstrateDriver(loaded.experiment.substrate.options)
    instances = _compile_instances(
        loaded, resources, blueprints, capabilities, driver
    )
    coordination = _coordination(loaded, resources, instances)
    snapshot = _snapshot(loaded)
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    digest = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
    return DeploymentPlan(
        apiVersion="mininet-ai/v1alpha1",
        metadata=loaded.experiment.metadata,
        source=str(loaded.source),
        digest=digest,
        substrate=driver.name,
        resources=resources,
        agents=instances,
        coordination=coordination,
        policies=loaded.experiment.policies,
        snapshot=snapshot,
    )
