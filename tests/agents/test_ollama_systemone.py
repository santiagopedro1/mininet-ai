"""Native Ollama System One provider coverage."""

from __future__ import annotations

import json
from threading import Event
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from mininet_ai.agents.bounded import (
    OllamaSystemOneClient,
    create_systemone_client,
    map_choice,
)
from mininet_ai.compiler import (
    compile_experiment,
    load_deployment_plan,
    save_deployment_plan,
)
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.specification.models import AgentBlueprint, ModelConfiguration
from tests.compiler.test_bounded_decision_contract import _source_for_compile

MODEL = "clef-flash:9b"
DIGEST = "73e026ff389b28464bb26f8978f3164851a76832f0d339032747e2c56f98ea29"


def ollama_blueprint() -> dict[str, Any]:
    return {
        "apiVersion": "mininet-ai/v1alpha5",
        "kind": "AgentBlueprint",
        "metadata": {"name": "decision-maker"},
        "implementation": {"type": "declarative"},
        "execution": {"kind": "bounded-decision", "scenario": "diamond-policy"},
        "model": {
            "provider": "ollama-systemone",
            "name": MODEL,
            "parameters": {"host": "http://10.10.10.152:11434"},
        },
    }


class OllamaTransport:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.models = [
            {
                "name": MODEL,
                "model": MODEL,
                "digest": DIGEST,
                "capabilities": ["decision", "vision"],
            }
        ]
        self.version = "0.40.14"
        self.answer = "no_action"
        self.response_usage = {"input_tokens": 32, "output_tokens": 1}

    def get_json(self, url: str, **kwargs: Any) -> Any:
        if url.endswith("/api/version"):
            return {"version": self.version}
        if url.endswith("/api/tags"):
            return {"models": self.models}
        raise AssertionError(f"unexpected startup URL {url}")

    def post_json(self, url: str, payload: Any, **kwargs: Any) -> Any:
        self.posts.append((url, dict(payload), kwargs))
        return {
            "model": MODEL,
            "answers": {
                "next_action": {
                    "type": "choice",
                    "choice": self.answer,
                    "probabilities": {"no_action": 0.8, "install_recovery": 0.2},
                    "confidence": 0.6,
                }
            },
            "usage": self.response_usage,
        }


def test_ollama_choice_request_includes_model_and_accepts_scoring_tokens():
    transport = OllamaTransport()
    client = OllamaSystemOneClient(
        "http://10.10.10.152:11434/", MODEL, transport, startup_timeout_seconds=0.5
    )
    result = client.decide(
        state='{"ports":{}}',
        instructions="Choose one.",
        criteria={"no_action": "Healthy", "install_recovery": "Repair"},
        choices={"no_action": (), "install_recovery": ()},
        timeout_seconds=1.5,
    )

    assert result.choice_id == "no_action"
    assert result.probabilities == {"no_action": 0.8, "install_recovery": 0.2}
    assert result.confidence == 0.6
    url, payload, kwargs = transport.posts[0]
    assert url == "http://10.10.10.152:11434/v1/systemone"
    assert payload["model"] == MODEL
    assert payload["state"] == '{"ports":{}}'
    assert payload["questions"]["next_action"]["type"] == "choice"
    assert kwargs["timeout_seconds"] == 1.5


def test_llama_native_contract_still_rejects_generated_tokens_by_default():
    with pytest.raises(AgentRuntimeError, match="must not generate output tokens"):
        map_choice(
            {
                "answers": {"next_action": {"type": "choice", "choice": "no_action"}},
                "usage": {"output_tokens": 1},
            },
            {"no_action": (), "install_recovery": ()},
        )


def test_ollama_response_for_another_model_is_rejected():
    transport = OllamaTransport()
    transport.answer = "no_action"
    client = OllamaSystemOneClient("http://localhost:11434", MODEL, transport)
    original_post_json = transport.post_json

    def wrong_model(url, payload, **kwargs):
        response = original_post_json(url, payload, **kwargs)
        response["model"] = "qwen3.5:latest"
        return response

    transport.post_json = wrong_model  # type: ignore[method-assign]
    with pytest.raises(AgentRuntimeError, match="does not match requested"):
        client.decide(
            state="{}",
            instructions="Choose one.",
            criteria={"no_action": "Healthy", "install_recovery": "Repair"},
            choices={"no_action": (), "install_recovery": ()},
            timeout_seconds=1.0,
        )


def test_ollama_startup_verifies_tag_digest_and_native_decision_interface():
    transport = OllamaTransport()
    client = OllamaSystemOneClient(
        "http://10.10.10.152:11434", MODEL, transport, startup_timeout_seconds=0.5
    )
    startup = client.verify_startup(
        model_name=MODEL,
        expected_identity={"digest": f"sha256:{DIGEST}"},
        instructions="Choose one.",
        criteria={"no_action": "Healthy", "install_recovery": "Repair"},
        choices={"no_action": (), "install_recovery": ()},
    )

    assert startup["status"] == "verified"
    assert startup["identityVerification"] == "checked"
    assert startup["observedModel"] == MODEL
    assert startup["observedIdentity"]["digest"] == DIGEST
    assert startup["observedIdentity"]["serverVersion"] == "0.40.14"
    assert startup["interface"] == "verified"
    assert startup["health"] == {"status": "ok", "version": "0.40.14"}
    assert transport.posts[0][1]["model"] == MODEL


