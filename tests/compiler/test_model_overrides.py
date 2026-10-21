"""Overrides at the source → plan → SDK and offline construction boundaries."""

import os
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
import yaml
from agno.models.ollama import Ollama
from typer.testing import CliRunner

from mininet_ai.agents import AgnoAgentFactory, AgnoAgentProvider
from mininet_ai.cli import app
from mininet_ai.compiler import compile_experiment
from mininet_ai.compiler.models import DeploymentPlan
from mininet_ai.errors import SpecificationError
from mininet_ai.sdk import AgentContext
from mininet_ai.sdk.catalog import ExecutionCatalog
from mininet_ai.specification.loader import resolve_experiment
from mininet_ai.specification.models import (
    Experiment,
    ModelConfiguration,
    ModelReference,
)
from tests.compiler.test_model_references import project


def write_project(path, snapshot, base):
    path.write_text(yaml.safe_dump(snapshot))
    path.with_name("models.yaml").write_text(
        yaml.safe_dump({"models": {"shared": base}})
    )


@pytest.mark.parametrize("overrides", [{}, {"parameters": {}}, {"name": "base"}])
def test_omission_and_empty_overrides_inherit_without_changing_identity(
    tmp_path, overrides
):
    path, snapshot = project(tmp_path)
    original = compile_experiment(path)
    for blueprint in snapshot["blueprints"]:
        blueprint["model"]["overrides"] = overrides
    # Match the original ID for the explicit name case.
    if "name" in overrides:
        overrides["name"] = "llama3.2:3b"
    path.write_text(yaml.safe_dump(snapshot))
    plan = compile_experiment(path)
    assert plan.snapshot == original.snapshot
    assert plan.digest == original.digest
    assert plan.model_provenance["local-router"].overrides == overrides


def test_nested_values_replace_whole_keys_and_null_is_a_factory_owned_value(tmp_path):
    path, snapshot = project(tmp_path)
    for blueprint in snapshot["blueprints"]:
        blueprint["implementation"] = {
            "type": "python",
            "entrypoint": "not_imported:factory",
        }
    overrides = {"parameters": {"nested": {"replacement": [None, 2]}, "nullable": None}}
    snapshot["blueprints"][1]["model"]["overrides"] = overrides
    base = {
        "provider": "custom",
        "name": "base",
        "parameters": {
            "nested": {"old": 1, "retain": 2},
            "nullable": "old",
            "keep": [1],
        },
    }
    write_project(path, snapshot, base)
    plan = compile_experiment(path)
    assert plan.snapshot["blueprints"][0]["model"] == base
    expected = {
        "provider": "custom",
        "name": "base",
        "parameters": {
            "nested": {"replacement": [None, 2]},
            "nullable": None,
            "keep": [1],
        },
    }
    assert plan.snapshot["blueprints"][1]["model"] == expected
    assert plan.model_provenance["second-router"].overrides == overrides
    # Overriding did not mutate the shared base or the supplied source object.
    assert base["parameters"]["nested"] == {"old": 1, "retain": 2}
    assert snapshot["blueprints"][1]["model"]["overrides"] == overrides


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"provider": "SECRET_SENTINEL"}, "provider"),
        ({"unknown": "SECRET_SENTINEL"}, "unknown"),
        ({"name": None}, "name"),
        ({"name": ""}, "name"),
        ({"name": ["SECRET_SENTINEL"]}, "name"),
        ({"parameters": None}, "parameters"),
        ({"parameters": ["SECRET_SENTINEL"]}, "parameters"),
        (None, "overrides"),
    ],
)
def test_invalid_override_structure_is_safe_and_produces_no_plan(
    tmp_path, overrides, field
):
    path, snapshot = project(tmp_path)
    snapshot["blueprints"][0]["model"]["overrides"] = overrides
    path.write_text(yaml.safe_dump(snapshot))
    result = CliRunner().invoke(app, ["plan", str(path), "--format", "json"])
    assert result.exit_code == 1, result.output
    assert field in result.output
    assert "SECRET_SENTINEL" not in result.output
    assert '"snapshot"' not in result.output


def test_inline_overrides_are_invalid_even_if_empty(tmp_path):
    path, snapshot = project(tmp_path)
    snapshot["blueprints"][0]["model"] = {
        "provider": "ollama",
        "name": "base",
        "overrides": {},
    }
    path.write_text(yaml.safe_dump(snapshot))
    with pytest.raises(SpecificationError, match="overrides"):
        compile_experiment(path)


def test_programmatic_reference_roundtrip_preserves_omitted_override_fields(tmp_path):
    path, snapshot = project(tmp_path)
    snapshot["blueprints"][0]["model"]["overrides"] = {"parameters": {"think": False}}
    experiment = Experiment.model_validate(snapshot)
    restored = Experiment.model_validate_json(experiment.model_dump_json(by_alias=True))
    plan = compile_experiment(resolve_experiment(restored, source=path))
    assert plan.snapshot["blueprints"][0]["model"]["parameters"] == {"think": False}


