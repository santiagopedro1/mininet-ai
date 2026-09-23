"""Rootless planning validation for the Mininet/OVS substrate.

This module deliberately does not import Mininet or execute host commands. It
describes what the future privileged runtime can deploy and rejects plans that
cannot be represented safely by Linux interfaces, Open vSwitch, and ``tc``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from mininet_ai.specification.models import (
    AttachmentLayer,
    ControllerProtocol,
    ControllerType,
    ResourceKind,
)
from mininet_ai.substrates.protocol import (
    LayerSupport,
    ManifestSubstrateDriver,
    SubstrateIssue,
    SubstrateManifest,
)

if TYPE_CHECKING:
    from mininet_ai.compiler.models import (
        PlannedController,
        PlannedLink,
        PlannedPort,
        PlannedResource,
        PlannedSwitch,
    )


LINUX_INTERFACE_NAME_MAX_BYTES = 15
OPENFLOW_PHYSICAL_PORT_MAX = 65_279
MININET_TC_BANDWIDTH_MAX_MBPS = 1_000


_OBSERVATIONS: dict[AttachmentLayer, frozenset[str]] = {
    AttachmentLayer.GLOBAL: frozenset(
        {"topology.resources", "topology.neighbors"}
    ),
    AttachmentLayer.MANAGEMENT: frozenset(
        {"topology.resources", "topology.neighbors", "controller.events"}
    ),
    AttachmentLayer.CONTROL: frozenset(
        {
            "topology.resources",
            "topology.neighbors",
            "controller.events",
            "openflow.flows",
        }
    ),
    AttachmentLayer.DATA: frozenset(
        {
            "topology.neighbors",
            "openflow.flows",
            "ovs.port-counters",
            "tc.queue-occupancy",
        }
    ),
    AttachmentLayer.HOST: frozenset(
        {"topology.neighbors", "host.interfaces", "host.processes"}
    ),
}
_OBSERVATIONS[AttachmentLayer.OBSERVER] = frozenset().union(
    *_OBSERVATIONS.values()
)


_LAYERS = MappingProxyType(
    {
        AttachmentLayer.GLOBAL: LayerSupport(
            targets=frozenset({ResourceKind.NETWORK}),
            runtimes=frozenset({"orchestrator", "process", "container"}),
            observations=_OBSERVATIONS[AttachmentLayer.GLOBAL],
        ),
        AttachmentLayer.MANAGEMENT: LayerSupport(
            targets=frozenset(
                {
                    ResourceKind.NETWORK,
                    ResourceKind.REGION,
                    ResourceKind.CONTROLLER,
                    ResourceKind.CONTROLLER_DOMAIN,
                }
            ),
            runtimes=frozenset({"orchestrator", "process", "container"}),
            observations=_OBSERVATIONS[AttachmentLayer.MANAGEMENT],
        ),
        AttachmentLayer.CONTROL: LayerSupport(
            targets=frozenset(
                {
                    ResourceKind.CONTROLLER,
                    ResourceKind.CONTROLLER_DOMAIN,
                    ResourceKind.SWITCH,
                }
            ),
            runtimes=frozenset(
                {"controller-sidecar", "process", "container"}
            ),
            observations=_OBSERVATIONS[AttachmentLayer.CONTROL],
        ),
        AttachmentLayer.DATA: LayerSupport(
            targets=frozenset(
                {
                    ResourceKind.SWITCH,
                    ResourceKind.PORT,
                    ResourceKind.LINK,
                    ResourceKind.FLOW,
                }
            ),
            runtimes=frozenset({"device-sidecar", "process", "container"}),
            observations=_OBSERVATIONS[AttachmentLayer.DATA],
        ),
        AttachmentLayer.HOST: LayerSupport(
            targets=frozenset({ResourceKind.HOST}),
            runtimes=frozenset(
                {"host-namespace", "process", "container"}
            ),
            observations=_OBSERVATIONS[AttachmentLayer.HOST],
        ),
        AttachmentLayer.OBSERVER: LayerSupport(
            targets=frozenset(ResourceKind),
            runtimes=frozenset({"orchestrator", "process", "container"}),
            observations=_OBSERVATIONS[AttachmentLayer.OBSERVER],
        ),
    }
)


def _issue(
    suffix: str, message: str, *, index: int, field: str
) -> SubstrateIssue:
    return SubstrateIssue(
        code=f"mininet-ovs.{suffix}",
        message=message,
        path=f"resources.{index}.{field}",
    )


def _interface_name_issue(
    name: str, *, index: int, field: str = "name"
) -> SubstrateIssue | None:
    size = len(name.encode("utf-8"))
    if size <= LINUX_INTERFACE_NAME_MAX_BYTES:
        return None
    return _issue(
        "resource.interface-name",
        (
            f"Linux interface name {name!r} uses {size} bytes; the maximum is "
            f"{LINUX_INTERFACE_NAME_MAX_BYTES}"
        ),
        index=index,
        field=field,
    )


class MininetOVSDriver(ManifestSubstrateDriver):
    """Compile-time capabilities and constraints for Mininet with OVS."""

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options = dict(options or {})
        self.manifest = SubstrateManifest(
            name="mininet-ovs",
            resource_kinds=frozenset(ResourceKind),
            layers=_LAYERS,
        )

    def validate_options(self) -> tuple[SubstrateIssue, ...]:
        return tuple(
            SubstrateIssue(
                code="mininet-ovs.options.unknown",
                message=f"unknown Mininet/OVS substrate option {name!r}",
                path=f"options.{name}",
            )
            for name in sorted(self.options)
        )

    def validate_resources(
        self, resources: Sequence[PlannedResource]
    ) -> tuple[SubstrateIssue, ...]:
        issues = list(super().validate_resources(resources))
        local_controllers: dict[int, tuple[int, str]] = {}

        for index, resource in enumerate(resources):
            if resource.kind == ResourceKind.SWITCH:
                switch = cast("PlannedSwitch", resource)
                name_issue = _interface_name_issue(switch.name, index=index)
                if name_issue:
                    issues.append(name_issue)

            elif resource.kind == ResourceKind.PORT:
                port = cast("PlannedPort", resource)
                name_issue = _interface_name_issue(port.name, index=index)
                if name_issue:
                    issues.append(name_issue)
                if port.number > OPENFLOW_PHYSICAL_PORT_MAX:
                    issues.append(
                        _issue(
                            "resource.port-number",
                            (
                                f"OpenFlow physical port number {port.number} exceeds "
                                f"{OPENFLOW_PHYSICAL_PORT_MAX}"
                            ),
                            index=index,
                            field="number",
                        )
                    )

            elif resource.kind == ResourceKind.CONTROLLER:
                controller = cast("PlannedController", resource)
                if controller.protocol == ControllerProtocol.SSL:
                    issues.append(
                        _issue(
                            "resource.controller-protocol",
                            "SSL controllers require certificate configuration that is not yet supported",
                            index=index,
                            field="protocol",
                        )
                    )
                if controller.controller_type == ControllerType.BUILTIN:
                    previous = local_controllers.get(controller.port)
                    if previous is not None:
                        previous_index, previous_name = previous
                        issues.append(
                            _issue(
                                "resource.controller-endpoint",
                                (
                                    f"built-in controllers {previous_name!r} and "
                                    f"{controller.name!r} share local listening port "
                                    f"{controller.port} (first declared at "
                                    f"resources.{previous_index})"
                                ),
                                index=index,
                                field="port",
                            )
                        )
                    else:
                        local_controllers[controller.port] = (index, controller.name)

            elif resource.kind == ResourceKind.LINK:
                link = cast("PlannedLink", resource)
                if (
                    link.bandwidth is not None
                    and link.bandwidth > MININET_TC_BANDWIDTH_MAX_MBPS
                ):
                    issues.append(
                        _issue(
                            "resource.link-bandwidth",
                            (
                                f"Mininet TCLink bandwidth {link.bandwidth:g} Mbps "
                                f"exceeds {MININET_TC_BANDWIDTH_MAX_MBPS} Mbps"
                            ),
                            index=index,
                            field="bandwidth",
                        )
                    )
                if link.jitter is not None and link.delay is None:
                    issues.append(
                        _issue(
                            "resource.link-jitter",
                            "link jitter requires a base delay for tc netem",
                            index=index,
                            field="jitter",
                        )
                    )

        return tuple(issues)