def test_ollama_startup_checks_model_presence_and_decision_capability():
    transport = OllamaTransport()
    transport.models = [
        {
            "name": MODEL,
            "model": MODEL,
            "digest": DIGEST,
            "capabilities": ["completion", "tools"],
        }
    ]
    client = OllamaSystemOneClient("http://localhost:11434", MODEL, transport)
    parameters = {
        "model_name": MODEL,
        "expected_identity": None,
        "instructions": "Choose one.",
        "criteria": {"no_action": "Healthy", "install_recovery": "Repair"},
        "choices": {"no_action": (), "install_recovery": ()},
    }
    with pytest.raises(AgentRuntimeError, match="does not advertise decision"):
        client.verify_startup(**parameters)

    transport.models = []
    with pytest.raises(AgentRuntimeError, match="is not available"):
        client.verify_startup(**parameters)

    transport.models = [
        {"name": MODEL, "model": MODEL, "digest": DIGEST, "capabilities": ["decision"]}
    ]
    startup = client.verify_startup(**parameters)
    assert startup["status"] == "unverified"
    assert startup["observedModel"] == MODEL


def test_ollama_startup_rejects_a_different_digest():
    transport = OllamaTransport()
    client = OllamaSystemOneClient("http://localhost:11434", MODEL, transport)
    with pytest.raises(AgentRuntimeError, match="identity mismatch for digest"):
        client.verify_startup(
            model_name=MODEL,
            expected_identity={"digest": "0" * 64},
            instructions="Choose one.",
            criteria={"no_action": "Healthy", "install_recovery": "Repair"},
            choices={"no_action": (), "install_recovery": ()},
        )


@pytest.mark.parametrize(
    "parameters",
    [
        {"host": "http://localhost:11434", "endpoint": "http://localhost:8080"},
        {"host": "http://user:secret@localhost:11434"},
        {"host": "http://localhost:11434?query=bad"},
        {"host": "http://localhost:11434", "startupTimeout": "never"},
        {
            "host": "http://localhost:11434",
            "expectedIdentity": {"repository": "not-ollama"},
        },
        {
            "host": "http://localhost:11434",
            "verifyIdentity": False,
            "expectedIdentity": {"digest": DIGEST},
        },
    ],
)
def test_ollama_bounded_model_parameters_are_allowlisted_and_validated(parameters):
    document = ollama_blueprint()
    document["model"]["parameters"] = parameters
    with pytest.raises(ValidationError):
        AgentBlueprint.model_validate(document)


def test_ollama_bounded_model_accepts_pinned_digest_and_startup_timeout():
    document = ollama_blueprint()
    document["model"]["parameters"] = {
        "host": "http://10.10.10.152:11434",
        "startupTimeout": "120s",
        "expectedIdentity": {"digest": f"sha256:{DIGEST}"},
    }
    blueprint = AgentBlueprint.model_validate(document)
    assert isinstance(blueprint.model, ModelConfiguration)
    assert blueprint.model.provider == "ollama-systemone"


def test_factory_selects_ollama_client_with_declared_startup_timeout():
    model = ModelConfiguration(
        provider="ollama-systemone",
        name=MODEL,
        parameters={
            "host": "http://10.10.10.152:11434",
            "startupTimeout": "90s",
        },
    )
    client = create_systemone_client(model, OllamaTransport())
    assert isinstance(client, OllamaSystemOneClient)
    assert client.startup_timeout_seconds == 90
    assert client.request_url == "http://10.10.10.152:11434/v1/systemone"


def test_saved_ollama_plan_runs_native_no_action_through_runtime(tmp_path, monkeypatch):
    import importlib

    from mininet_ai import experiment as experiment_module
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    source = _source_for_compile(tmp_path)
    document = yaml.safe_load(source.read_text())
    document["blueprints"] = [ollama_blueprint()]
    document["decisionScenarios"][0]["timing"]["decisionDeadline"] = "3s"
    source.write_text(yaml.safe_dump(document))
    plan = compile_experiment(source)
    restored = load_deployment_plan(
        save_deployment_plan(plan, tmp_path / "saved-ollama-plan.json")
    )
    assert restored.snapshot["blueprints"][0]["model"]["provider"] == "ollama-systemone"

    transport = OllamaTransport()
    substrate = FakeSubstrateRuntime()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed = Event()
    results: list[Any] = []

    def record_result(record):
        results.append(record.result)
        completed.set()

    def forbidden_agno_factory(*args, **kwargs):
        raise AssertionError("bounded execution must not construct Agno")

    monkeypatch.setattr(experiment_module, "AgnoAgentFactory", forbidden_agno_factory)
    agent_runtime_module = importlib.import_module("mininet_ai.agents.runtime")
    monkeypatch.setattr(
        agent_runtime_module, "AgnoAgentFactory", forbidden_agno_factory
    )
    runtime = ExperimentRuntime(
        restored,
        substrate,
        registries,
        decision_transport=transport,
        invocation_listener=record_result,
    )
    runtime.start()
    try:
        assert completed.wait(3), "scheduled Ollama bounded decision did not complete"
    finally:
        runtime.stop()

    result = results[0]
    assert result.status.value == "succeeded"
    assert result.response is not None
    assert result.response.metadata["selectedChoice"] == "no_action"
    evidence = result.response.metadata["executionRecord"]["runtime"]["boundedDecision"]
    assert evidence["effectiveConfiguration"]["provider"] == "ollama-systemone"
    assert evidence["request"]["url"] == ("http://10.10.10.152:11434/v1/systemone")
    assert evidence["request"]["model"] == MODEL
    assert transport.posts[-1][1]["model"] == MODEL
    assert json.loads(evidence["request"]["state"])["apiVersion"].startswith(
        "mininet-ai/bounded-decision-context/"
    )
    from pathlib import Path

    from jsonschema import Draft202012Validator

    schema_root = Path(__file__).parents[2] / "mininet_ai" / "compiler" / "schemas"
    schema = json.loads((schema_root / "bounded-decision-v1alpha1.json").read_text())
    Draft202012Validator(schema).validate(evidence)
