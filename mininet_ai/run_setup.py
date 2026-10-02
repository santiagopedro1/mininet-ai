"""Composition-only identity reservation for the built-in adapters."""

from uuid import uuid4

from mininet_ai.substrates import (
    FakeSubstrateRuntime,
    MininetOVSRuntime,
    SubstrateRuntime,
)


def reserve_run(substrate: str) -> tuple[str, SubstrateRuntime]:
    if substrate == "fake":
        run_id = f"run-{uuid4()}"
        return run_id, FakeSubstrateRuntime(run_id_factory=lambda: run_id)
    if substrate == "mininet-ovs":
        run_id = f"mn-{uuid4()}"
        return run_id, MininetOVSRuntime(run_id_factory=lambda: run_id)
    raise ValueError(
        f"artifact identity reservation is not supported for {substrate!r}"
    )
