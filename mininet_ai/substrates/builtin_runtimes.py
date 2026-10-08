"""Single composition table for built-in construction and identity reservation."""

from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from mininet_ai.substrates.fake_runtime import FakeSubstrateRuntime
from mininet_ai.substrates.mininet_ovs import MininetOVSRuntime
from mininet_ai.substrates.runtime import SubstrateRuntime
from mininet_ai.substrates.runtime_registry import RuntimeRegistry


class _BuiltinRuntimeFactory(Protocol):
    def __call__(
        self, *, run_id_factory: Callable[[], str] = ...
    ) -> SubstrateRuntime: ...


def _register_builtin(
    registry: RuntimeRegistry, name: str, prefix: str, factory: _BuiltinRuntimeFactory
) -> None:
    def reserve() -> tuple[str, SubstrateRuntime]:
        run_id = f"{prefix}{uuid4()}"
        return run_id, factory(run_id_factory=lambda: run_id)

    registry.register(name, factory, reserve=reserve)


def register_builtin_runtimes(registry: RuntimeRegistry) -> None:
    """Keep zero-argument construction and reserved construction in one registration."""
    definitions: tuple[tuple[str, str, _BuiltinRuntimeFactory], ...] = (
        ("fake", "run-", FakeSubstrateRuntime),
        ("mininet-ovs", "mn-", MininetOVSRuntime),
    )
    for name, prefix, factory in definitions:
        _register_builtin(registry, name, prefix, factory)
