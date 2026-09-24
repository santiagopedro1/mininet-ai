"""Versioned compile-time contract for substrate drivers.

Phase 1 drivers describe compatibility and validate compiled resources. Phase 2
can add deployment and teardown protocols without changing this planning
contract, which both the fake and Mininet/OVS drivers must satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Protocol, Sequence, runtime_checkable

from mininet_ai.specification.models import AttachmentLayer, ResourceKind

if TYPE_CHECKING:
    from mininet_ai.compiler.models import PlannedResource


SUBSTRATE_CONTRACT_VERSION = "mininet-ai/substrate/v1alpha1"


@dataclass(frozen=True)
class SubstrateIssue:
    """A deterministic, user-facing substrate compatibility failure."""

    code: str
    message: str
    path: str | None = None

    def format(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


@dataclass(frozen=True)
class LayerSupport:
    """Targets, runtimes, and observations available at one attachment layer."""

    targets: frozenset[ResourceKind]
    runtimes: frozenset[str]
    observations: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SubstrateManifest:
    """Static compatibility declaration consumed by the experiment compiler."""

    name: str
    resource_kinds: frozenset[ResourceKind]
    layers: Mapping[AttachmentLayer, LayerSupport]
    custom_layers: Mapping[str, LayerSupport] = field(default_factory=dict)
    contract_version: str = SUBSTRATE_CONTRACT_VERSION


@runtime_checkable
class SubstrateDriver(Protocol):
    """Required Phase 1 interface for every substrate implementation."""

    @property
    def name(self) -> str: ...

    manifest: SubstrateManifest

    def validate_options(self) -> tuple[SubstrateIssue, ...]:
        """Validate driver-specific options supplied by the experiment."""
        ...

    def validate_resources(
        self, resources: Sequence[PlannedResource]
    ) -> tuple[SubstrateIssue, ...]:
        """Validate the concrete resource graph against substrate support."""
        ...

    def validate_attachment(
        self,
        *,
        layer: AttachmentLayer,
        custom_layer: str | None,
        target_kind: ResourceKind,
        runtime: str,
    ) -> tuple[SubstrateIssue, ...]:
        """Validate one logical agent attachment."""
        ...

    def validate_observation(
        self, *, layer: AttachmentLayer, custom_layer: str | None, name: str
    ) -> tuple[SubstrateIssue, ...]:
        """Validate one observation binding."""
        ...


class ManifestSubstrateDriver:
    """Reusable manifest-backed implementation of the common validations."""

    manifest: SubstrateManifest

    @property
    def name(self) -> str:
        return self.manifest.name

    def validate_options(self) -> tuple[SubstrateIssue, ...]:
        return ()

    def validate_resources(
        self, resources: Sequence[PlannedResource]
    ) -> tuple[SubstrateIssue, ...]:
        issues = []
        for index, resource in enumerate(resources):
            if resource.kind not in self.manifest.resource_kinds:
                issues.append(
                    SubstrateIssue(
                        code="substrate.resource.unsupported",
                        message=(
                            f"resource kind {resource.kind.value!r} is not supported "
                            f"by substrate {self.name!r}"
                        ),
                        path=f"resources.{index}",
                    )
                )
        return tuple(issues)

    def _layer_support(
        self, layer: AttachmentLayer, custom_layer: str | None
    ) -> tuple[LayerSupport | None, tuple[SubstrateIssue, ...]]:
        if layer == AttachmentLayer.CUSTOM:
            support = self.manifest.custom_layers.get(custom_layer or "")
            if support is None:
                return None, (
                    SubstrateIssue(
                        code="substrate.layer.unknown",
                        message=(
                            f"custom layer {custom_layer!r} is not registered by "
                            f"substrate {self.name!r}"
                        ),
                    ),
                )
            return support, ()

        support = self.manifest.layers.get(layer)
        if support is None:
            return None, (
                SubstrateIssue(
                    code="substrate.layer.unsupported",
                    message=(
                        f"layer {layer.value!r} is not supported by substrate "
                        f"{self.name!r}"
                    ),
                ),
            )
        return support, ()

    def validate_attachment(
        self,
        *,
        layer: AttachmentLayer,
        custom_layer: str | None,
        target_kind: ResourceKind,
        runtime: str,
    ) -> tuple[SubstrateIssue, ...]:
        support, issues = self._layer_support(layer, custom_layer)
        if support is None:
            return issues

        result = []
        layer_name = custom_layer if layer == AttachmentLayer.CUSTOM else layer.value
        if target_kind not in support.targets:
            result.append(
                SubstrateIssue(
                    code="substrate.attachment.target",
                    message=(
                        f"layer {layer_name!r} cannot target "
                        f"{target_kind.value!r} resources"
                    ),
                )
            )
        if runtime not in support.runtimes:
            result.append(
                SubstrateIssue(
                    code="substrate.attachment.runtime",
                    message=(
                        f"layer {layer_name!r} does not support runtime {runtime!r}"
                    ),
                )
            )
        return tuple(result)

    def validate_observation(
        self, *, layer: AttachmentLayer, custom_layer: str | None, name: str
    ) -> tuple[SubstrateIssue, ...]:
        support, issues = self._layer_support(layer, custom_layer)
        if support is None:
            return issues
        if name in support.observations:
            return ()
        layer_name = custom_layer if layer == AttachmentLayer.CUSTOM else layer.value
        return (
            SubstrateIssue(
                code="substrate.observation.unsupported",
                message=f"observation {name!r} is not available at layer {layer_name!r}",
            ),
        )
