"""Typed registries for Phase 3 provider adapters."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic, Protocol, TypeVar, cast

from mininet_ai.errors import AgentRuntimeError
from mininet_ai.sdk.catalog import AgentExecutionDefinition
from mininet_ai.sdk.contracts import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    AgentProvider,
    CapabilityProvider,
    ModelProvider,
)
from mininet_ai.specification.models import (
    NAME_PATTERN,
    CapabilityDefinition,
    ModelConfiguration,
)


ConfigurationT = TypeVar("ConfigurationT")


class _VersionedProvider(Protocol):
    contract_version: str


ProviderT = TypeVar("ProviderT", bound=_VersionedProvider)


class ProviderKind(StrEnum):
    AGENT = "agent"
    CAPABILITY = "capability"
    MODEL = "model"


@dataclass(frozen=True)
class ProviderPlugin(Generic[ConfigurationT, ProviderT]):
    """Versioned provider factory exported by a plugin entry point."""

    kind: ProviderKind
    factory: Callable[[ConfigurationT], ProviderT]
    contract_version: str = AGENT_RUNTIME_CONTRACT_VERSION


class ProviderRegistry(Generic[ConfigurationT, ProviderT]):
    """Register factories and construct contract-checked provider adapters."""

    def __init__(self, kind: ProviderKind, provider_type: type[Any]) -> None:
        self.kind = kind
        self._provider_type = provider_type
        self._plugins: dict[
            str,
            ProviderPlugin[ConfigurationT, ProviderT],
        ] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))

    def register(
        self,
        name: str,
        plugin: ProviderPlugin[ConfigurationT, ProviderT],
    ) -> None:
        self._validate(name, plugin)
        self._plugins[name] = plugin

    def create(self, name: str, configuration: ConfigurationT) -> ProviderT:
        try:
            plugin = self._plugins[name]
        except KeyError as error:
            available = ", ".join(self.names) or "none"
            raise AgentRuntimeError(
                f"unknown {self.kind.value} provider {name!r}; "
                f"available: {available}",
                code="plugin.provider.unknown",
            ) from error

        try:
            provider = plugin.factory(configuration)
        except Exception as error:
            raise AgentRuntimeError(
                f"{self.kind.value} provider factory {name!r} failed: {error}",
                code="plugin.factory.failed",
            ) from error
        if not isinstance(provider, self._provider_type):
            raise AgentRuntimeError(
                f"{self.kind.value} provider {name!r} does not satisfy its "
                "SDK protocol",
                code="plugin.provider.invalid",
            )
        checked = cast(ProviderT, provider)
        if checked.contract_version != AGENT_RUNTIME_CONTRACT_VERSION:
            raise AgentRuntimeError(
                f"{self.kind.value} provider {name!r} uses unsupported "
                f"contract version {checked.contract_version!r}",
                code="plugin.version.unsupported",
            )
        return checked

    def _validate(
        self,
        name: str,
        plugin: ProviderPlugin[ConfigurationT, ProviderT],
    ) -> None:
        if len(name) > 128 or re.fullmatch(NAME_PATTERN, name) is None:
            raise AgentRuntimeError(
                f"invalid {self.kind.value} provider name {name!r}",
                code="plugin.name.invalid",
            )
        if name in self._plugins:
            raise AgentRuntimeError(
                f"{self.kind.value} provider {name!r} is already registered",
                code="plugin.provider.duplicate",
            )
        if not isinstance(plugin, ProviderPlugin):
            raise AgentRuntimeError(
                f"{self.kind.value} provider {name!r} has an invalid descriptor",
                code="plugin.descriptor.invalid",
            )
        if not isinstance(plugin.kind, ProviderKind):
            raise AgentRuntimeError(
                f"{self.kind.value} provider {name!r} has an invalid kind",
                code="plugin.descriptor.invalid",
            )
        if plugin.kind != self.kind:
            raise AgentRuntimeError(
                f"provider {name!r} declares kind {plugin.kind.value!r}, "
                f"not {self.kind.value!r}",
                code="plugin.kind.mismatch",
            )
        if plugin.contract_version != AGENT_RUNTIME_CONTRACT_VERSION:
            raise AgentRuntimeError(
                f"{self.kind.value} provider {name!r} uses unsupported "
                f"contract version {plugin.contract_version!r}",
                code="plugin.version.unsupported",
            )
        if not callable(plugin.factory):
            raise AgentRuntimeError(
                f"{self.kind.value} provider {name!r} has a non-callable factory",
                code="plugin.factory.invalid",
            )

    def _clone(self) -> ProviderRegistry[ConfigurationT, ProviderT]:
        clone = ProviderRegistry(self.kind, self._provider_type)
        clone._plugins = dict(self._plugins)
        return clone

    def _replace(self, other: ProviderRegistry[ConfigurationT, ProviderT]) -> None:
        self._plugins = dict(other._plugins)


class ProviderRegistries:
    """The three provider registries installed into one agent runtime."""

    def __init__(self) -> None:
        self.agents = ProviderRegistry[AgentExecutionDefinition, AgentProvider](
            ProviderKind.AGENT,
            AgentProvider,
        )
        self.capabilities = ProviderRegistry[
            CapabilityDefinition,
            CapabilityProvider,
        ](
            ProviderKind.CAPABILITY,
            CapabilityProvider,
        )
        self.models = ProviderRegistry[ModelConfiguration, ModelProvider](
            ProviderKind.MODEL,
            ModelProvider,
        )

    def _for_kind(
        self,
        kind: ProviderKind,
    ) -> ProviderRegistry[Any, Any]:
        registries = {
            ProviderKind.AGENT: self.agents,
            ProviderKind.CAPABILITY: self.capabilities,
            ProviderKind.MODEL: self.models,
        }
        return cast(ProviderRegistry[Any, Any], registries[kind])

    def _clone(self) -> ProviderRegistries:
        clone = ProviderRegistries()
        clone.agents = self.agents._clone()
        clone.capabilities = self.capabilities._clone()
        clone.models = self.models._clone()
        return clone

    def _replace(self, other: ProviderRegistries) -> None:
        self.agents._replace(other.agents)
        self.capabilities._replace(other.capabilities)
        self.models._replace(other.models)
