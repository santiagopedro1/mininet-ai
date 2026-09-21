"""Compile-time substrate interface.

Runtime methods will extend this protocol in Phase 2. Phase 1 only needs an
explicit compatibility catalog so invalid placements fail before deployment.
"""

from __future__ import annotations

from typing import Protocol

from mininet_ai.specification.models import AttachmentLayer, ResourceKind


class SubstrateDriver(Protocol):
    name: str

    def validate_attachment(
        self,
        *,
        layer: AttachmentLayer,
        custom_layer: str | None,
        target_kind: ResourceKind,
        runtime: str,
    ) -> str | None:
        """Return an incompatibility message, or ``None`` when supported."""

    def validate_observation(self, *, layer: AttachmentLayer, name: str) -> str | None:
        """Return an incompatibility message, or ``None`` when supported."""
