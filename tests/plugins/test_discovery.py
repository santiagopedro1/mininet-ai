from __future__ import annotations

import unittest
from unittest.mock import patch

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.plugins import (
    ENTRY_POINT_GROUPS,
    ProviderKind,
    ProviderRegistries,
    discover_plugins,
)
from tests.compiler.helpers import EXAMPLE
from tests.plugins.helpers import FakeEntryPoint, plugin


class PluginDiscoveryTests(unittest.TestCase):
    def test_discovers_supported_groups_in_deterministic_order(self) -> None:
        points = (
            FakeEntryPoint(
                "z-model",
                ENTRY_POINT_GROUPS[ProviderKind.MODEL],
                plugin(ProviderKind.MODEL),
            ),
            FakeEntryPoint(
                "agent",
                ENTRY_POINT_GROUPS[ProviderKind.AGENT],
                plugin(ProviderKind.AGENT),
            ),
            FakeEntryPoint(
                "action",
                ENTRY_POINT_GROUPS[ProviderKind.CAPABILITY],
                plugin(ProviderKind.CAPABILITY),
            ),
            FakeEntryPoint(
                "ignored",
                "unrelated.group",
                object(),
            ),
        )
        registries = ProviderRegistries()
        original_agents = registries.agents
        original_capabilities = registries.capabilities
        original_models = registries.models

        loaded = discover_plugins(registries, entry_points=points)

        self.assertEqual(
            tuple((item.kind, item.name) for item in loaded),
            (
                (ProviderKind.AGENT, "agent"),
                (ProviderKind.CAPABILITY, "action"),
                (ProviderKind.MODEL, "z-model"),
            ),
        )
        self.assertEqual(registries.agents.names, ("agent",))
        self.assertEqual(registries.capabilities.names, ("action",))
        self.assertEqual(registries.models.names, ("z-model",))
        self.assertIs(registries.agents, original_agents)
        self.assertIs(registries.capabilities, original_capabilities)
        self.assertIs(registries.models, original_models)
        self.assertFalse(points[-1].loaded)

    def test_discovery_failure_does_not_partially_modify_registries(self) -> None:
        registries = ProviderRegistries()
        registries.models.register("existing", plugin(ProviderKind.MODEL))
        points = (
            FakeEntryPoint(
                "agent",
                ENTRY_POINT_GROUPS[ProviderKind.AGENT],
                plugin(ProviderKind.AGENT),
            ),
            FakeEntryPoint(
                "broken",
                ENTRY_POINT_GROUPS[ProviderKind.MODEL],
                object(),
                error=RuntimeError("cannot import dependency"),
            ),
        )

        with self.assertRaises(AgentRuntimeError) as context:
            discover_plugins(registries, entry_points=points)

        self.assertEqual(context.exception.code, "plugin.load.failed")
        self.assertEqual(registries.agents.names, ())
        self.assertEqual(registries.models.names, ("existing",))

    def test_invalid_descriptor_and_collision_are_typed_and_atomic(self) -> None:
        cases = (
            (
                FakeEntryPoint(
                    "bad",
                    ENTRY_POINT_GROUPS[ProviderKind.MODEL],
                    object(),
                ),
                "plugin.descriptor.invalid",
            ),
            (
                FakeEntryPoint(
                    "existing",
                    ENTRY_POINT_GROUPS[ProviderKind.MODEL],
                    plugin(ProviderKind.MODEL),
                ),
                "plugin.provider.duplicate",
            ),
        )

        for point, code in cases:
            with self.subTest(code=code):
                registries = ProviderRegistries()
                registries.models.register("existing", plugin(ProviderKind.MODEL))
                with self.assertRaises(AgentRuntimeError) as context:
                    discover_plugins(registries, entry_points=(point,))
                self.assertEqual(context.exception.code, code)
                self.assertEqual(registries.models.names, ("existing",))

    def test_compilation_does_not_trigger_plugin_discovery(self) -> None:
        with patch(
            "mininet_ai.plugins.discovery.metadata.entry_points"
        ) as entry_points:
            compile_experiment(EXAMPLE)

        entry_points.assert_not_called()


if __name__ == "__main__":
    unittest.main()
