"""Registry for executable substrate runtime adapters."""

from __future__ import annotations

from collections.abc import Callable

from mininet_ai.substrates.runtime import (
    RUNTIME_CONTRACT_VERSION,
    SubstrateRuntime,
)


RuntimeFactory = Callable[[], SubstrateRuntime]


class RuntimeRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, RuntimeFactory] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def register(self, name: str, factory: RuntimeFactory) -> None:
        if not name:
            raise ValueError("substrate runtime name cannot be empty")
        if name in self._factories:
            raise ValueError(f"substrate runtime {name!r} is already registered")
        self._factories[name] = factory

    def create(self, name: str) -> SubstrateRuntime:
        try:
            factory = self._factories[name]
        except KeyError as error:
            available = ", ".join(self.names) or "none"
            raise LookupError(
                f"unknown substrate runtime {name!r}; available: {available}"
            ) from error

        runtime = factory()
        if not isinstance(runtime, SubstrateRuntime):
            raise TypeError(f"substrate runtime {name!r} does not satisfy the contract")
        if runtime.name != name:
            raise ValueError(
                f"substrate runtime registered as {name!r} reports name "
                f"{runtime.name!r}"
            )
        if runtime.contract_version != RUNTIME_CONTRACT_VERSION:
            raise ValueError(
                f"substrate runtime {name!r} uses unsupported contract version "
                f"{runtime.contract_version!r}"
            )
        return runtime
