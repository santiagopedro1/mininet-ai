"""Normalized discovery and telemetry for live Mininet/OVS runs."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import ip_interface
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from mininet_ai.specification.models import ControllerType, ResourceKind
from mininet_ai.substrates.runtime import (
    LiveResource,
    ObservationQuery,
    ResourceOperationalState,
)

if TYPE_CHECKING:
    from mininet_ai.compiler.models import (
        DeploymentPlan,
        PlannedController,
        PlannedControllerDomain,
        PlannedLink,
        PlannedPort,
        PlannedResource,
        PlannedSwitch,
    )


_COUNTER_NAMES = {
    "collisions": "collisions",
    "rx_bytes": "rxBytes",
    "rx_crc_err": "rxCrcErrors",
    "rx_dropped": "rxDropped",
    "rx_errors": "rxErrors",
    "rx_frame_err": "rxFrameErrors",
    "rx_over_err": "rxOverErrors",
    "rx_packets": "rxPackets",
    "tx_bytes": "txBytes",
    "tx_dropped": "txDropped",
    "tx_errors": "txErrors",
    "tx_packets": "txPackets",
}
_FLOW_FIELDS = {
    "cookie": ("cookie", str),
    "duration": ("durationSeconds", lambda value: float(value.removesuffix("s"))),
    "table": ("table", int),
    "n_packets": ("packets", int),
    "n_bytes": ("bytes", int),
    "priority": ("priority", int),
    "idle_age": ("idleAgeSeconds", int),
    "hard_age": ("hardAgeSeconds", int),
}


@dataclass(frozen=True)
class CommandResult:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class CommandExecutor(Protocol):
    def run(
        self, arguments: Sequence[str], *, node: Any | None = None
    ) -> CommandResult:
        """Run one command in the root or a Mininet node namespace."""


class ObservationProvider(Protocol):
    def snapshot(self) -> tuple[LiveResource, ...]:
        """Discover the current operational state of planned resources."""

    def collect(self, query: ObservationQuery) -> dict[str, Any]:
        """Collect one normalized observation for each requested target."""


class LocalCommandExecutor:
    """Production command adapter for the orchestrator and Mininet nodes."""

    def run(
        self, arguments: Sequence[str], *, node: Any | None = None
    ) -> CommandResult:
        if node is not None:
            output, error, status = node.pexec(list(arguments))
            return CommandResult(output, error, status)
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return CommandResult(
            completed.stdout,
            completed.stderr,
            completed.returncode,
        )


class ObservationCollectionError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class MininetOVSObservations:
    """Expose live topology and telemetry through two normalized operations."""

    def __init__(
        self,
        plan: DeploymentPlan,
        network: Any,
        *,
        executor: CommandExecutor | None = None,
    ) -> None:
        self._plan = plan
        self._network = network
        self._executor = executor or LocalCommandExecutor()
        self._resources = {resource.name: resource for resource in plan.resources}
        self._interfaces: dict[str, dict[str, Any] | None] = {}

    def snapshot(self) -> tuple[LiveResource, ...]:
        self._interfaces.clear()
        states: dict[str, ResourceOperationalState] = {}
        return tuple(
            self._live_resource(resource, states)
            for resource in self._plan.resources
        )

    def collect(self, query: ObservationQuery) -> dict[str, Any]:
        self._interfaces.clear()
        handlers = {
            "topology.resources": self._topology_resources,
            "topology.neighbors": self._topology_neighbors,
            "controller.events": self._controller_events,
            "openflow.flows": self._openflow_flows,
            "ovs.port-counters": self._port_counters,
            "tc.queue-occupancy": self._queue_occupancy,
            "host.interfaces": self._host_interfaces,
            "host.processes": self._host_processes,
            "host.reachability": self._host_reachability,
        }
        try:
            handler = handlers[query.name]
        except KeyError as error:
            raise ObservationCollectionError(
                f"observation {query.name!r} is not implemented",
                code="runtime.observation.unsupported",
            ) from error
        return handler(query)

    def _live_resource(
        self,
        resource: PlannedResource,
        states: dict[str, ResourceOperationalState],
    ) -> LiveResource:
        state = self._resource_state(resource, states)
        attributes = resource.model_dump(
            mode="json",
            by_alias=True,
            exclude={"name", "kind"},
            exclude_none=True,
        )
        if resource.kind in {ResourceKind.CONTROLLER, ResourceKind.HOST}:
            node = self._node(resource.name)
            pid = getattr(node, "pid", None)
            if isinstance(pid, int):
                attributes["pid"] = pid
        elif resource.kind == ResourceKind.PORT:
            interface = self._interface(cast("PlannedPort", resource))
            if interface is not None:
                attributes["operState"] = interface.get("operstate", "UNKNOWN")
                attributes["carrier"] = interface.get("carrier")
        return LiveResource(
            name=resource.name,
            kind=resource.kind,
            state=state,
            attributes=attributes,
        )

    def _resource_state(
        self,
        resource: PlannedResource,
        states: dict[str, ResourceOperationalState],
    ) -> ResourceOperationalState:
        if resource.name in states:
            return states[resource.name]
        if resource.kind == ResourceKind.SWITCH:
            result = self._run(
                ("ovs-vsctl", "--timeout=5", "br-exists", resource.name)
            )
            state = self._up_or_down(result.returncode == 0)
        elif resource.kind in {ResourceKind.CONTROLLER, ResourceKind.HOST}:
            state = self._up_or_down(self._node_is_alive(resource.name))
        elif resource.kind == ResourceKind.PORT:
            interface = self._interface(cast("PlannedPort", resource))
            state = self._up_or_down(
                interface is not None and "UP" in interface.get("flags", ())
            )
        elif resource.kind == ResourceKind.LINK:
            link = cast("PlannedLink", resource)
            endpoints = [self._resources[name] for name in link.endpoints]
            state = self._up_or_down(
                all(
                    self._resource_state(endpoint, states)
                    == ResourceOperationalState.UP
                    for endpoint in endpoints
                )
            )
        elif resource.kind == ResourceKind.FLOW:
            state = ResourceOperationalState.UNKNOWN
        else:
            state = ResourceOperationalState.UP
        states[resource.name] = state
        return state

    @staticmethod
    def _up_or_down(value: bool) -> ResourceOperationalState:
        return (
            ResourceOperationalState.UP
            if value
            else ResourceOperationalState.DOWN
        )

    def _node_is_alive(self, name: str) -> bool:
        node = self._node(name)
        pid = getattr(node, "pid", None)
        if not isinstance(pid, int):
            return node is not None
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except ProcessLookupError:
            return False
        return True

    def _node(self, name: str) -> Any | None:
        try:
            return self._network.get(name)
        except (KeyError, TypeError):
            return None

    def _interface(self, port: PlannedPort) -> dict[str, Any] | None:
        if port.name in self._interfaces:
            return self._interfaces[port.name]
        node = self._node(port.parent)
        result = self._run(
            ("ip", "-j", "-s", "address", "show", "dev", port.name),
            node=node if port.role == "host-interface" else None,
        )
        if result.returncode != 0:
            self._interfaces[port.name] = None
            return None
        try:
            records = json.loads(result.stdout)
            if not isinstance(records, list) or any(
                not isinstance(record, dict) for record in records
            ):
                raise TypeError("expected a JSON array of interface objects")
            interface = records[0] if records else None
        except (TypeError, json.JSONDecodeError) as error:
            raise ObservationCollectionError(
                f"invalid interface telemetry for {port.name!r}: {error}",
                code="runtime.observation.invalid-output",
            ) from error
        self._interfaces[port.name] = interface
        return interface

    def _topology_resources(self, query: ObservationQuery) -> dict[str, Any]:
        resources = {resource.name: resource for resource in self.snapshot()}
        return {
            target: resources[target].model_dump(mode="json", by_alias=True)
            for target in query.targets
        }

    def _topology_neighbors(self, query: ObservationQuery) -> dict[str, Any]:
        values = {}
        for target in query.targets:
            resource = self._resources[target]
            links = self._links_for(resource)
            values[target] = {
                "links": [
                    {
                        "name": link.name,
                        "endpoints": [
                            {
                                "port": port_name,
                                "node": self._resources[port_name].parent,
                            }
                            for port_name in link.endpoints
                        ],
                    }
                    for link in links
                ]
            }
        return values

    def _links_for(self, target: PlannedResource) -> list[PlannedLink]:
        links = [
            cast("PlannedLink", resource)
            for resource in self._plan.resources
            if resource.kind == ResourceKind.LINK
        ]
        if target.kind == ResourceKind.LINK:
            return [link for link in links if link.name == target.name]
        if target.kind == ResourceKind.PORT:
            return [link for link in links if target.name in link.endpoints]
        descendants = self._descendant_names(target.name) | {target.name}
        if target.kind == ResourceKind.CONTROLLER:
            descendants.update(
                resource.name
                for resource in self._plan.resources
                if resource.kind == ResourceKind.SWITCH
                and target.name in cast("PlannedSwitch", resource).controllers
            )
        return [
            link
            for link in links
            if any(
                self._resources[endpoint].parent in descendants
                for endpoint in link.endpoints
            )
        ]

    def _controller_events(self, query: ObservationQuery) -> dict[str, Any]:
        limit = self._limit(query)
        values = {}
        for target in query.targets:
            controllers = self._selected(
                target, kinds={ResourceKind.CONTROLLER}
            )
            if not controllers:
                self._invalid_target(query.name, target, "controller scope")
            events = []
            for resource in controllers:
                controller = cast("PlannedController", resource)
                if controller.controller_type != ControllerType.BUILTIN:
                    continue
                path = str(Path("/tmp") / f"{controller.name}.log")
                result = self._run(("tail", "-n", str(limit), path))
                if result.returncode != 0:
                    continue
                events.extend(
                    {"controller": controller.name, "message": line}
                    for line in result.stdout.splitlines()
                    if line
                )
            values[target] = {"events": events[-limit:]}
        return values

    def _openflow_flows(self, query: ObservationQuery) -> dict[str, Any]:
        values = {}
        for target in query.targets:
            switches = self._selected(target, kinds={ResourceKind.SWITCH})
            if not switches:
                self._invalid_target(query.name, target, "switch scope")
            values[target] = {
                "switches": [
                    {
                        "name": switch.name,
                        "flows": self._flows(cast("PlannedSwitch", switch)),
                    }
                    for switch in switches
                ]
            }
        return values

    def _flows(self, switch: PlannedSwitch) -> list[dict[str, Any]]:
        protocol = switch.protocols[-1].value if switch.protocols else "OpenFlow13"
        result = self._checked(
            ("ovs-ofctl", "-O", protocol, "dump-flows", switch.name),
            subject=switch.name,
        )
        return [
            parsed
            for line in result.stdout.splitlines()
            if (parsed := self._parse_flow(line)) is not None
        ]

    @staticmethod
    def _parse_flow(line: str) -> dict[str, Any] | None:
        if " actions=" not in line:
            return None
        fields, actions = line.strip().split(" actions=", 1)
        normalized: dict[str, Any] = {"match": {}, "actions": actions}
        for token in (item.strip() for item in fields.split(",")):
            if not token:
                continue
            name, separator, value = token.partition("=")
            if name in _FLOW_FIELDS and separator:
                output_name, converter = _FLOW_FIELDS[name]
                try:
                    normalized[output_name] = converter(value)
                except ValueError:
                    normalized[output_name] = value
            elif separator:
                normalized["match"][name] = value
            else:
                normalized["match"][token] = True
        return normalized

    def _port_counters(self, query: ObservationQuery) -> dict[str, Any]:
        values = {}
        for target in query.targets:
            ports = [
                cast("PlannedPort", resource)
                for resource in self._selected(target, kinds={ResourceKind.PORT})
                if cast("PlannedPort", resource).role == "switch-port"
            ]
            if not ports:
                self._invalid_target(query.name, target, "OVS port scope")
            values[target] = {
                "ports": [
                    {"name": port.name, **self._ovs_counters(port.name)}
                    for port in ports
                ]
            }
        return values

    def _ovs_counters(self, name: str) -> dict[str, int]:
        result = self._checked(
            ("ovs-vsctl", "--timeout=5", "get", "Interface", name, "statistics"),
            subject=name,
        )
        raw = {
            key: int(value)
            for key, value in re.findall(r"([a-z0-9_]+)=(-?\d+)", result.stdout)
        }
        return {
            output_name: raw.get(input_name, 0)
            for input_name, output_name in _COUNTER_NAMES.items()
        }

    def _queue_occupancy(self, query: ObservationQuery) -> dict[str, Any]:
        values = {}
        for target in query.targets:
            ports = [
                cast("PlannedPort", resource)
                for resource in self._selected(target, kinds={ResourceKind.PORT})
            ]
            if not ports:
                self._invalid_target(query.name, target, "port or link scope")
            values[target] = {
                "ports": [
                    {"name": port.name, "qdiscs": self._qdiscs(port)}
                    for port in ports
                ]
            }
        return values

    def _qdiscs(self, port: PlannedPort) -> list[dict[str, Any]]:
        node = self._node(port.parent)
        result = self._checked(
            ("tc", "-j", "-s", "qdisc", "show", "dev", port.name),
            subject=port.name,
            node=node if port.role == "host-interface" else None,
        )
        try:
            qdiscs = json.loads(result.stdout)
            if not isinstance(qdiscs, list) or any(
                not isinstance(qdisc, dict) for qdisc in qdiscs
            ):
                raise TypeError("expected a JSON array of qdisc objects")
        except (TypeError, json.JSONDecodeError) as error:
            raise ObservationCollectionError(
                f"invalid queue telemetry for {port.name!r}: {error}",
                code="runtime.observation.invalid-output",
            ) from error
        fields = (
            "kind",
            "handle",
            "parent",
            "root",
            "bytes",
            "packets",
            "drops",
            "overlimits",
            "requeues",
            "backlog",
            "qlen",
        )
        return [
            {name: qdisc[name] for name in fields if name in qdisc}
            for qdisc in qdiscs
        ]

    def _host_interfaces(self, query: ObservationQuery) -> dict[str, Any]:
        values = {}
        for target in query.targets:
            ports = [
                cast("PlannedPort", resource)
                for resource in self._selected(target, kinds={ResourceKind.PORT})
                if cast("PlannedPort", resource).role == "host-interface"
            ]
            if not ports:
                self._invalid_target(query.name, target, "host scope")
            values[target] = {
                "interfaces": [
                    self._normalized_interface(port)
                    for port in ports
                    if self._interface(port) is not None
                ]
            }
        return values

    def _normalized_interface(self, port: PlannedPort) -> dict[str, Any]:
        interface = self._interface(port) or {}
        statistics = interface.get("stats64", interface.get("stats", {}))
        addresses = [
            {
                name: address[name]
                for name in ("family", "local", "prefixlen", "scope")
                if name in address
            }
            for address in interface.get("addr_info", ())
        ]
        return {
            "name": port.name,
            "index": interface.get("ifindex"),
            "state": interface.get("operstate", "UNKNOWN"),
            "mtu": interface.get("mtu"),
            "mac": interface.get("address"),
            "addresses": addresses,
            "rx": self._ip_statistics(statistics.get("rx", {})),
            "tx": self._ip_statistics(statistics.get("tx", {})),
        }

    @staticmethod
    def _ip_statistics(statistics: dict[str, Any]) -> dict[str, int]:
        return {
            name: int(statistics.get(name, 0))
            for name in ("bytes", "packets", "errors", "dropped")
        }

    def _host_processes(self, query: ObservationQuery) -> dict[str, Any]:
        limit = self._limit(query)
        result = self._checked(
            ("ps", "-eo", "pid=,ppid=,stat=,comm=,args=", "--sort=pid"),
            subject="host processes",
        )
        processes = self._parse_processes(result.stdout)
        values = {}
        for target in query.targets:
            resource = self._resources[target]
            if resource.kind != ResourceKind.HOST:
                self._invalid_target(query.name, target, "host")
            node = self._node(target)
            root_pid = getattr(node, "pid", None)
            selected = self._process_descendants(processes, root_pid)
            values[target] = {"processes": selected[:limit]}
        return values

    def _host_reachability(self, query: ObservationQuery) -> dict[str, Any]:
        destinations = query.parameters.get("destinations")
        if destinations is not None and (
            not isinstance(destinations, (list, tuple))
            or not destinations
            or any(not isinstance(name, str) for name in destinations)
        ):
            raise ObservationCollectionError(
                "observation parameter 'destinations' must be a non-empty list "
                "of host names",
                code="runtime.observation.invalid-parameters",
            )
        timeout = query.parameters.get("timeoutSeconds", 1)
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0 < timeout <= 10
        ):
            raise ObservationCollectionError(
                "observation parameter 'timeoutSeconds' must be a number "
                "greater than 0 and at most 10",
                code="runtime.observation.invalid-parameters",
            )

        all_hosts = [
            resource
            for resource in self._plan.resources
            if resource.kind == ResourceKind.HOST
        ]
        by_name = {host.name: host for host in all_hosts}
        if destinations is not None:
            unknown = sorted(set(destinations) - set(by_name))
            if unknown:
                raise ObservationCollectionError(
                    "unknown reachability destination hosts: " + ", ".join(unknown),
                    code="runtime.observation.invalid-parameters",
                )
            destination_hosts = [by_name[name] for name in destinations]
        else:
            destination_hosts = all_hosts

        values = {}
        for target in query.targets:
            sources = self._selected(target, kinds={ResourceKind.HOST})
            if not sources:
                self._invalid_target(query.name, target, "host scope")
            probes = []
            for source in sources:
                node = self._node(source.name)
                if node is None:
                    raise ObservationCollectionError(
                        f"host {source.name!r} is not present in the live topology",
                        code="runtime.observation.failed",
                    )
                for destination in destination_hosts:
                    if source.name == destination.name:
                        continue
                    address = self._host_address(destination.name)
                    result = self._run(
                        (
                            "ping",
                            "-n",
                            "-c",
                            "1",
                            "-W",
                            str(math.ceil(timeout)),
                            address,
                        ),
                        node=node,
                    )
                    match = re.search(r"time[=<]([0-9.]+)\s*ms", result.stdout)
                    probes.append(
                        {
                            "source": source.name,
                            "destination": destination.name,
                            "address": address,
                            "reachable": result.returncode == 0,
                            "latencyMs": (
                                float(match.group(1)) if match is not None else None
                            ),
                        }
                    )
            values[target] = {"probes": probes}
        return values

    def _host_address(self, host_name: str) -> str:
        ports = [
            cast("PlannedPort", resource)
            for resource in self._selected(host_name, kinds={ResourceKind.PORT})
            if cast("PlannedPort", resource).role == "host-interface"
            and cast("PlannedPort", resource).ipv4 is not None
        ]
        if not ports:
            raise ObservationCollectionError(
                f"host {host_name!r} has no planned IPv4 address",
                code="runtime.observation.invalid-target",
            )
        return str(ip_interface(ports[0].ipv4).ip)

    @staticmethod
    def _parse_processes(output: str) -> list[dict[str, Any]]:
        processes = []
        for line in output.splitlines():
            fields = line.strip().split(maxsplit=4)
            if len(fields) < 4:
                continue
            processes.append(
                {
                    "pid": int(fields[0]),
                    "parentPid": int(fields[1]),
                    "state": fields[2],
                    "command": fields[3],
                    "arguments": fields[4] if len(fields) == 5 else fields[3],
                }
            )
        return processes

    @staticmethod
    def _process_descendants(
        processes: list[dict[str, Any]], root_pid: Any
    ) -> list[dict[str, Any]]:
        if not isinstance(root_pid, int):
            return []
        selected_pids = {root_pid}
        changed = True
        while changed:
            changed = False
            for process in processes:
                if (
                    process["parentPid"] in selected_pids
                    and process["pid"] not in selected_pids
                ):
                    selected_pids.add(process["pid"])
                    changed = True
        return [process for process in processes if process["pid"] in selected_pids]

    def _selected(
        self, target: str, *, kinds: set[ResourceKind]
    ) -> list[PlannedResource]:
        selected = []
        descendants = self._descendant_names(target) | {target}
        target_resource = self._resources[target]
        for resource in self._plan.resources:
            if resource.kind not in kinds:
                continue
            if resource.name in descendants:
                selected.append(resource)
            elif (
                resource.kind == ResourceKind.SWITCH
                and target_resource.kind == ResourceKind.CONTROLLER
                and target in cast("PlannedSwitch", resource).controllers
            ):
                selected.append(resource)
            elif (
                resource.kind == ResourceKind.CONTROLLER
                and target_resource.kind == ResourceKind.CONTROLLER_DOMAIN
                and resource.name
                in cast("PlannedControllerDomain", target_resource).controllers
            ):
                selected.append(resource)
            elif (
                resource.kind == ResourceKind.PORT
                and target_resource.kind == ResourceKind.LINK
            ):
                link = cast("PlannedLink", target_resource)
                if resource.name in link.endpoints:
                    selected.append(resource)
        return selected

    def _descendant_names(self, target: str) -> set[str]:
        descendants: set[str] = set()
        changed = True
        while changed:
            changed = False
            for resource in self._plan.resources:
                if (
                    resource.parent in descendants | {target}
                    and resource.name not in descendants
                ):
                    descendants.add(resource.name)
                    changed = True
        return descendants

    @staticmethod
    def _limit(query: ObservationQuery) -> int:
        value = query.parameters.get("limit", 100)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= 1_000
        ):
            raise ObservationCollectionError(
                "observation parameter 'limit' must be an integer from 1 to 1000",
                code="runtime.observation.invalid-parameters",
            )
        return value

    @staticmethod
    def _invalid_target(name: str, target: str, expected: str) -> None:
        raise ObservationCollectionError(
            f"observation {name!r} cannot target {target!r}; expected {expected}",
            code="runtime.observation.invalid-target",
        )

    def _checked(
        self,
        arguments: Sequence[str],
        *,
        subject: str,
        node: Any | None = None,
    ) -> CommandResult:
        result = self._run(arguments, node=node)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ObservationCollectionError(
                f"could not collect telemetry for {subject!r}: {detail}",
                code="runtime.observation.failed",
            )
        return result

    def _run(
        self, arguments: Sequence[str], *, node: Any | None = None
    ) -> CommandResult:
        try:
            return self._executor.run(arguments, node=node)
        except (OSError, subprocess.SubprocessError) as error:
            raise ObservationCollectionError(
                f"telemetry command {' '.join(arguments)!r} failed: {error}",
                code="runtime.observation.failed",
            ) from error
