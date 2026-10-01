"""Provider registries and explicit plugin discovery."""

from mininet_ai.plugins.discovery import (
    ENTRY_POINT_GROUPS,
    LoadedPlugin,
    discover_plugins,
)
from mininet_ai.plugins.registry import (
    ProviderKind,
    ProviderPlugin,
    ProviderRegistries,
    ProviderRegistry,
)

__all__ = [
    "ENTRY_POINT_GROUPS",
    "LoadedPlugin",
    "ProviderKind",
    "ProviderPlugin",
    "ProviderRegistries",
    "ProviderRegistry",
    "discover_plugins",
]
