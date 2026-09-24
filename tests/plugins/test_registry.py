from __future__ import annotations

import unittest
from typing import Any, cast

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.plugins import (
    ProviderKind,
    ProviderPlugin,
    ProviderRegistries,
)
from mininet_ai.sdk import AGENT_RUNTIME_CONTRACT_VERSION, ExecutionCatalog
from mininet_ai.specification.models import ModelConfiguration
from tests.compiler.helpers import EXAMPLE
from tests.plugins.helpers import plugin


class ProviderRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = compile_experiment(EXAMPLE)
        catalog = ExecutionCatalog(self.plan)
        self.agent_definition = catalog.resolve("switch-router@s1")
        self.capability_definition = self.agent_definition.capabilities[0]
        model_configuration = self.agent_definition.blueprint.model
        self.assertIsNotNone(model_configuration)
        assert model_configuration is not None
        self.model_configuration: ModelConfiguration = model_configuration
        self.registries = ProviderRegistries()

    def test_registers_and_constructs_each_provider_kind(self) -> None:
        self.registries.agents.register("python-agent", plugin(ProviderKind.AGENT))
        self.registries.capabilities.register(
            "custom-action",
            plugin(ProviderKind.CAPABILITY),
        )
        self.registries.models.register("mock", plugin(ProviderKind.MODEL))

        agent = self.registries.agents.create(
            "python-agent",
            self.agent_definition,
        )
        capability = self.registries.capabilities.create(
            "custom-action",
            self.capability_definition,
        )
        model = self.registries.models.create("mock", self.model_configuration)

        self.assertEqual(agent.contract_version, AGENT_RUNTIME_CONTRACT_VERSION)
        self.assertEqual(
            capability.contract_version,
            AGENT_RUNTIME_CONTRACT_VERSION,
        )
        self.assertEqual(model.contract_version, AGENT_RUNTIME_CONTRACT_VERSION)
        self.assertEqual(self.registries.agents.names, ("python-agent",))

    def test_unknown_and_duplicate_names_have_typed_errors(self) -> None:
        self.registries.models.register("z-model", plugin(ProviderKind.MODEL))
        self.registries.models.register("a-model", plugin(ProviderKind.MODEL))

        with self.assertRaises(AgentRuntimeError) as unknown:
            self.registries.models.create("missing", self.model_configuration)
        with self.assertRaises(AgentRuntimeError) as duplicate:
            self.registries.models.register("a-model", plugin(ProviderKind.MODEL))

        self.assertEqual(unknown.exception.code, "plugin.provider.unknown")
        self.assertIn("a-model, z-model", str(unknown.exception))
        self.assertEqual(duplicate.exception.code, "plugin.provider.duplicate")

    def test_rejects_invalid_names_kinds_versions_and_factories(self) -> None:
        cases = (
            (
                "invalid name",
                plugin(ProviderKind.MODEL),
                "plugin.name.invalid",
            ),
            (
                "wrong-kind",
                plugin(ProviderKind.AGENT),
                "plugin.kind.mismatch",
            ),
            (
                "old-model",
                ProviderPlugin(
                    kind=ProviderKind.MODEL,
                    factory=cast(Any, lambda configuration: object()),
                    contract_version="mininet-ai/agent-runtime/v0",
                ),
                "plugin.version.unsupported",
            ),
            (
                "invalid-kind",
                ProviderPlugin(
                    kind=cast(Any, "not-a-kind"),
                    factory=cast(Any, lambda configuration: object()),
                ),
                "plugin.descriptor.invalid",
            ),
            (
                "not-callable",
                ProviderPlugin(
                    kind=ProviderKind.MODEL,
                    factory=cast(Any, None),
                ),
                "plugin.factory.invalid",
            ),
        )

        for name, descriptor, code in cases:
            with self.subTest(name=name):
                with self.assertRaises(AgentRuntimeError) as context:
                    self.registries.models.register(name, descriptor)
                self.assertEqual(context.exception.code, code)

    def test_factory_failures_and_invalid_results_are_typed(self) -> None:
        def fail(configuration):
            raise RuntimeError("scripted failure")

        class LegacyModel:
            contract_version = "mininet-ai/agent-runtime/v0"

            def generate(self, request):
                raise AssertionError("the legacy provider must not be used")

        cases = (
            (
                "broken",
                ProviderPlugin(kind=ProviderKind.MODEL, factory=fail),
                "plugin.factory.failed",
            ),
            (
                "invalid",
                ProviderPlugin(
                    kind=ProviderKind.MODEL,
                    factory=cast(Any, lambda configuration: object()),
                ),
                "plugin.provider.invalid",
            ),
            (
                "legacy",
                ProviderPlugin(
                    kind=ProviderKind.MODEL,
                    factory=lambda configuration: LegacyModel(),
                ),
                "plugin.version.unsupported",
            ),
        )

        for name, descriptor, code in cases:
            with self.subTest(name=name):
                self.registries.models.register(name, cast(Any, descriptor))
                with self.assertRaises(AgentRuntimeError) as context:
                    self.registries.models.create(name, self.model_configuration)
                self.assertEqual(context.exception.code, code)


if __name__ == "__main__":
    unittest.main()
