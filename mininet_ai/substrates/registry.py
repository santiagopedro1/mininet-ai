"""Registry and construction helpers for substrate drivers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mininet_ai.substrates.protocol import (
    SUBSTRATE_CONTRACT_VERSION,
    SubstrateDriver,
    SubstrateManifest,
)


SubstrateFactory = Callable[[Mapping[str, Any] | None], SubstrateDriver]


class SubstrateRegistry:
    """Explicit registry used by the compiler and future driver plugins."""

    def __init__(self) -> None:
        self._factories: dict[str, SubstrateFactory] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def register(self, name: str, factory: SubstrateFactory) -> None:
        if not name:
            raise ValueError("substrate driver name cannot be empty")
        if name in self._factories:
            raise ValueError(f"substrate driver {name!r} is already registered")
        self._factories[name] = factory

    def create(
        self, name: str, options: Mapping[str, Any] | None = None
    ) -> SubstrateDriver:
        try:
            factory = self._factories[name]
        except KeyError as error:
            available = ", ".join(self.names) or "none"
            raise LookupError(
                f"unknown substrate driver {name!r}; available: {available}"
            ) from error

        driver = factory(options)
        if not isinstance(driver, SubstrateDriver):
            raise TypeError(f"substrate driver {name!r} does not satisfy the contract")
        if not isinstance(driver.manifest, SubstrateManifest):
            raise TypeError(f"substrate driver {name!r} has an invalid manifest")
        if driver.name != name or driver.manifest.name != name:
            raise ValueError(
                f"substrate driver registered as {name!r} reports name "
                f"{driver.manifest.name!r}"
            )
        if driver.manifest.contract_version != SUBSTRATE_CONTRACT_VERSION:
            raise ValueError(
                f"substrate driver {name!r} uses unsupported contract version "
                f"{driver.manifest.contract_version!r}"
            )
        return driver
