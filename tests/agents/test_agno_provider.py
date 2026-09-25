from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime

from agno.agent import Agent

from mininet_ai.agents import AgnoAgentFactory, AgnoAgentProvider
from mininet_ai.compiler import compile_experiment
from mininet_ai.sdk import (
    AgentContext,
    AgentProvider,
    AgentProviderError,
    AgentResponse,
    ExecutionCatalog,
)
from mininet_ai.specification.models import Implementation
from tests.agents.agno_helpers import StaticModel
from tests.compiler.helpers import EXAMPLE


NOW = datetime(2026, 1, 1, tzinfo=UTC)


class AgnoAgentProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        plan = compile_experiment(EXAMPLE)
        cls.definition = ExecutionCatalog(plan).resolve("switch-router@s1")
        attachment = cls.definition.instance.attachment
        cls.context = AgentContext(
            invocationId="invoke-1",
            runId="run-1",
            agentId=cls.definition.instance.id,
            deployment=cls.definition.instance.deployment,
            layer=attachment.layer,
            customLayer=attachment.custom_layer,
            targetKind=attachment.target_kind,
            targets=attachment.targets,
            capabilities=cls.definition.instance.capabilities,
            observations={"topology.neighbors": {"s1": ["s2"]}},
            intent="Inspect forwarding and propose a safe repair",
            priority=cls.definition.instance.priority,
            invokedAt=NOW,
        )

    def python_definition(self, entrypoint: str):
        blueprint = self.definition.blueprint.model_copy(
            update={
                "implementation": Implementation(
                    type="python",
                    entrypoint=entrypoint,
                ),
                "model": None,
            }
        )
        return replace(self.definition, blueprint=blueprint)

    def test_declarative_blueprint_runs_as_native_agno_agent(self) -> None:
        model = StaticModel(
            '{"message":"inspect first","proposals":[{"id":"proposal-1",'
            '"capability":"openflow.flow.install","target":"s1",'
            '"arguments":{"match":"ip","actions":"normal"}}]}'
        )
        factory = AgnoAgentFactory(model_resolver=lambda configuration: model)
        provider = AgnoAgentProvider(self.definition, factory=factory)

        response = provider.invoke(self.context)

        self.assertIsInstance(provider, AgentProvider)
        self.assertEqual(response.message, "inspect first")
        self.assertEqual(response.proposals[0].target, "s1")
        self.assertEqual(model.calls, 1)

    def test_context_is_checked_before_agno_runs(self) -> None:
        model = StaticModel()
        provider = AgnoAgentProvider(
            self.definition,
            factory=AgnoAgentFactory(
                model_resolver=lambda configuration: model
            ),
        )
        invalid = self.context.model_copy(update={"targets": ("s2",)})

        with self.assertRaises(AgentProviderError) as caught:
            provider.invoke(invalid)

        self.assertEqual(caught.exception.code, "agent.context.invalid")
        self.assertEqual(model.calls, 0)

    def test_python_entrypoint_can_return_factory_or_agent(self) -> None:
        cases = (
            ("tests.agents.agno_helpers:agent_factory", "from Python Agno factory"),
            ("tests.agents.agno_helpers:direct_agent", "from direct Agno agent"),
        )
        for entrypoint, expected in cases:
            with self.subTest(entrypoint=entrypoint):
                provider = AgnoAgentProvider(self.python_definition(entrypoint))
                response = provider.invoke(self.context)
                self.assertEqual(response.message, expected)

    def test_python_entrypoint_must_produce_an_agno_agent(self) -> None:
        cases = (
            (
                "tests.agents.agno_helpers:invalid_factory",
                "agent.agno.factory-invalid",
            ),
            (
                "tests.agents.agno_helpers:not_an_agent",
                "agent.agno.entrypoint-invalid",
            ),
            ("tests.agents.missing:factory", "agent.agno.load-failed"),
            ("invalid", "agent.agno.entrypoint-invalid"),
        )
        for entrypoint, code in cases:
            with self.subTest(entrypoint=entrypoint):
                with self.assertRaises(AgentProviderError) as caught:
                    AgnoAgentProvider(self.python_definition(entrypoint))
                self.assertEqual(caught.exception.code, code)

    def test_default_factory_uses_agno_model_strings(self) -> None:
        agent = AgnoAgentFactory().create(self.definition)

        self.assertIsInstance(agent, Agent)
        self.assertEqual(agent.id, "switch-router@s1")
        assert agent.model is not None
        self.assertEqual(agent.model.id, "llama3.1:8b")
        self.assertEqual(agent.telemetry, False)
        self.assertIs(agent.output_schema, AgentResponse)

    def test_legacy_model_parameters_are_not_silently_ignored(self) -> None:
        model = self.definition.blueprint.model
        assert model is not None
        blueprint = self.definition.blueprint.model_copy(
            update={
                "model": model.model_copy(
                    update={"parameters": {"temperature": 0.2}}
                )
            }
        )
        definition = replace(self.definition, blueprint=blueprint)

        with self.assertRaises(AgentProviderError) as caught:
            AgnoAgentFactory().create(definition)

        self.assertEqual(
            caught.exception.code,
            "agent.agno.model-parameters-unsupported",
        )


if __name__ == "__main__":
    unittest.main()
