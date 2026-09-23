"""A no-root substrate used to compile and test experiment designs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from mininet_ai.specification.models import (
    NAME_PATTERN,
    AttachmentLayer,
    ResourceKind,
)
from mininet_ai.substrates.protocol import (
    LayerSupport,
    ManifestSubstrateDriver,
    SubstrateIssue,
    SubstrateManifest,
)


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
            runtimes=frozenset({"controller-sidecar", "process", "container"}),
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
            runtimes=frozenset({"host-namespace", "process", "container"}),
            observations=_OBSERVATIONS[AttachmentLayer.HOST],
        ),
        AttachmentLayer.OBSERVER: LayerSupport(
            targets=frozenset(ResourceKind),
            runtimes=frozenset({"orchestrator", "process", "container"}),
            observations=_OBSERVATIONS[AttachmentLayer.OBSERVER],
        ),
    }
)


def _string_set(
    value: object,
    *,
    path: str,
    field_name: str,
    issues: list[SubstrateIssue],
    allow_empty: bool = False,
) -> frozenset[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "a list" if allow_empty else "a non-empty list"
        issues.append(
            SubstrateIssue(
                code="fake.options.invalid",
                message=f"{field_name} must be {qualifier} of strings",
                path=path,
            )
        )
        return frozenset()
    if any(not isinstance(item, str) or not item for item in value):
        issues.append(
            SubstrateIssue(
                code="fake.options.invalid",
                message=f"{field_name} must contain only non-empty strings",
                path=path,
            )
        )
        return frozenset()
    return frozenset(value)


def _parse_custom_layers(
    options: Mapping[str, Any],
) -> tuple[Mapping[str, LayerSupport], tuple[SubstrateIssue, ...]]:
    issues: list[SubstrateIssue] = []
    unknown = set(options) - {"custom-layers"}
    for name in sorted(unknown):
        issues.append(
            SubstrateIssue(
                code="fake.options.unknown",
                message=f"unknown fake substrate option {name!r}",
                path=f"options.{name}",
            )
        )

    raw_layers = options.get("custom-layers", [])
    if not isinstance(raw_layers, list):
        return MappingProxyType({}), tuple(
            issues
            + [
                SubstrateIssue(
                    code="fake.options.invalid",
                    message="custom-layers must be a list",
                    path="options.custom-layers",
                )
            ]
        )

    custom_layers: dict[str, LayerSupport] = {}
    for index, value in enumerate(raw_layers):
        path = f"options.custom-layers.{index}"
        if not isinstance(value, Mapping):
            issues.append(
                SubstrateIssue(
                    code="fake.options.invalid",
                    message="custom layer must be an object",
                    path=path,
                )
            )
            continue
        name = value.get("name")
        if not isinstance(name, str) or not re.fullmatch(NAME_PATTERN, name):
            issues.append(
                SubstrateIssue(
                    code="fake.options.invalid",
                    message="custom layer requires a valid name",
                    path=f"{path}.name",
                )
            )
            continue
        if name in custom_layers:
            issues.append(
                SubstrateIssue(
                    code="fake.options.duplicate-layer",
                    message=f"custom layer {name!r} is declared more than once",
                    path=f"{path}.name",
                )
            )
            continue

        unknown_fields = set(value) - {
            "name",
            "targets",
            "runtimes",
            "observations",
        }
        for field_name in sorted(unknown_fields):
            issues.append(
                SubstrateIssue(
                    code="fake.options.unknown",
                    message=f"unknown custom-layer field {field_name!r}",
                    path=f"{path}.{field_name}",
                )
            )

        raw_targets = _string_set(
            value.get("targets"),
            path=f"{path}.targets",
            field_name="targets",
            issues=issues,
        )
        targets: set[ResourceKind] = set()
        for target in sorted(raw_targets):
            try:
                targets.add(ResourceKind(target))
            except ValueError:
                issues.append(
                    SubstrateIssue(
                        code="fake.options.unknown-resource-kind",
                        message=f"unknown resource kind {target!r}",
                        path=f"{path}.targets",
                    )
                )
        runtimes = _string_set(
            value.get("runtimes"),
            path=f"{path}.runtimes",
            field_name="runtimes",
            issues=issues,
        )
        observations = _string_set(
            value.get("observations", []),
            path=f"{path}.observations",
            field_name="observations",
            issues=issues,
            allow_empty=True,
        )
        custom_layers[name] = LayerSupport(
            targets=frozenset(targets),
            runtimes=runtimes,
            observations=observations,
        )
    return MappingProxyType(custom_layers), tuple(issues)


class FakeSubstrateDriver(ManifestSubstrateDriver):
    """Manifest-backed compile-time substrate; it never creates a network."""

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options = dict(options or {})
        custom_layers, self._option_issues = _parse_custom_layers(self.options)
        self.manifest = SubstrateManifest(
            name="fake",
            resource_kinds=frozenset(ResourceKind),
            layers=_LAYERS,
            custom_layers=custom_layers,
        )

    def validate_options(self) -> tuple[SubstrateIssue, ...]:
        return self._option_issues
