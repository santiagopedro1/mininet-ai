"""Registry for executable substrate runtime adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mininet_ai.substrates.runtime import (
    RUNTIME_CONTRACT_VERSION,
    SubstrateRuntime,
)

RuntimeFactory = Callable[[], SubstrateRuntime]
RuntimeReservationFactory = Callable[[], tuple[str, SubstrateRuntime]]


@dataclass(frozen=True)
class _RuntimeRegistration:
    factory: RuntimeFactory
    reserve: RuntimeReservationFactory | None = None


class RuntimeRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, _RuntimeRegistration] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def register(
        self,
        name: str,
        factory: RuntimeFactory,
        *,
        reserve: RuntimeReservationFactory | None = None,
    ) -> None:
        if not name:
            raise ValueError("substrate runtime name cannot be empty")
        if name in self._registrations:
            raise ValueError(f"substrate runtime {name!r} is already registered")
        self._registrations[name] = _RuntimeRegistration(factory, reserve)

    def create(self, name: str) -> SubstrateRuntime:
        runtime = self._registration(name).factory()
        return self._validate(name, runtime)

    def reserve(self, name: str) -> tuple[str | None, SubstrateRuntime]:
        """Use composition-layer reservation when provided, otherwise a plain factory."""
        registration = self._registration(name)
        if registration.reserve is None:
            return None, self._validate(name, registration.factory())
        run_id, runtime = registration.reserve()
        return run_id, self._validate(name, runtime)

    def _registration(self, name: str) -> _RuntimeRegistration:
        try:
            return self._registrations[name]
        except KeyError as error:
            available = ", ".join(self.names) or "none"
            raise LookupError(
                f"unknown substrate runtime {name!r}; available: {available}"
            ) from error

    @staticmethod
    def _validate(name: str, runtime: SubstrateRuntime) -> SubstrateRuntime:
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
