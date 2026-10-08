from __future__ import annotations

import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from typing import Any, cast
from unittest.mock import patch

from agno.agent import Agent

from mininet_ai.agents import (
    AgnoAgentFactory,
    AgnoAgentProvider,
)
from mininet_ai.agents.agno import DeterministicAgnoModel
from mininet_ai.compiler import compile_experiment
from mininet_ai.sdk import (
    AgentContext,
    AgentCoordinationContext,
    AgentProvider,
    AgentProviderError,
    AgentResponse,
    ExecutionCatalog,
    SharedStateSnapshot,
)
from mininet_ai.specification.models import (
    ConversationMemoryConfiguration,
    Implementation,
    LearnedMemoryConfiguration,
    LocalMemoryConfiguration,
    MemoryConfiguration,
    ModelConfiguration,
    ReasoningConfiguration,
)
from mininet_ai.substrates import ActionResult, ActionStatus
from tests.agents.agno_helpers import SlowAsyncModel, StaticModel
from tests.specification_fixtures import COMPILER_MULTILAYER_SPECIFICATION

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class AgnoAgentProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        plan = compile_experiment(COMPILER_MULTILAYER_SPECIFICATION)
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

    def test_model_receives_scoped_capability_argument_schemas(self) -> None:
        model = StaticModel()
        provider = AgnoAgentProvider(
            self.definition,
            factory=AgnoAgentFactory(model_resolver=lambda configuration: model),
        )

        provider.invoke(self.context)

        prompt = "\n".join(str(message.content) for message in model.messages)
        self.assertIn("proposal.arguments must satisfy", prompt)
        for capability in self.definition.capabilities:
            self.assertIn(
                json.dumps(
                    {
                        "name": capability.metadata.name,
                        "description": capability.metadata.description,
                        "inputSchema": capability.input_schema,
                    },
                    sort_keys=True,
                ),
                prompt,
            )
        self.assertIn('"required": ["match", "actions"]', prompt)
        self.assertNotIn('"name": "link.disable"', prompt)

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

    def test_safety_instructions_reference_serialized_context_fields(self) -> None:
        model = StaticModel()
        provider = AgnoAgentProvider(
            self.definition,
            factory=AgnoAgentFactory(model_resolver=lambda configuration: model),
        )
        context = self.context.model_copy(
            update={
                "coordination": AgentCoordinationContext(
                    messageId="context-check",
                    correlationId="context-check",
                    kind="intent",
                    requestedAgentId=self.context.agent_id,
                    allowedDestinations=("another-agent",),
                ),
                "shared_state": SharedStateSnapshot(allowedScopes=("run",)),
            }
        )
        provider.invoke(context)
        prompt = "\n".join(str(message.content) for message in model.messages)
        self.assertIn('"allowed_destinations"', prompt)
        self.assertIn('"allowed_scopes"', prompt)
        self.assertIn("coordination.allowed_destinations", prompt)
        self.assertIn("shared_state.allowed_scopes", prompt)

    def test_declared_reasoning_timeout_cancels_agno_run(self) -> None:
        definition = replace(
            self.definition,
            blueprint=self.definition.blueprint.model_copy(
                update={"reasoning": ReasoningConfiguration(timeout="1ms")}
            ),
        )
        factory = AgnoAgentFactory(
            model_resolver=lambda configuration: SlowAsyncModel()
        )
        self.addCleanup(factory.close)
        provider = AgnoAgentProvider(definition, factory=factory)

        with self.assertRaises(AgentProviderError) as caught:
            provider.run(self.context)

        self.assertEqual(caught.exception.code, "agent.reasoning.timeout")
        self.assertIn("0.001 seconds", str(caught.exception))

    def test_repeated_timed_invocations_reuse_ollama_connections(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["Content-Length"]))
                payload = json.dumps(
                    {
                        "model": "llama3.1:8b",
                        "created_at": "2026-01-01T00:00:00Z",
                        "message": {
                            "role": "assistant",
                            "content": '{"message":"from Ollama"}',
                        },
                        "done": True,
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        factory = AgnoAgentFactory()
        try:
            with patch.dict("os.environ", {"OLLAMA_API_KEY": ""}):
                definition = replace(
                    self.definition,
                    blueprint=self.definition.blueprint.model_copy(
                        update={
                            "model": ModelConfiguration(
                                provider="ollama",
                                name="llama3.1:8b",
                                parameters={
                                    "host": f"http://127.0.0.1:{server.server_port}"
                                },
                            ),
                            "reasoning": ReasoningConfiguration(timeout="2s"),
                        }
                    ),
                )
                # Provider wrappers and worker threads may change between runs;
                # the factory's underlying Agno agent and HTTP client persist.
                for invocation in range(2):
                    for agent_id in ("switch-router@s1", "switch-router@s2"):
                        agent_definition = replace(
                            definition,
                            instance=definition.instance.model_copy(
                                update={"id": agent_id}
                            ),
                        )
                        provider = AgnoAgentProvider(agent_definition, factory=factory)
                        context = self.context.model_copy(
                            update={
                                "agent_id": agent_id,
                                "invocation_id": f"{agent_id}-invoke-{invocation}",
                            }
                        )
                        with ThreadPoolExecutor(max_workers=1) as worker:
                            response = worker.submit(provider.invoke, context).result()
                        self.assertEqual(response.message, "from Ollama")
        finally:
            factory.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_timeout_does_not_close_loop_for_next_invocation(self) -> None:
        class CancelOnceModel(StaticModel):
            loop: asyncio.AbstractEventLoop | None = None
            cancelled = False

            async def ainvoke(self, *args: Any, **kwargs: Any):
                if self.loop is None:
                    self.loop = asyncio.get_running_loop()
                    try:
                        await asyncio.sleep(1)
                    except asyncio.CancelledError:
                        self.cancelled = True
                        raise
                if self.loop is not asyncio.get_running_loop():
                    raise RuntimeError("async model moved to another event loop")
                return self.invoke(*args, **kwargs)

        model = CancelOnceModel()
        definition = replace(
            self.definition,
            blueprint=self.definition.blueprint.model_copy(
                update={"reasoning": ReasoningConfiguration(timeout="10ms")}
            ),
        )
        factory = AgnoAgentFactory(model_resolver=lambda configuration: model)
        self.addCleanup(factory.close)
        provider = AgnoAgentProvider(definition, factory=factory)
        with self.assertRaises(AgentProviderError) as caught:
            provider.invoke(self.context)
        self.assertEqual(caught.exception.code, "agent.reasoning.timeout")
        self.assertTrue(model.cancelled)
        response = AgnoAgentProvider(definition, factory=factory).invoke(
            self.context.model_copy(update={"invocation_id": "invoke-2"})
        )
        self.assertEqual(response.message, "from Agno")
        assert model.loop is not None
        self.assertFalse(model.loop.is_closed())
        factory.close()
        self.assertTrue(model.loop.is_closed())
        factory.close()  # Teardown is idempotent.

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
        self.assertGreaterEqual(execution.model_queueing_seconds, 0)
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
            factory = AgnoAgentFactory(
                model_resolver=lambda configuration: StaticModel(),
                database_path=Path(temporary) / "agno.sqlite3",
            )
            provider = AgnoAgentProvider(
                definition,
                factory=factory,
            )

            execution = provider.run(self.context)

            session_id = "run-1:switch-router@s1"
            self.assertEqual(execution.session_id, session_id)
            self.assertEqual(
                factory.session_state(self.context.agent_id, session_id),
                {},
            )
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
                    database_path=Path(temporary) / "agno.sqlite3",
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

    def test_verified_action_results_are_persisted_in_local_state(self) -> None:
        definition = self.memory_definition(
            MemoryConfiguration(local=LocalMemoryConfiguration(maxEntries=2))
        )
        with TemporaryDirectory() as temporary:
            factory = AgnoAgentFactory(
                model_resolver=lambda configuration: StaticModel(),
                database_path=Path(temporary) / "agno.sqlite3",
            )
            provider = AgnoAgentProvider(definition, factory=factory)
            provider.run(self.context)

            state = provider.record_action_results(
                self.context,
                (
                    ActionResult(
                        run_id="run-1",
                        request_id="proposal-1",
                        status=ActionStatus.SUCCEEDED,
                        completed_at=NOW,
                        changed=True,
                        output={"installed": True},
                    ),
                ),
            )

        assert state is not None
        results = cast(list[dict[str, Any]], state["mininetActionResults"])
        self.assertEqual(results[0]["output"], {"installed": True})

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
        factory = AgnoAgentFactory()
        agent = factory.create(self.definition)

        self.assertIsInstance(agent, Agent)
        self.assertIs(factory.create(self.definition), agent)
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