@pytest.mark.parametrize(
    "entrypoint",
    [None, "mininet_ai.agents.agno.ollama_factory:create_prompt_parsed_agent"],
)
@pytest.mark.parametrize("invalid_base", [False, True])
@pytest.mark.parametrize(
    "parameter,value",
    [
        ("think", None),
        ("think", "SECRET_SENTINEL"),
        ("host", None),
        ("host", "http://user:SECRET_SENTINEL@host"),
    ],
)
def test_base_and_effective_models_are_validated_independently(
    tmp_path, entrypoint, invalid_base, parameter, value
):
    path, snapshot = project(tmp_path)
    valid = False if parameter == "think" else "http://localhost:11434"
    for blueprint in snapshot["blueprints"]:
        if entrypoint:
            blueprint["implementation"] = {"type": "python", "entrypoint": entrypoint}
        blueprint["model"]["overrides"] = {
            "parameters": {parameter: valid if invalid_base else value}
        }
    write_project(
        path,
        snapshot,
        {
            "provider": "ollama",
            "name": "base",
            "parameters": {parameter: value if invalid_base else valid},
        },
    )
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(path)
    message = str(caught.value)
    for context in ("local-router", "models.yaml", "shared", parameter):
        assert context in message
    if parameter == "think" or not invalid_base:
        assert ("base" if invalid_base else "effective") in message
    assert "SECRET_SENTINEL" not in message


def test_overrides_cannot_repair_incomplete_or_credential_bases(tmp_path):
    path, snapshot = project(tmp_path)
    for blueprint in snapshot["blueprints"]:
        blueprint["model"]["overrides"] = {"name": "repaired"}
    for base in (
        {"provider": "ollama"},
        {
            "provider": "ollama",
            "name": "base",
            "parameters": {"api_key": "SECRET_SENTINEL"},
        },
    ):
        write_project(path, snapshot, base)
        with pytest.raises(SpecificationError) as caught:
            compile_experiment(path)
        assert "SECRET_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize("python_factory", [False, True])
def test_override_literal_credentials_are_rejected_before_persistence(
    tmp_path, python_factory
):
    path, snapshot = project(tmp_path)
    for blueprint in snapshot["blueprints"]:
        blueprint["model"]["overrides"] = {"parameters": {"api_key": "SECRET_SENTINEL"}}
        if python_factory:
            blueprint["implementation"] = {
                "type": "python",
                "entrypoint": "not_imported:factory",
            }
    path.write_text(yaml.safe_dump(snapshot))
    for command in ("validate", "plan"):
        result = CliRunner().invoke(app, [command, str(path)])
        assert result.exit_code == 1, result.output
        for context in (
            "local-router",
            "models.yaml",
            "shared",
            "effective",
            "parameters.api_key",
        ):
            assert context in result.output
        assert "SECRET_SENTINEL" not in result.output


def test_programmatic_source_and_provenance_do_not_own_effective_snapshot_values(
    tmp_path,
):
    path, snapshot = project(tmp_path)
    for blueprint in snapshot["blueprints"]:
        blueprint["implementation"] = {
            "type": "python",
            "entrypoint": "not_imported:factory",
        }
        blueprint["model"]["overrides"] = {"parameters": {"nested": {"key": [1, None]}}}
    experiment = Experiment.model_validate(snapshot)
    plan = compile_experiment(resolve_experiment(experiment, source=path))
    before = deepcopy(plan.snapshot)
    original_digest = plan.digest
    # Mutating informational provenance cannot alter executable configuration.
    plan.model_provenance["local-router"].overrides["parameters"]["nested"][
        "key"
    ].append(2)
    assert plan.snapshot == before
    assert plan.digest == original_digest
    # Nor can editing the programmatic source rewrite the captured provenance.
    second = experiment.blueprints[1]
    assert not isinstance(second, str)
    assert isinstance(second.model, ModelReference)
    assert "parameters" in second.model.overrides
    second.model.overrides["parameters"]["nested"]["key"].append(3)
    assert plan.model_provenance["second-router"].overrides == {
        "parameters": {"nested": {"key": [1, None]}}
    }
    assert plan.snapshot == before


def test_equivalent_inline_reference_and_override_expressions_have_equal_identity(
    tmp_path,
):
    path, snapshot = project(tmp_path)
    effective = {
        "provider": "ollama",
        "name": "selected",
        "parameters": {"think": True},
    }
    write_project(path, snapshot, effective)
    reference = compile_experiment(path)
    for blueprint in snapshot["blueprints"]:
        blueprint["model"]["overrides"] = {
            "name": "selected",
            "parameters": {"think": True},
        }
    write_project(
        path,
        snapshot,
        {"provider": "ollama", "name": "base", "parameters": {"think": False}},
    )
    overridden = compile_experiment(path)
    for blueprint in snapshot["blueprints"]:
        blueprint["model"] = effective
    path.write_text(yaml.safe_dump(snapshot))
    inline = compile_experiment(path)
    assert reference.snapshot == overridden.snapshot == inline.snapshot
    assert reference.digest == overridden.digest == inline.digest
    assert reference.model_provenance != overridden.model_provenance
    assert not inline.model_provenance


