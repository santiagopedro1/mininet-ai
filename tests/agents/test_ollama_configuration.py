from __future__ import annotations

import asyncio
import os
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import httpx
import yaml
from agno.models.ollama import Ollama
from pydantic import ValidationError
from typer.testing import CliRunner

from mininet_ai.agents import AgnoAgentFactory
from mininet_ai.cli import app
from mininet_ai.compiler import compile_experiment
from mininet_ai.sdk import AgentProviderError, AgentResponse, ExecutionCatalog
from mininet_ai.specification.models import Implementation, ModelConfiguration
from tests.specification_fixtures import COMPILER_MULTILAYER_SPECIFICATION


class OllamaConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.definition = ExecutionCatalog(
            compile_experiment(COMPILER_MULTILAYER_SPECIFICATION)
        ).resolve("switch-router@s1")

    def configured_definition(
        self, parameters: dict[str, Any], *, prompt_parsed: bool = False
    ):
        blueprint = self.definition.blueprint.model_copy(
            update={
                "model": ModelConfiguration(
                    provider="ollama", name="llama3.1:8b", parameters=parameters
                ),
                "implementation": Implementation(
                    type="python",
                    entrypoint="mininet_ai.agents.agno.ollama_factory:create_prompt_parsed_agent",
                )
                if prompt_parsed
                else Implementation(),
            }
        )
        return replace(self.definition, blueprint=blueprint)

    def test_declarative_agent_uses_configured_ollama_host(self) -> None:
        agent = AgnoAgentFactory().create(
            self.configured_definition({"host": "http://ollama.example:11434"})
        )
        self.assertIsInstance(agent.model, Ollama)
        assert isinstance(agent.model, Ollama)
        self.assertEqual(agent.model.host, "http://ollama.example:11434")
        self.assertTrue(agent.model.supports_native_structured_outputs)

    def test_prompt_parsed_factory_honors_host_without_changing_output_mode(
        self,
    ) -> None:
        agent = AgnoAgentFactory().create(
            self.configured_definition(
                {"host": "https://ollama.example:11434"}, prompt_parsed=True
            )
        )
        assert isinstance(agent.model, Ollama)
        self.assertEqual(agent.model.host, "https://ollama.example:11434")
        self.assertFalse(agent.model.supports_native_structured_outputs)
        self.assertFalse(agent.structured_outputs)
        self.assertIs(agent.output_schema, AgentResponse)

    def test_invalid_host_is_rejected_by_public_configuration(self) -> None:
        for host in (
            None,
            1,
            False,
            {},
            "",
            " ",
            "localhost:11434",
            "ftp://ollama.example",
            "http://",
            "http:///missing-host",
            "http://host:99999",
            "http://user:secret@host",
            "http://@host",
            "http://host\n",
            "http://host\x00",
            "http://host/path with spaces",
            "http://host\\evil",
        ):
            with self.subTest(host=host), self.assertRaises(ValidationError):
                ModelConfiguration(
                    provider="ollama", name="llama3.1:8b", parameters={"host": host}
                )

    def test_valid_hosts_are_preserved_without_snapshot_normalization(self) -> None:
        for host in (
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "https://ollama.example",
            "http://[::1]:11434",
            "https://ollama.example/prefix",
        ):
            with self.subTest(host=host):
                model = ModelConfiguration(
                    provider="ollama", name="llama3.1:8b", parameters={"host": host}
                )
                self.assertEqual(model.model_dump()["parameters"]["host"], host)

    def test_unknown_ollama_parameters_are_rejected_in_both_factories(self) -> None:
        for prompt_parsed in (False, True):
            with self.subTest(prompt_parsed=prompt_parsed):
                with self.assertRaises(AgentProviderError) as caught:
                    AgnoAgentFactory().create(
                        self.configured_definition(
                            {"temperature": 0.2}, prompt_parsed=prompt_parsed
                        )
                    )
                expected = (
                    "agent.agno.factory-failed"
                    if prompt_parsed
                    else "agent.agno.model-parameters-unsupported"
                )
                self.assertEqual(caught.exception.code, expected)
                self.assertIn(
                    "unsupported Ollama model parameters", str(caught.exception)
                )

    def test_invalid_yaml_host_fails_validation_before_runtime_creation(self) -> None:
        snapshot = compile_experiment(COMPILER_MULTILAYER_SPECIFICATION).snapshot
        snapshot["blueprints"][0]["model"]["parameters"] = {
            "host": "http://user:example-password@host"
        }
        with (
            TemporaryDirectory() as temporary,
            patch("mininet_ai.cli.create_substrate_runtime") as create,
        ):
            experiment = Path(temporary) / "experiment.yaml"
            experiment.write_text(yaml.safe_dump(snapshot), encoding="utf-8")
            result = CliRunner().invoke(app, ["validate", str(experiment)])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(
            "Ollama host must not contain credentials", " ".join(result.output.split())
        )
        self.assertNotIn("example-password", result.output)
        create.assert_not_called()

    def test_sync_and_async_clients_use_explicit_host_or_environment_fallback(
        self,
    ) -> None:
        for prompt_parsed in (False, True):
            for explicit in (False, True):
                with (
                    self.subTest(prompt_parsed=prompt_parsed, explicit=explicit),
                    patch.dict(
                        os.environ,
                        {
                            "OLLAMA_HOST": "http://environment.example:11434",
                            "OLLAMA_API_KEY": "",
                        },
                    ),
                ):
                    parameters = (
                        {"host": "http://configured.example:11434"} if explicit else {}
                    )
                    agent = AgnoAgentFactory().create(
                        self.configured_definition(
                            parameters, prompt_parsed=prompt_parsed
                        )
                    )
                    assert isinstance(agent.model, Ollama)
                    requests: list[str] = []

                    def respond(
                        request: httpx.Request, requests: list[str] = requests
                    ) -> httpx.Response:
                        requests.append(str(request.url))
                        return httpx.Response(200, json={"models": []})

                    agent.model.client_params = {
                        "transport": httpx.MockTransport(respond)
                    }
                    client = agent.model.get_client()
                    client.list()

                    async def request_async(model: Ollama) -> None:
                        await model.get_async_client().list()

                    asyncio.run(request_async(agent.model))
                    expected = (
                        "http://configured.example:11434/api/tags"
                        if explicit
                        else "http://environment.example:11434/api/tags"
                    )
                    self.assertEqual(requests, [expected, expected])
                    self.assertEqual(
                        os.environ["OLLAMA_HOST"], "http://environment.example:11434"
                    )

    def test_no_host_and_no_environment_keep_ollama_local_default(self) -> None:
        for prompt_parsed in (False, True):
            with self.subTest(prompt_parsed=prompt_parsed), patch.dict(os.environ):
                os.environ.pop("OLLAMA_HOST", None)
                os.environ.pop("OLLAMA_API_KEY", None)
                agent = AgnoAgentFactory().create(
                    self.configured_definition({}, prompt_parsed=prompt_parsed)
                )
                assert isinstance(agent.model, Ollama)
                self.assertIsNone(agent.model.host)
                requests: list[str] = []

                def respond(
                    request: httpx.Request, requests: list[str] = requests
                ) -> httpx.Response:
                    requests.append(str(request.url))
                    return httpx.Response(200, json={"models": []})

                agent.model.client_params = {"transport": httpx.MockTransport(respond)}
                agent.model.get_client().list()
                self.assertEqual(requests, ["http://127.0.0.1:11434/api/tags"])

    def test_different_agents_keep_independent_hosts(self) -> None:
        factory = AgnoAgentFactory()
        first = self.configured_definition({"host": "http://first.example:11434"})
        second = self.configured_definition({"host": "http://second.example:11434"})
        second = replace(
            second, instance=second.instance.model_copy(update={"id": "another-agent"})
        )
        first_model = factory.create(first).model
        second_model = factory.create(second).model
        assert isinstance(first_model, Ollama) and isinstance(second_model, Ollama)
        self.assertEqual(first_model.host, "http://first.example:11434")
        self.assertEqual(second_model.host, "http://second.example:11434")

    def test_api_key_preserves_cloud_default_unless_host_is_explicit(self) -> None:
        for prompt_parsed in (False, True):
            for explicit in (False, True):
                with (
                    self.subTest(prompt_parsed=prompt_parsed, explicit=explicit),
                    patch.dict(
                        os.environ,
                        {
                            "OLLAMA_HOST": "http://environment.example:11434",
                            "OLLAMA_API_KEY": "test-api-key",
                        },
                    ),
                ):
                    parameters = (
                        {"host": "http://configured.example:11434"} if explicit else {}
                    )
                    agent = AgnoAgentFactory().create(
                        self.configured_definition(
                            parameters, prompt_parsed=prompt_parsed
                        )
                    )
                    assert isinstance(agent.model, Ollama)
                    requests: list[httpx.Request] = []

                    def respond(
                        request: httpx.Request, requests: list[httpx.Request] = requests
                    ) -> httpx.Response:
                        requests.append(request)
                        return httpx.Response(200, json={"models": []})

                    agent.model.client_params = {
                        "transport": httpx.MockTransport(respond)
                    }
                    agent.model.get_client().list()
                    expected = (
                        "http://configured.example:11434/api/tags"
                        if explicit
                        else "https://ollama.com/api/tags"
                    )
                    self.assertEqual(str(requests[0].url), expected)
                    self.assertEqual(
                        requests[0].headers["authorization"], "Bearer test-api-key"
                    )
                    self.assertEqual(
                        os.environ["OLLAMA_HOST"], "http://environment.example:11434"
                    )
