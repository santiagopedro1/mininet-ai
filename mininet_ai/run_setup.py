"""Composition-only identity reservation for the built-in adapters."""

from uuid import uuid4

from mininet_ai.substrates import (
    FakeSubstrateRuntime,
    MininetOVSRuntime,
    SubstrateRuntime,
    create_substrate_runtime,
)


def reserve_run(substrate: str) -> tuple[str | None, SubstrateRuntime]:
    if substrate == "fake":
        run_id = f"run-{uuid4()}"
        return run_id, FakeSubstrateRuntime(run_id_factory=lambda: run_id)
    if substrate == "mininet-ovs":
        run_id = f"mn-{uuid4()}"
        return run_id, MininetOVSRuntime(run_id_factory=lambda: run_id)
    # Third-party zero-argument factories retain their existing contract.
    return None, create_substrate_runtime(substrate)