@pytest.mark.parametrize("python_factory", [False, True])
def test_two_overridden_models_execute_offline_after_source_edit_and_removal(
    tmp_path, python_factory
):
    path, snapshot = project(tmp_path)
    agent = deepcopy(
        next(agent for agent in snapshot["agents"] if agent["name"] == "switch-router")
    )
    agent.update(name="second-switch-router", blueprint="second-router")
    snapshot["agents"].append(agent)
    parameter = "message" if python_factory else "response"
    base_value = "base response" if python_factory else {"message": "base response"}
    override_value = (
        "override response" if python_factory else {"message": "override response"}
    )
    if python_factory:
        for blueprint in snapshot["blueprints"]:
            blueprint["implementation"] = {
                "type": "python",
                "entrypoint": "tests.agents.agno_helpers:consuming_model_factory",
            }
    snapshot["blueprints"][1]["model"]["overrides"] = {
        "name": "overridden",
        "parameters": {parameter: override_value},
    }
    base = {
        "provider": "custom" if python_factory else "mock",
        "name": "base",
        "parameters": {parameter: base_value},
    }
    write_project(path, snapshot, base)
    with patch(
        "tests.agents.agno_helpers.consuming_model_factory",
        side_effect=AssertionError("compile invoked factory"),
    ):
        plan = compile_experiment(path)
    serialized = plan.model_dump_json(by_alias=True)
    base["name"] = "later"
    write_project(path, snapshot, base)
    assert (
        compile_experiment(path).snapshot["blueprints"][0]["model"]["name"] == "later"
    )
    path.with_name("models.yaml").unlink()
    path.unlink()
    with patch(
        "mininet_ai.specification.model_sources.read_model_configurations",
        side_effect=AssertionError("source read"),
    ):
        catalog = ExecutionCatalog(DeploymentPlan.model_validate_json(serialized))
        for agent_id, message, model_id in (
            ("switch-router@s1", "base response", "base"),
            ("second-switch-router@s1", "override response", "overridden"),
        ):
            definition = catalog.resolve(agent_id)
            attachment = definition.instance.attachment
            context = AgentContext(
                invocationId="offline",
                runId="saved",
                agentId=agent_id,
                deployment=definition.instance.deployment,
                layer=attachment.layer,
                targetKind=attachment.target_kind,
                targets=attachment.targets,
                capabilities=definition.instance.capabilities,
                observations={},
                intent="Inspect offline",
                priority=0,
                invokedAt=datetime(2026, 1, 1, tzinfo=UTC),
            )
            factory = AgnoAgentFactory()
            assert (
                AgnoAgentProvider(definition, factory=factory).invoke(context).message
                == message
            )
            model = definition.blueprint.model
            assert isinstance(model, ModelConfiguration)
            assert model.name == model_id
            if python_factory:
                constructed = factory.create(definition).model
                assert constructed is not None
                assert constructed.id == model_id


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("credential", ["", "SECRET_SENTINEL"])
def test_saved_overrides_retain_actual_agno_environment_endpoint_behavior(
    tmp_path, monkeypatch, explicit, credential
):
    path, snapshot = project(tmp_path)
    for blueprint in snapshot["blueprints"]:
        blueprint["model"]["overrides"] = {
            "name": "overridden",
            "parameters": {"think": False},
        }
        if explicit:
            blueprint["model"]["overrides"]["parameters"]["host"] = (
                "http://configured.example:11434"
            )
    path.write_text(yaml.safe_dump(snapshot))
    with patch("os.getenv", side_effect=AssertionError("compile read environment")):
        serialized = compile_experiment(path).model_dump_json(by_alias=True)
    assert "SECRET_SENTINEL" not in serialized
    path.with_name("models.yaml").unlink()
    monkeypatch.setenv("OLLAMA_HOST", "http://runtime.example:11434")
    monkeypatch.setenv("OLLAMA_API_KEY", credential)
    definition = ExecutionCatalog(
        DeploymentPlan.model_validate_json(serialized)
    ).resolve("switch-router@s1")
    model = AgnoAgentFactory().create(definition).model
    assert isinstance(model, Ollama)
    assert model.id == "overridden"
    assert model.get_request_params()["think"] is False
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"models": []})

    model.client_params = {"transport": httpx.MockTransport(respond)}
    model.get_client().list()
    expected = (
        "http://configured.example:11434"
        if explicit
        else "https://ollama.com"
        if credential
        else "http://runtime.example:11434"
    )
    assert str(requests[0].url) == expected + "/api/tags"
    if credential:
        assert requests[0].headers["authorization"] == "Bearer SECRET_SENTINEL"
    assert os.environ["OLLAMA_HOST"] == "http://runtime.example:11434"
    assert os.environ["OLLAMA_API_KEY"] == credential
