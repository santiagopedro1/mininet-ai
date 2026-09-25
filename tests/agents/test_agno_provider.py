from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from agno.agent import Agent

from mininet_ai.agents import (
    AgnoAgentFactory,
    AgnoAgentProvider,
    create_agno_database,
)
from mininet_ai.agents.agno import DeterministicAgnoModel
from mininet_ai.compiler import compile_experiment
from mininet_ai.sdk import (
    AgentContext,
    AgentProvider,
    AgentProviderError,
    AgentResponse,
    ExecutionCatalog,
)
from mininet_ai.specification.models import (
    ConversationMemoryConfiguration,
    Implementation,
    LearnedMemoryConfiguration,
    LocalMemoryConfiguration,
    MemoryConfiguration,
)
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

    def memory_definition(self, memory: MemoryConfiguration):
        return replace(
            self.definition,
            blueprint=self.definition.blueprint.model_copy(
                update={"memory": memory}
            ),
        )

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

    def test_run_normalizes_agno_identity_and_detailed_metrics(self) -> None:
        model = DeterministicAgnoModel(
            {"message": "measured"},
            usage={
                "inputTokens": 11,
                "outputTokens": 4,
                "totalTokens": 15,
                "audioInputTokens": 2,
                "audioOutputTokens": 1,
                "audioTotalTokens": 3,
                "cacheReadTokens": 5,
                "cacheWriteTokens": 2,
                "reasoningTokens": 3,
                "cost": 0.012,
            },
        )
        provider = AgnoAgentProvider(
            self.definition,
            factory=AgnoAgentFactory(model_resolver=lambda configuration: model),
        )

        execution = provider.run(self.context)

        self.assertEqual(execution.agno_run_id, "invoke-1")
        self.assertEqual(execution.session_id, "run-1:switch-router@s1")
        self.assertIsNone(execution.user_id)
        self.assertRegex(execution.runtime_version, r"^3\.0\.")
        self.assertEqual(execution.model, "deterministic")
        self.assertEqual(execution.model_provider, "MininetAI")
        self.assertEqual(execution.response.message, "measured")
        self.assertEqual(execution.metrics.total_tokens, 15)
        self.assertEqual(execution.metrics.audio_total_tokens, 3)
        self.assertEqual(execution.metrics.cache_read_tokens, 5)
        self.assertEqual(execution.metrics.cache_write_tokens, 2)
        self.assertEqual(execution.metrics.reasoning_tokens, 3)
        self.assertEqual(execution.metrics.cost, 0.012)
        self.assertEqual(len(execution.metrics.models), 1)
        self.assertEqual(execution.metrics.models[0].role, "model")
        self.assertEqual(execution.metrics.models[0].model, "deterministic")

    def test_conversation_session_is_persisted_with_stable_identity(self) -> None:
        definition = self.memory_definition(
            MemoryConfiguration(
                conversation=ConversationMemoryConfiguration(maxMessages=7)
            )
        )
        with TemporaryDirectory() as temporary:
            database = create_agno_database(Path(temporary) / "agno.sqlite3")
            provider = AgnoAgentProvider(
                definition,
                factory=AgnoAgentFactory(
                    model_resolver=lambda configuration: StaticModel(),
                    db=database,
                ),
            )

            execution = provider.run(self.context)

            session_id = "run-1:switch-router@s1"
            self.assertEqual(execution.session_id, session_id)
            self.assertIsNotNone(database.get_session(session_id))
            self.assertTrue(execution.memory.conversation_history)
            self.assertEqual(execution.memory.history_messages, 7)

    def test_learned_memory_scope_controls_agno_user_identity(self) -> None:
        definition = self.memory_definition(
            MemoryConfiguration(
                learned=LearnedMemoryConfiguration(
                    scope="agent",
                    mode="agentic",
                )
            )
        )
        with TemporaryDirectory() as temporary:
            provider = AgnoAgentProvider(
                definition,
                factory=AgnoAgentFactory(
                    model_resolver=lambda configuration: StaticModel(),
                    db=create_agno_database(Path(temporary) / "agno.sqlite3"),
                ),
            )

            execution = provider.run(self.context)

        self.assertEqual(execution.user_id, "switch-router@s1")
        self.assertTrue(execution.memory.learned_memory)
        self.assertEqual(execution.memory.learned_scope, "agent")
        self.assertEqual(execution.memory.learned_mode, "agentic")

    def test_declared_memory_requires_a_session_database(self) -> None:
        definition = self.memory_definition(
            MemoryConfiguration(local=LocalMemoryConfiguration())
        )

        with self.assertRaises(AgentProviderError) as caught:
            AgnoAgentProvider(definition)

        self.assertEqual(caught.exception.code, "agent.agno.database-required")

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
