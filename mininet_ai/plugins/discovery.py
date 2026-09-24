"""Explicit Python entry-point discovery for provider plugins."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata
from typing import Protocol

from mininet_ai.errors import AgentRuntimeError
from mininet_ai.plugins.registry import (
    ProviderKind,
    ProviderPlugin,
    ProviderRegistries,
)


ENTRY_POINT_GROUPS = {
    ProviderKind.AGENT: "mininet_ai.agents",
    ProviderKind.CAPABILITY: "mininet_ai.capabilities",
    ProviderKind.MODEL: "mininet_ai.models",
}


class EntryPoint(Protocol):
    name: str
    group: str

    def load(self) -> object:
        """Load the object referenced by this entry point."""
        ...


@dataclass(frozen=True)
class LoadedPlugin:
    kind: ProviderKind
    name: str
    group: str


def discover_plugins(
    registries: ProviderRegistries,
    *,
    entry_points: Iterable[EntryPoint] | None = None,
) -> tuple[LoadedPlugin, ...]:
    """Load installed provider descriptors transactionally and explicitly."""

    points = _entry_points(entry_points)
    staged = registries._clone()
    loaded = []
    for point in sorted(points, key=lambda item: (item.group, item.name)):
        kind = _kind_for_group(point.group)
        try:
            descriptor = point.load()
        except Exception as error:
            raise AgentRuntimeError(
                f"could not load plugin {point.name!r} from "
                f"{point.group!r}: {error}",
                code="plugin.load.failed",
            ) from error
        if not isinstance(descriptor, ProviderPlugin):
            raise AgentRuntimeError(
                f"plugin {point.name!r} from {point.group!r} did not export "
                "a ProviderPlugin descriptor",
                code="plugin.descriptor.invalid",
            )
        staged._for_kind(kind).register(point.name, descriptor)
        loaded.append(LoadedPlugin(kind=kind, name=point.name, group=point.group))

    registries._replace(staged)
    return tuple(loaded)


def _entry_points(
    supplied: Iterable[EntryPoint] | None,
) -> tuple[EntryPoint, ...]:
    groups = set(ENTRY_POINT_GROUPS.values())
    if supplied is not None:
        return tuple(point for point in supplied if point.group in groups)

    installed = metadata.entry_points()
    return tuple(
        point
        for group in sorted(groups)
        for point in installed.select(group=group)
    )


def _kind_for_group(group: str) -> ProviderKind:
    for kind, expected in ENTRY_POINT_GROUPS.items():
        if group == expected:
            return kind
    raise AgentRuntimeError(
        f"unsupported plugin entry-point group {group!r}",
        code="plugin.group.unsupported",
    )
