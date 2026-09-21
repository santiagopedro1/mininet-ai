"""A no-root substrate used to compile and test experiment designs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mininet_ai.specification.models import AttachmentLayer, ResourceKind


_TARGETS: dict[AttachmentLayer, frozenset[ResourceKind]] = {
    AttachmentLayer.GLOBAL: frozenset({ResourceKind.NETWORK}),
    AttachmentLayer.MANAGEMENT: frozenset(
        {ResourceKind.NETWORK, ResourceKind.REGION, ResourceKind.CONTROLLER_DOMAIN}
    ),
    AttachmentLayer.CONTROL: frozenset(
        {ResourceKind.CONTROLLER_DOMAIN, ResourceKind.SWITCH}
    ),
    AttachmentLayer.DATA: frozenset(
        {ResourceKind.SWITCH, ResourceKind.PORT, ResourceKind.LINK, ResourceKind.FLOW}
    ),
    AttachmentLayer.HOST: frozenset({ResourceKind.HOST}),
    AttachmentLayer.OBSERVER: frozenset(ResourceKind),
}

_RUNTIMES: dict[AttachmentLayer, frozenset[str]] = {
    AttachmentLayer.GLOBAL: frozenset({"orchestrator", "process", "container"}),
    AttachmentLayer.MANAGEMENT: frozenset({"orchestrator", "process", "container"}),
    AttachmentLayer.CONTROL: frozenset(
        {"controller-sidecar", "process", "container"}
    ),
    AttachmentLayer.DATA: frozenset({"device-sidecar", "process", "container"}),
    AttachmentLayer.HOST: frozenset({"host-namespace", "process", "container"}),
    AttachmentLayer.OBSERVER: frozenset({"orchestrator", "process", "container"}),
}

_OBSERVATIONS: dict[AttachmentLayer, frozenset[str]] = {
    AttachmentLayer.GLOBAL: frozenset({"topology.resources", "topology.neighbors"}),
    AttachmentLayer.MANAGEMENT: frozenset(
        {"topology.resources", "topology.neighbors", "controller.events"}
    ),
    AttachmentLayer.CONTROL: frozenset(
        {"topology.resources", "topology.neighbors", "controller.events", "openflow.flows"}
    ),
    AttachmentLayer.DATA: frozenset(
        {
            "topology.neighbors",
            "openflow.flows",
            "ovs.port-counters",
            "tc.queue-occupancy",
            "packets.samples",
        }
    ),
    AttachmentLayer.HOST: frozenset(
        {"topology.neighbors", "host.interfaces", "host.processes"}
    ),
}
_OBSERVATIONS[AttachmentLayer.OBSERVER] = frozenset().union(*_OBSERVATIONS.values())


class FakeSubstrateDriver:
    """Compile-time model of a substrate; it never creates a network."""

    name = "fake"

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options = dict(options or {})

    def validate_attachment(
        self,
        *,
        layer: AttachmentLayer,
        custom_layer: str | None,
        target_kind: ResourceKind,
        runtime: str,
    ) -> str | None:
        if layer == AttachmentLayer.CUSTOM:
            extensions = self.options.get("custom-layers", [])
            extension = next(
                (item for item in extensions if item.get("name") == custom_layer), None
            )
            if not extension:
                return f"custom layer {custom_layer!r} is not registered by substrate fake"
            if target_kind.value not in extension.get("targets", []):
                return f"custom layer {custom_layer!r} cannot target {target_kind.value}"
            if runtime not in extension.get("runtimes", []):
                return f"custom layer {custom_layer!r} does not support runtime {runtime!r}"
            return None

        if target_kind not in _TARGETS[layer]:
            return f"layer {layer.value!r} cannot target {target_kind.value!r} resources"
        if runtime not in _RUNTIMES[layer]:
            return f"layer {layer.value!r} does not support runtime {runtime!r}"
        return None

    def validate_observation(self, *, layer: AttachmentLayer, name: str) -> str | None:
        if layer == AttachmentLayer.CUSTOM:
            return None
        if name not in _OBSERVATIONS[layer]:
            return f"observation {name!r} is not available at layer {layer.value!r}"
        return None
