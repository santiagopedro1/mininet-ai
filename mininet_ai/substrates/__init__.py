"""Substrate contracts, built-in drivers, and the public driver registry."""

from collections.abc import Mapping
from typing import Any

from mininet_ai.substrates.fake import FakeSubstrateDriver
from mininet_ai.substrates.fake_runtime import FakeSubstrateRuntime
from mininet_ai.substrates.mininet_ovs import MininetOVSDriver, MininetOVSRuntime
from mininet_ai.substrates.protocol import (
    SUBSTRATE_CONTRACT_VERSION,
    LayerSupport,
    ManifestSubstrateDriver,
    SubstrateDriver,
    SubstrateIssue,
    SubstrateManifest,
)
from mininet_ai.substrates.registry import SubstrateFactory, SubstrateRegistry
from mininet_ai.substrates.runtime import (
    RUNTIME_CONTRACT_VERSION,
    ActionRequest,
    ActionResult,
    ActionStatus,
    ExternallyStoppableRuntime,
    LiveResource,
    ObservationQuery,
    ObservationResult,
    ResourceOperationalState,
    RunInfo,
    RunState,
    RuntimeIssue,
    RuntimeSnapshot,
    SubstrateRuntime,
    TeardownResult,
)
from mininet_ai.substrates.runtime_registry import RuntimeFactory, RuntimeRegistry


substrate_registry = SubstrateRegistry()
substrate_registry.register("fake", FakeSubstrateDriver)
substrate_registry.register("mininet-ovs", MininetOVSDriver)
runtime_registry = RuntimeRegistry()
runtime_registry.register("fake", FakeSubstrateRuntime)
runtime_registry.register("mininet-ovs", MininetOVSRuntime)


def register_substrate_driver(name: str, factory: SubstrateFactory) -> None:
    """Register a driver factory for compiler lookup."""

    substrate_registry.register(name, factory)


def create_substrate_driver(
    name: str, options: Mapping[str, Any] | None = None
) -> SubstrateDriver:
    """Construct and contract-check a registered substrate driver."""

    return substrate_registry.create(name, options)


def register_substrate_runtime(name: str, factory: RuntimeFactory) -> None:
    """Register an executable runtime adapter."""

    runtime_registry.register(name, factory)


def create_substrate_runtime(name: str) -> SubstrateRuntime:
    """Construct and contract-check a registered runtime adapter."""

    return runtime_registry.create(name)


__all__ = [
    "SUBSTRATE_CONTRACT_VERSION",
    "RUNTIME_CONTRACT_VERSION",
    "ActionRequest",
    "ActionResult",
    "ActionStatus",
    "FakeSubstrateDriver",
    "FakeSubstrateRuntime",
    "ExternallyStoppableRuntime",
    "LayerSupport",
    "LiveResource",
    "ManifestSubstrateDriver",
    "MininetOVSDriver",
    "MininetOVSRuntime",
    "ObservationQuery",
    "ObservationResult",
    "ResourceOperationalState",
    "RunInfo",
    "RunState",
    "RuntimeFactory",
    "RuntimeIssue",
    "RuntimeRegistry",
    "RuntimeSnapshot",
    "SubstrateDriver",
    "SubstrateFactory",
    "SubstrateIssue",
    "SubstrateManifest",
    "SubstrateRuntime",
    "TeardownResult",
    "SubstrateRegistry",
    "create_substrate_driver",
    "create_substrate_runtime",
    "register_substrate_runtime",
    "register_substrate_driver",
    "runtime_registry",
    "substrate_registry",
]
