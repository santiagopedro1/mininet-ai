from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime

from mininet_ai.agents import DeclarativeAgentProvider, PythonAgentProvider
from mininet_ai.compiler import compile_experiment
from mininet_ai.sdk import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    AgentContext,
    AgentProvider,
    AgentProviderError,
    ExecutionCatalog,
    ModelResponse,
)
from mininet_ai.specification.models import Implementation
from tests.compiler.helpers import EXAMPLE


NOW = datetime(2026, 1, 1, tzinfo=UTC)


class RecordingModel:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return self.response


class AgentProviderTests(unittest.TestCase):
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
                )
            }
        )
        return replace(self.definition, blueprint=blueprint)

    def test_declarative_provider_builds_one_scoped_model_request(self) -> None:
        model = RecordingModel(
            ModelResponse(
                structuredOutput={
                    "message": "install a specific forwarding rule",
                    "proposals": [
                        {
                            "id": "proposal-1",
                            "capability": "openflow.flow.install",
                            "target": "s1",
                            "arguments": {
                                "match": "ip",
                                "actions": "normal",
                            },
                        }
                    ],
                }
            )
        )
        provider = DeclarativeAgentProvider(self.definition, model)

        response = provider.invoke(self.context)

        self.assertIsInstance(provider, AgentProvider)
        self.assertEqual(response.proposals[0].target, "s1")
        model_request = model.requests[0]
        self.assertEqual(model_request.provider, "ollama")
        self.assertEqual(model_request.model, "llama3.1:8b")
        self.assertEqual(model_request.timeout_seconds, 20)
        self.assertIn("safe routing changes", model_request.messages[0].content)
        payload = json.loads(model_request.messages[1].content)
        self.assertEqual(payload["agentId"], "switch-router@s1")
        self.assertEqual(payload["targets"], ["s1"])
        self.assertEqual(
            model_request.response_schema["title"],
            "AgentResponse",
        )

    def test_declarative_provider_accepts_json_content_fallback(self) -> None:
        model = RecordingModel(ModelResponse(content='{"message":"done"}'))
        provider = DeclarativeAgentProvider(self.definition, model)

        response = provider.invoke(self.context)

        self.assertEqual(response.message, "done")

    def test_declarative_provider_rejects_invalid_model_output(self) -> None:
        cases = (
            (ModelResponse(content="not-json"), "agent.response.invalid-json"),
            (
                ModelResponse(structuredOutput={"metadata": {}}),
                "agent.response.invalid",
            ),
        )

        for response, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                provider = DeclarativeAgentProvider(
                    self.definition,
                    RecordingModel(response),
                )
                with self.assertRaises(AgentProviderError) as context:
                    provider.invoke(self.context)
                self.assertEqual(context.exception.code, expected_code)

    def test_all_agent_providers_reject_context_outside_compiled_scope(self) -> None:
        providers = (
            DeclarativeAgentProvider(
                self.definition,
                RecordingModel(ModelResponse(content='{"message":"done"}')),
            ),
            PythonAgentProvider(
                self.python_definition("tests.agents.helpers:mapping_agent")
            ),
        )

        invalid_contexts = (
            self.context.model_copy(update={"targets": ("s2",)}),
            self.context.model_copy(
                update={"observations": {"host.processes": []}}
            ),
        )

        for provider in providers:
            for invalid_context in invalid_contexts:
                with self.subTest(
                    provider=type(provider).__name__,
                    context=invalid_context,
                ):
                    with self.assertRaises(AgentProviderError) as context:
                        provider.invoke(invalid_context)
                    self.assertEqual(context.exception.code, "agent.context.invalid")

    def test_python_provider_loads_invokes_and_normalizes_entrypoints(self) -> None:
        cases = (
            ("tests.agents.helpers:mapping_agent", "inspected switch-router@s1"),
            (
                "tests.agents.helpers:response_agent",
                "Inspect forwarding and propose a safe repair",
            ),
        )

        for entrypoint, expected_message in cases:
            with self.subTest(entrypoint=entrypoint):
                provider = PythonAgentProvider(self.python_definition(entrypoint))
                response = provider.invoke(self.context)
                self.assertIsInstance(provider, AgentProvider)
                self.assertEqual(response.message, expected_message)

    def test_python_provider_reports_load_invocation_and_output_failures(self) -> None:
        construction_cases = (
            ("invalid", "agent.python.entrypoint-invalid"),
            ("tests.agents.missing:agent", "agent.python.load-failed"),
            (
                "tests.agents.helpers:missing_agent",
                "agent.python.load-failed",
            ),
            (
                "tests.agents.helpers:not_callable",
                "agent.python.entrypoint-invalid",
            ),
        )
        for entrypoint, expected_code in construction_cases:
            with self.subTest(entrypoint=entrypoint):
                with self.assertRaises(AgentProviderError) as context:
                    PythonAgentProvider(self.python_definition(entrypoint))
                self.assertEqual(context.exception.code, expected_code)

        invocation_cases = (
            (
                "tests.agents.helpers:failing_agent",
                "agent.python.invocation-failed",
            ),
            ("tests.agents.helpers:invalid_agent", "agent.response.invalid"),
        )
        for entrypoint, expected_code in invocation_cases:
            with self.subTest(entrypoint=entrypoint):
                provider = PythonAgentProvider(self.python_definition(entrypoint))
                with self.assertRaises(AgentProviderError) as context:
                    provider.invoke(self.context)
                self.assertEqual(context.exception.code, expected_code)

    def test_python_provider_preserves_timeout_errors(self) -> None:
        provider = PythonAgentProvider(
            self.python_definition("tests.agents.helpers:timeout_agent")
        )

        with self.assertRaises(TimeoutError):
            provider.invoke(self.context)

    def test_implementation_mismatches_and_missing_models_are_rejected(self) -> None:
        with self.assertRaises(AgentProviderError) as declarative:
            DeclarativeAgentProvider(
                self.python_definition("tests.agents.helpers:mapping_agent"),
                RecordingModel(ModelResponse(content='{"message":"done"}')),
            )

        blueprint = self.definition.blueprint.model_copy(update={"model": None})
        missing_model = replace(self.definition, blueprint=blueprint)
        with self.assertRaises(AgentProviderError) as model:
            DeclarativeAgentProvider(
                missing_model,
                RecordingModel(ModelResponse(content='{"message":"done"}')),
            )

        with self.assertRaises(AgentProviderError) as python:
            PythonAgentProvider(self.definition)

        self.assertEqual(
            declarative.exception.code,
            "agent.configuration.implementation-mismatch",
        )
        self.assertEqual(model.exception.code, "agent.configuration.model-missing")
        self.assertEqual(
            python.exception.code,
            "agent.configuration.implementation-mismatch",
        )


if __name__ == "__main__":
    unittest.main()
