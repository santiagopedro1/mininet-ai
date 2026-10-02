"""Composition-only identity reservation for the built-in adapters."""

from mininet_ai.substrates import SubstrateRuntime, runtime_registry


def reserve_run(substrate: str) -> tuple[str | None, SubstrateRuntime]:
    """Reserve through the same registrations and validation as ordinary construction."""
    return runtime_registry.reserve(substrate)
