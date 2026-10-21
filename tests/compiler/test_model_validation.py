"""Source-only model checks at the compiler/CLI/catalog seams."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from mininet_ai.agents import AgnoAgentFactory
from mininet_ai.cli import app
from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import SpecificationError
from mininet_ai.sdk import ExecutionCatalog
from mininet_ai.specification.loader import resolve_experiment
from mininet_ai.specification.models import Experiment
from tests.specification_fixtures import COMPILER_MULTILAYER_SPECIFICATION


@pytest.fixture
def source():
    return compile_experiment(COMPILER_MULTILAYER_SPECIFICATION).snapshot


@pytest.mark.parametrize(
    "entrypoint",
    [
        None,
        "mininet_ai.agents.agno.ollama_factory:create_prompt_parsed_agent",
        "mininet_ai.agents.agno.ollama_factory:create_deterministic_prompt_parsed_agent",
    ],
)
@pytest.mark.parametrize("think", ["SECRET", 0, None])
def test_invalid_ollama_think_fails_before_plan(source, entrypoint, think):
    blueprint = source["blueprints"][0]
    blueprint["model"] = {
        "provider": "ollama",
        "name": "test",
        "parameters": {"think": think},
    }
    if entrypoint:
        blueprint["implementation"] = {"type": "python", "entrypoint": entrypoint}
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(Experiment.model_validate(source))
    assert blueprint["metadata"]["name"] in str(caught.value)
    assert "parameters.think" in str(caught.value)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("command", [["validate"], ["plan", "--format", "json"]])
@pytest.mark.parametrize(
    "model",
    [
        {"provider": "ollama", "name": "test", "parameters": {"think": "SECRET"}},
        {"provider": "ollama", "name": "test", "parameters": {"api_key": "SECRET"}},
        {
            "provider": "mock",
            "name": "test",
            "parameters": {"response": {"message": "SECRET", "proposals": [{}]}},
        },
        {
            "provider": "mock",
            "name": "test",
            "parameters": {"response": {"message": "ok"}, "usage": {"cost": "SECRET"}},
        },
        {"provider": "ollama", "name": "test", "api_key": "SECRET"},
    ],
)
def test_cli_rejects_invalid_parameters_without_values_or_runtime(
    source, tmp_path: Path, command, model
):
    source["blueprints"][0]["model"] = model
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(source))
    with patch("mininet_ai.cli.create_substrate_runtime") as runtime:
        result = CliRunner().invoke(app, [*command, str(path)])
    assert result.exit_code == 1, result.output
    assert "model" in result.output
    assert "SECRET" not in result.output
    assert '"snapshot"' not in result.output
    runtime.assert_not_called()


@pytest.mark.parametrize(
    ("parameters", "field"),
    [
        ({}, "response"),
        ({"response": "SECRET"}, "response"),
        ({"response": {}}, "response"),
        ({"response": {"message": "SECRET", "proposals": [{}]}}, "response"),
        ({"response": {"message": "ok"}, "usage": None}, "usage"),
        ({"response": {"message": "ok"}, "usage": []}, "usage"),
        ({"response": {"message": "ok"}, "usage": {"inputTokens": -1}}, "usage"),
        ({"response": {"message": "ok"}, "usage": {"cost": -1}}, "usage"),
        ({"response": {"message": "ok"}, "usage": {"unknown": "SECRET"}}, "usage"),
        (
            {
                "response": {"message": "ok"},
                "usage": {"inputTokens": 2, "totalTokens": 1},
            },
            "usage",
        ),
        ({"response": {"message": "ok"}, "usage": {"audioOutputTokens": 2}}, "usage"),
        ({"response": {"message": "ok"}, "api_key": "SECRET"}, "parameters"),
    ],
)
def test_invalid_mock_values_fail_safely(source, parameters, field):
    source["blueprints"][0]["model"] = {
        "provider": "mock",
        "name": "offline",
        "parameters": parameters,
    }
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(Experiment.model_validate(source))
    assert field in str(caught.value)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("provider", ["ollama", "openai", "delegated"])
def test_unsupported_literal_credentials_are_not_persisted(source, provider):
    source["blueprints"][0]["model"] = {
        "provider": provider,
        "name": "test",
        "parameters": {"api_key": "SECRET"},
    }
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(Experiment.model_validate(source))
    assert "parameters" in str(caught.value)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize(
    "entrypoint",
    [None, "create_prompt_parsed_agent", "create_deterministic_prompt_parsed_agent"],
)
@pytest.mark.parametrize(
    "model",
    [
        None,
        {
            "provider": "mock",
            "name": "offline",
            "parameters": {"response": {"message": "ok"}},
        },
    ],
)
def test_required_models_and_bundled_provider_are_checked(source, entrypoint, model):
    if entrypoint is None and model is not None:
        source["blueprints"][0]["model"] = model
        compile_experiment(Experiment.model_validate(source))
        return  # Declarative mock is supported.
    blueprint = source["blueprints"][0]
    blueprint["model"] = model
    if entrypoint:
        blueprint["implementation"] = {
            "type": "python",
            "entrypoint": f"mininet_ai.agents.agno.ollama_factory:{entrypoint}",
        }
    with pytest.raises(SpecificationError, match="model"):
        compile_experiment(resolve_experiment(Experiment.model_validate(source)))


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"think": True},
        {"think": False},
        {"host": "https://example.test/prefix"},
        {"host": "https://example.test/prefix", "think": True},
        {"host": "https://example.test/prefix", "think": False},
    ],
)
@pytest.mark.parametrize(
    "entrypoint",
    [None, "create_prompt_parsed_agent", "create_deterministic_prompt_parsed_agent"],
)
def test_supported_ollama_parameters_are_preserved_offline(
    source, parameters, entrypoint
):
    source["blueprints"][0]["model"]["parameters"] = parameters
    if entrypoint:
        source["blueprints"][0]["implementation"] = {
            "type": "python",
            "entrypoint": f"mininet_ai.agents.agno.ollama_factory:{entrypoint}",
        }
    with (
        patch("os.getenv", side_effect=AssertionError("credential lookup")),
        patch(
            "agno.models.ollama.Ollama.__init__",
            side_effect=AssertionError("model construction"),
        ),
        patch(
            "httpx.Client.__init__", side_effect=AssertionError("client construction")
        ),
    ):
        plan = compile_experiment(Experiment.model_validate(source))
    assert plan.snapshot["blueprints"][0]["model"]["parameters"] == parameters


def test_mock_schema_coercion_is_validated_but_not_normalized(source):
    parameters = {
        "response": {"message": "offline"},
        "usage": {"input_tokens": "2", "totalTokens": "2"},
    }
    source["blueprints"][0]["model"] = {
        "provider": "mock",
        "name": "offline",
        "parameters": parameters,
    }
    plan = compile_experiment(Experiment.model_validate(source))
    assert plan.snapshot["blueprints"][0]["model"]["parameters"] == parameters


@pytest.mark.parametrize("provider", ["unknown-provider", "openai"])
def test_empty_parameters_keep_provider_resolution_delegated(source, provider):
    source["blueprints"][0]["model"] = {"provider": provider, "name": "anything"}
    compile_experiment(Experiment.model_validate(source))


@pytest.mark.parametrize(
    "model",
    [
        None,
        {
            "provider": "ollama",
            "name": "owned",
            "parameters": {
                "think": "factory-owned",
                "options": {"api_key": "not-a-credential-selector"},
            },
        },
    ],
)
def test_custom_factory_is_not_imported_and_owns_parameters(source, model):
    source["blueprints"][0]["implementation"] = {
        "type": "python",
        "entrypoint": "not_installed.agent:factory",
    }
    source["blueprints"][0]["model"] = model
    with patch("importlib.import_module", side_effect=AssertionError("factory import")):
        plan = compile_experiment(Experiment.model_validate(source))
    effective = plan.snapshot["blueprints"][0]["model"]
    assert effective == model


def test_custom_factory_consumes_exact_configuration_and_keeps_its_model(source):
    source["blueprints"][0]["implementation"] = {
        "type": "python",
        "entrypoint": "tests.agents.agno_helpers:consuming_factory",
    }
    configuration = {
        "provider": "ollama",
        "name": "not-the-factory-model",
        "parameters": {
            "custom-response": "consumed configuration",
            "think": "custom-owned",
        },
    }
    source["blueprints"][0]["model"] = configuration
    plan = compile_experiment(Experiment.model_validate(source))
    definition = ExecutionCatalog(plan).resolve("switch-router@s1")
    assert definition.blueprint.model is not None
    assert definition.blueprint.model.model_dump() == configuration
    agent = AgnoAgentFactory().create(definition)
    assert agent.model is not None and not isinstance(agent.model, str)
    assert agent.model.id == "static"
    assert agent.model.invoke().content == '{"message": "consumed configuration"}'


@pytest.mark.parametrize(
    "entrypoint",
    [None, "create_prompt_parsed_agent", "create_deterministic_prompt_parsed_agent"],
)
def test_unsupported_ollama_parameter_is_rejected_on_owned_paths(source, entrypoint):
    blueprint = source["blueprints"][0]
    blueprint["model"]["parameters"] = {"temperature": 0.2}
    if entrypoint:
        blueprint["implementation"] = {
            "type": "python",
            "entrypoint": f"mininet_ai.agents.agno.ollama_factory:{entrypoint}",
        }
    with pytest.raises(SpecificationError, match="parameters"):
        compile_experiment(Experiment.model_validate(source))


def test_mock_compilation_does_not_import_agno_runtime_or_read_credentials():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import builtins
import os
original_import = builtins.__import__
original_getitem = type(os.environ).__getitem__
def guarded_import(name, *args, **kwargs):
    if name.startswith(('agno', 'ollama', 'mininet_ai.agents')):
        raise AssertionError('runtime import: ' + name)
    return original_import(name, *args, **kwargs)
def guarded_getitem(self, key):
    if key in {'OLLAMA_API_KEY', 'OPENAI_API_KEY', 'OLLAMA_HOST'}:
        raise AssertionError('runtime environment lookup: ' + key)
    return original_getitem(self, key)
builtins.__import__ = guarded_import
type(os.environ).__getitem__ = guarded_getitem
from mininet_ai.compiler import compile_experiment
from mininet_ai.specification.models import Experiment
from tests.specification_fixtures import COMPILER_MULTILAYER_SPECIFICATION
source = compile_experiment(COMPILER_MULTILAYER_SPECIFICATION).snapshot
source['blueprints'][0]['model'] = {
    'provider': 'mock', 'name': 'offline',
    'parameters': {'response': {'message': 'ok'}, 'usage': {}}
}
compile_experiment(Experiment.model_validate(source))
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
