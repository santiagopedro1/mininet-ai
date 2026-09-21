"""Substrate contracts, built-in drivers, and the public driver registry."""

from collections.abc import Mapping
from typing import Any

from mininet_ai.substrates.fake import FakeSubstrateDriver
from mininet_ai.substrates.protocol import (
    SUBSTRATE_CONTRACT_VERSION,
    LayerSupport,
    ManifestSubstrateDriver,
    SubstrateDriver,
    SubstrateIssue,
    SubstrateManifest,
)
from mininet_ai.substrates.registry import SubstrateFactory, SubstrateRegistry


substrate_registry = SubstrateRegistry()
substrate_registry.register("fake", FakeSubstrateDriver)


def register_substrate_driver(name: str, factory: SubstrateFactory) -> None:
    """Register a driver factory for compiler lookup."""

    substrate_registry.register(name, factory)


def create_substrate_driver(
    name: str, options: Mapping[str, Any] | None = None
) -> SubstrateDriver:
    """Construct and contract-check a registered substrate driver."""

    return substrate_registry.create(name, options)


__all__ = [
    "SUBSTRATE_CONTRACT_VERSION",
    "FakeSubstrateDriver",
    "LayerSupport",
    "ManifestSubstrateDriver",
    "SubstrateDriver",
    "SubstrateFactory",
    "SubstrateIssue",
    "SubstrateManifest",
    "SubstrateRegistry",
    "create_substrate_driver",
    "register_substrate_driver",
    "substrate_registry",
]
