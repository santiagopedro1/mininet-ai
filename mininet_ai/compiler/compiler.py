"""Compile a validated experiment into a deterministic deployment plan."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface
from pathlib import Path
from typing import Literal

from mininet_ai.compiler.models import (
    AgentInstance,
    Attachment,
    CoordinationEdge,
    CoordinationPlan,
    DeploymentPlan,
    PlannedController,
    PlannedControllerDomain,
    PlannedFlow,
    PlannedHost,
    PlannedLink,
    PlannedNetwork,
    PlannedPort,
    PlannedRegion,
    PlannedResource,
    PlannedSwitch,
)
from mininet_ai.errors import CompilationError
from mininet_ai.specification.loader import LoadedExperiment, load_experiment
from mininet_ai.specification.models import (
    AgentBlueprint,
    AgentDeployment,
    AttachmentLayer,
    CapabilityDefinition,
    Cardinality,
    ControllerDomainResource,
    ControllerResource,
    CoordinationMode,
    Experiment,
    FlowResource,
    HostResource,
    NetworkResource,
    RegionResource,
    ResourceKind,
    SwitchResource,
    TopologyResource,
)
from mininet_ai.substrates import create_substrate_driver
from mininet_ai.substrates.protocol import SubstrateDriver, SubstrateIssue


def _format_substrate_issues(issues: Iterable[SubstrateIssue]) -> str:
    return "; ".join(issue.format() for issue in issues)


_AdapterRole = Literal["host-interface", "switch-port"]
_CoordinationRelationship = Literal["coordinates", "parent", "peer"]


def _unique_by_name[_NamedItem](
    items: Iterable[_NamedItem],
    category: str,
    key: Callable[[_NamedItem], str],
) -> dict[str, _NamedItem]:
    result: dict[str, _NamedItem] = {}
    for item in items:
        name = key(item)
        if name in result:
            raise CompilationError(f"duplicate {category} name: {name!r}")
        result[name] = item
    return result


@dataclass
class _AdapterDraft:
    name: str
    owner: str
    role: _AdapterRole
    number: int
    mtu: int
    ipv4_setting: IPv4Interface | str | None = None
    mac_setting: str | None = None
    ipv4: str | None = None
    mac: str | None = None


def _planned_node(resource: TopologyResource) -> PlannedResource:
    common = {
        "name": resource.name,
        "kind": resource.kind,
        "labels": resource.labels,
        "parent": resource.parent,
        "attributes": resource.attributes,
    }
    if isinstance(resource, NetworkResource):
        return PlannedNetwork(**common)
    if isinstance(resource, RegionResource):
        return PlannedRegion(**common)
    if isinstance(resource, FlowResource):
        return PlannedFlow(**common)
    if isinstance(resource, ControllerResource):
        return PlannedController(
            **common,
            type=resource.type,
            address=str(resource.address) if resource.address else None,
            protocol=resource.protocol,
            port=resource.port,
        )
    if isinstance(resource, ControllerDomainResource):
        return PlannedControllerDomain(
            **common, controllers=tuple(resource.controllers)
        )
    if isinstance(resource, SwitchResource):
        return PlannedSwitch(
            **common,
            failMode=resource.fail_mode,
            datapath=resource.datapath,
            controllers=tuple(resource.controllers),
            protocols=tuple(resource.protocols),
        )
    if isinstance(resource, HostResource):
        return PlannedHost(**common, defaultRoute=resource.default_route)
    raise CompilationError(f"unsupported resource type: {type(resource).__name__}")


def _validate_resource_graph(
    loaded: LoadedExperiment,
    nodes: dict[str, TopologyResource],
    reserved_names: set[str],
) -> None:
    for resource in loaded.topology.resources:
        if resource.parent and resource.parent not in nodes:
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
        if link.name in reserved_names:
            raise CompilationError(f"duplicate resource name: {link.name!r}")
        reserved_names.add(link.name)

    for resource in loaded.topology.resources:
        if not isinstance(resource, (ControllerDomainResource, SwitchResource)):
            continue
        for controller_name in resource.controllers:
            controller = nodes.get(controller_name)
            if controller is None:
                raise CompilationError(
                    f"resource {resource.name!r} references unknown controller "
                    f"{controller_name!r}"
                )
            if not isinstance(controller, ControllerResource):
                raise CompilationError(
                    f"resource {resource.name!r} controller reference "
                    f"{controller_name!r} is not a controller"
                )


def _declare_adapters(
    loaded: LoadedExperiment,
    reserved_names: set[str],
) -> tuple[dict[str, _AdapterDraft], dict[str, list[_AdapterDraft]]]:
    adapters: dict[str, _AdapterDraft] = {}
    by_owner: dict[str, list[_AdapterDraft]] = defaultdict(list)

    def register(adapter: _AdapterDraft) -> None:
        if adapter.name in reserved_names:
            raise CompilationError(f"duplicate resource or adapter name: {adapter.name!r}")
        reserved_names.add(adapter.name)
        adapters[adapter.name] = adapter
        by_owner[adapter.owner].append(adapter)

    for resource in loaded.topology.resources:
        if isinstance(resource, HostResource):
            for number, interface in enumerate(resource.interfaces):
                register(
                    _AdapterDraft(
                        name=interface.name or f"{resource.name}-eth{number}",
                        owner=resource.name,
                        role="host-interface",
                        number=number,
                        mtu=interface.mtu,
                        ipv4_setting=interface.ipv4,
                        mac_setting=interface.mac,
                    )
                )
        elif isinstance(resource, SwitchResource):
            explicit_numbers = [
                port.number for port in resource.ports if isinstance(port.number, int)
            ]
            if len(explicit_numbers) != len(set(explicit_numbers)):
                raise CompilationError(
                    f"switch {resource.name!r} has duplicate port numbers"
                )
            used_numbers = set(explicit_numbers)
            next_number = 1
            for port in resource.ports:
                if isinstance(port.number, int):
                    number = port.number
                else:
                    while next_number in used_numbers:
                        next_number += 1
                    number = next_number
                    used_numbers.add(number)
                    next_number += 1
                register(
                    _AdapterDraft(
                        name=port.name or f"{resource.name}-eth{number}",
                        owner=resource.name,
                        role="switch-port",
                        number=number,
                        mtu=port.mtu,
                    )
                )
    return adapters, by_owner


def _create_automatic_adapter(
    node: HostResource | SwitchResource,
    adapters: dict[str, _AdapterDraft],
    by_owner: dict[str, list[_AdapterDraft]],
    reserved_names: set[str],
) -> _AdapterDraft:
    used_numbers = {adapter.number for adapter in by_owner[node.name]}
    number = 0 if isinstance(node, HostResource) else 1
    while number in used_numbers:
        number += 1
    name = f"{node.name}-eth{number}"
    if name in reserved_names:
        raise CompilationError(
            f"cannot generate adapter {name!r}; the name is already in use"
        )
    if isinstance(node, HostResource):
        adapter = _AdapterDraft(
            name=name,
            owner=node.name,
            role="host-interface",
            number=number,
            mtu=1500,
            ipv4_setting="auto",
            mac_setting="auto",
        )
    else:
        adapter = _AdapterDraft(
            name=name,
            owner=node.name,
            role="switch-port",
            number=number,
            mtu=1500,
        )
    reserved_names.add(name)
    adapters[name] = adapter
    by_owner[node.name].append(adapter)
    return adapter


def _resolve_links(
    loaded: LoadedExperiment,
    nodes: dict[str, TopologyResource],
    adapters: dict[str, _AdapterDraft],
    by_owner: dict[str, list[_AdapterDraft]],
    reserved_names: set[str],
) -> list[PlannedLink]:
    used_adapters: set[str] = set()
    links: list[PlannedLink] = []
    for link in loaded.topology.links:
        endpoints: list[str] = []
        for endpoint in link.endpoints:
            node = nodes.get(endpoint.node)
            if node is None:
                raise CompilationError(
                    f"link {link.name!r} has unknown endpoint node {endpoint.node!r}"
                )
            if not isinstance(node, (HostResource, SwitchResource)):
                raise CompilationError(
                    f"link {link.name!r} endpoint {endpoint.node!r} is not a host "
                    "or switch"
                )
            if endpoint.adapter:
                adapter = adapters.get(endpoint.adapter)
                if adapter is None:
                    raise CompilationError(
                        f"link {link.name!r} references unknown adapter "
                        f"{endpoint.adapter!r}"
                    )
                if adapter.owner != endpoint.node:
                    raise CompilationError(
                        f"adapter {adapter.name!r} does not belong to "
                        f"{endpoint.node!r}"
                    )
            else:
                adapter = next(
                    (
                        candidate
                        for candidate in by_owner[endpoint.node]
                        if candidate.name not in used_adapters
                    ),
                    None,
                )
                if adapter is None:
                    adapter = _create_automatic_adapter(
                        node, adapters, by_owner, reserved_names
                    )
            if adapter.name in used_adapters:
                raise CompilationError(
                    f"adapter {adapter.name!r} is used by more than one link"
                )
            used_adapters.add(adapter.name)
            endpoints.append(adapter.name)

        links.append(
            PlannedLink(
                name=link.name,
                kind=ResourceKind.LINK,
                labels=link.labels,
                attributes=link.attributes,
                endpoints=(endpoints[0], endpoints[1]),
                bandwidth=link.bandwidth,
                delay=link.delay,
                jitter=link.jitter,
                loss=link.loss,
                maxQueueSize=link.max_queue_size,
            )
        )
    return links


def _allocate_addresses(
    loaded: LoadedExperiment, adapters: dict[str, _AdapterDraft]
) -> None:
    host_adapters = sorted(
        (adapter for adapter in adapters.values() if adapter.role == "host-interface"),
        key=lambda adapter: adapter.name,
    )
    subnet = loaded.topology.addressing.ipv4.subnet
    used_ips: dict[IPv4Address, str] = {}

    for adapter in host_adapters:
        setting = adapter.ipv4_setting
        if not isinstance(setting, IPv4Interface):
            continue
        address = setting.ip
        if address not in subnet:
            raise CompilationError(
                f"interface {adapter.name!r} address {address} is outside {subnet}"
            )
        if address in {subnet.network_address, subnet.broadcast_address}:
            raise CompilationError(
                f"interface {adapter.name!r} uses reserved address {address}"
            )
        if address in used_ips:
            raise CompilationError(
                f"interfaces {used_ips[address]!r} and {adapter.name!r} share "
                f"address {address}"
            )
        used_ips[address] = adapter.name
        adapter.ipv4 = str(setting)

    candidate = int(subnet.network_address) + 1
    last = int(subnet.broadcast_address) - 1
    for adapter in host_adapters:
        if adapter.ipv4_setting == "none":
            continue
        if adapter.ipv4 is not None:
            continue
        while candidate <= last and IPv4Address(candidate) in used_ips:
            candidate += 1
        if candidate > last:
            raise CompilationError(f"IPv4 allocation pool {subnet} is exhausted")
        address = IPv4Address(candidate)
        adapter.ipv4 = f"{address}/{subnet.prefixlen}"
        used_ips[address] = adapter.name
        candidate += 1

    used_macs: dict[str, str] = {}
    for adapter in host_adapters:
        setting = adapter.mac_setting
        if not setting or setting == "auto":
            continue
        normalized = setting.lower()
        if normalized in used_macs:
            raise CompilationError(
                f"interfaces {used_macs[normalized]!r} and {adapter.name!r} share "
                f"MAC {normalized}"
            )
        used_macs[normalized] = adapter.name
        adapter.mac = normalized

    prefix = loaded.topology.addressing.mac.prefix
    for adapter in host_adapters:
        if adapter.mac is not None:
            continue
        for suffix in range(1, 0x1000000):
            candidate_mac = prefix + ":" + ":".join(
                f"{octet:02x}"
                for octet in suffix.to_bytes(3, byteorder="big")
            )
            if candidate_mac not in used_macs:
                break
        else:
            raise CompilationError(f"MAC allocation prefix {prefix} is exhausted")
        adapter.mac = candidate_mac
        used_macs[candidate_mac] = adapter.name


def _build_resources(loaded: LoadedExperiment) -> tuple[PlannedResource, ...]:
    nodes: dict[str, TopologyResource] = {}
    for resource in loaded.topology.resources:
        if resource.name in nodes:
            raise CompilationError(f"duplicate resource name: {resource.name!r}")
        nodes[resource.name] = resource

    reserved_names = set(nodes)
    _validate_resource_graph(loaded, nodes, reserved_names)
    adapters, by_owner = _declare_adapters(loaded, reserved_names)
    links = _resolve_links(
        loaded, nodes, adapters, by_owner, reserved_names
    )
    _allocate_addresses(loaded, adapters)

    resources: list[PlannedResource] = [
        _planned_node(resource) for resource in loaded.topology.resources
    ]
    resources.extend(
        PlannedPort(
            name=adapter.name,
            kind=ResourceKind.PORT,
            parent=adapter.owner,
            role=adapter.role,
            number=adapter.number,
            ipv4=adapter.ipv4,
            mac=adapter.mac,
            mtu=adapter.mtu,
        )
        for adapter in adapters.values()
    )
    resources.extend(links)
    return tuple(sorted(resources, key=lambda item: (item.kind.value, item.name)))


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
    driver: SubstrateDriver,
) -> tuple[AgentInstance, ...]:
    _unique_by_name(
        loaded.experiment.agents,
        "agent deployment",
        lambda deployment: deployment.name,
    )
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
        policy_observations = [
            policy.observation for policy in deployment.observation_policies
        ]
        duplicate_policies = sorted(
            observation
            for observation in set(policy_observations)
            if policy_observations.count(observation) > 1
        )
        if duplicate_policies:
            raise CompilationError(
                f"agent {deployment.name!r} has duplicate observation policies: "
                + ", ".join(duplicate_policies)
            )
        undeclared_policies = sorted(
            set(policy_observations) - set(deployment.observe)
        )
        if undeclared_policies:
            raise CompilationError(
                "observation policy references undeclared observation "
                f"{undeclared_policies[0]!r}"
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

            issues = driver.validate_attachment(
                layer=layer,
                custom_layer=deployment.placement.custom_layer,
                target_kind=target_kind,
                runtime=deployment.placement.runtime,
            )
            if issues:
                raise CompilationError(
                    f"agent {deployment.name!r}: {_format_substrate_issues(issues)}"
                )

            for observation in deployment.observe:
                issues = driver.validate_observation(
                    layer=layer,
                    custom_layer=deployment.placement.custom_layer,
                    name=observation,
                )
                if issues:
                    raise CompilationError(
                        f"agent {deployment.name!r}: "
                        f"{_format_substrate_issues(issues)}"
                    )

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
                for postcondition in capability.postconditions:
                    issues = driver.validate_observation(
                        layer=layer,
                        custom_layer=deployment.placement.custom_layer,
                        name=postcondition.observation,
                    )
                    if issues:
                        raise CompilationError(
                            f"agent {deployment.name!r}: capability "
                            f"{capability_name!r} postcondition observation "
                            f"{postcondition.observation!r}: "
                            f"{_format_substrate_issues(issues)}"
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
                    triggers=tuple(deployment.triggers),
                    observationPolicies=tuple(deployment.observation_policies),
                    memory=blueprints[deployment.blueprint].memory,
                    execution=deployment.execution,
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
    edges: set[tuple[str, str, _CoordinationRelationship]] = set()

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
            resources_by_name = {resource.name: resource for resource in resources}
            links = [
                resource for resource in resources if isinstance(resource, PlannedLink)
            ]
            neighbors: set[frozenset[str]] = set()
            for link in links:
                endpoint_nodes = []
                for endpoint in link.endpoints:
                    resource = resources_by_name[endpoint]
                    endpoint_nodes.append(resource.parent or resource.name)
                neighbors.add(frozenset(endpoint_nodes))
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
    try:
        driver = create_substrate_driver(
            loaded.experiment.substrate.driver,
            loaded.experiment.substrate.options,
        )
    except (LookupError, TypeError, ValueError) as error:
        raise CompilationError(str(error)) from error
    option_issues = driver.validate_options()
    if option_issues:
        raise CompilationError(
            f"substrate {driver.name!r}: {_format_substrate_issues(option_issues)}"
        )

    blueprints = _unique_by_name(
        loaded.blueprints,
        "blueprint",
        lambda blueprint: blueprint.metadata.name,
    )
    capabilities = _unique_by_name(
        loaded.capabilities,
        "capability",
        lambda capability: capability.metadata.name,
    )
    resources = _build_resources(loaded)
    resource_issues = driver.validate_resources(resources)
    if resource_issues:
        raise CompilationError(
            f"substrate {driver.name!r}: {_format_substrate_issues(resource_issues)}"
        )
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
        resourceLimits=loaded.experiment.resource_limits,
        snapshot=snapshot,
    )
