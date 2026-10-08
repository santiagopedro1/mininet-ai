from pathlib import Path

import pytest
from agno.models.ollama import Ollama

from mininet_ai.agents.agno.ollama_factory import (
    create_deterministic_prompt_parsed_agent,
    create_prompt_parsed_agent,
)
from mininet_ai.agents.agno.ollama_model import create_ollama_model
from mininet_ai.compiler import compile_experiment
from mininet_ai.sdk import AgentProviderError, ExecutionCatalog
from mininet_ai.specification.models import ModelConfiguration


@pytest.mark.parametrize("prompt_parsed", [False, True])
def test_ollama_thinking_can_be_disabled(prompt_parsed: bool) -> None:
    configuration = ModelConfiguration(
        provider="ollama",
        name="qwen3.5:latest",
        parameters={"host": "http://10.10.10.201:11434", "think": False},
    )
    model = create_ollama_model(configuration, prompt_parsed=prompt_parsed)
    assert model.request_params == {"think": False}
    assert model.get_request_params()["think"] is False
    assert model.host == "http://10.10.10.201:11434"
    assert model.supports_native_structured_outputs is not prompt_parsed


def test_omitted_ollama_thinking_preserves_provider_default() -> None:
    model = create_ollama_model(
        ModelConfiguration(provider="ollama", name="qwen3.5:latest")
    )
    assert "think" not in model.get_request_params()


def test_ollama_thinking_can_be_enabled() -> None:
    model = create_ollama_model(
        ModelConfiguration(
            provider="ollama", name="qwen3.5:latest", parameters={"think": True}
        )
    )
    assert model.get_request_params()["think"] is True


@pytest.mark.parametrize("value", ["false", 0, None])
def test_ollama_thinking_requires_a_boolean(value: object) -> None:
    configuration = ModelConfiguration.model_validate(
        {"provider": "ollama", "name": "qwen3.5:latest", "parameters": {"think": value}}
    )
    with pytest.raises(AgentProviderError) as error:
        create_ollama_model(configuration)
    assert error.value.code == "agent.agno.ollama-think-invalid"


def test_fixed_policy_factory_pins_sampling_without_changing_default_factory() -> None:
    plan = compile_experiment(
        Path(__file__).parents[2] / "examples/hierarchical-routing/experiment.yaml"
    )
    definition = ExecutionCatalog(plan).resolve("switch-router@s1")
    deterministic = create_deterministic_prompt_parsed_agent(definition)
    standard = create_prompt_parsed_agent(definition)
    assert isinstance(deterministic.model, Ollama)
    assert isinstance(standard.model, Ollama)
    assert deterministic.model.get_request_params()["options"] == {
        "temperature": 0,
        "seed": 0,
    }
    assert "options" not in standard.model.get_request_params()
    instructions = str(deterministic.instructions)
    assert "coordination.allowed_destinations" in instructions
    assert "shared_state.allowed_scopes" in instructions
