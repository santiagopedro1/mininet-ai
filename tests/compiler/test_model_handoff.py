"""Cross-feature acceptance at the existing compiler, CLI and SDK seams."""

import re
from dataclasses import replace
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from mininet_ai.cli import app
from mininet_ai.compiler import compile_experiment
from mininet_ai.compiler.models import DeploymentPlan
from mininet_ai.errors import SpecificationError
from mininet_ai.sdk.catalog import ExecutionCatalog
from mininet_ai.specification.loader import load_experiment, resolve_experiment
from mininet_ai.specification.models import Experiment, SpecificationDeprecationWarning
from tests.compiler.test_model_references import project


@pytest.mark.parametrize("command", ["validate", "plan"])
@pytest.mark.parametrize(
    "tag,value",
    [
        ("bool", "SECRET_SENTINEL"),
        ("int", "SECRET_SENTINEL"),
        ("float", "SECRET_SENTINEL"),
        ("timestamp", "SECRET_SENTINEL"),
        ("binary", "SECRET_SENTINEL"),
        ("int", "''"),
        ("float", "''"),
    ],
)
def test_cli_rejects_invalid_yaml_scalars_in_unused_entries(
    tmp_path, command, tag, value
):
    path, _ = project(tmp_path)
    path.with_name("models.yaml").write_text(
        "models:\n  shared: {provider: ollama, name: valid}\n"
        "  unused:\n    provider: custom\n    name: other\n"
        f"    parameters:\n      option: !!{tag} {value}\n"
    )
    result = CliRunner().invoke(app, [command, str(path)])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    output = " ".join(result.output.split())
    for context in (
        "local-router",
        str(path),
        "models.yaml",
        "shared",
        "models.unused.parameters.option",
        "line 7, column 7",
        "invalid YAML",
    ):
        assert context in output
    assert "secret_sentinel" not in output.lower()
    assert "Traceback" not in output
    assert '"snapshot"' not in output


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("command", ["validate", "plan"])
def test_host_errors_identify_the_exact_field_without_disclosing_values(
    tmp_path, selected, command
):
    path, _ = project(tmp_path)
    entry = "shared" if selected else "unused"
    path.with_name("models.yaml").write_text(
        "models:\n"
        + ("" if selected else "  shared: {provider: ollama, name: valid}\n")
        + f"  {entry}:\n    provider: ollama\n    name: invalid\n"
        "    parameters:\n      host: http://user:SECRET_SENTINEL@localhost\n"
    )
    result = CliRunner().invoke(app, [command, str(path)])
    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    for context in (
        "local-router",
        "models.yaml",
        "shared",
        f"models.{entry}.parameters.host",
        "must not contain credentials",
    ):
        assert context in output
    assert re.search(
        rf"parameters.host at line {6 if selected else 7}, column 7", output
    )
    assert "SECRET_SENTINEL" not in result.output
    assert '"snapshot"' not in result.output


@pytest.mark.parametrize("command", ["validate", "plan"])
def test_effective_host_errors_point_to_the_supplied_override(tmp_path, command):
    path, snapshot = project(tmp_path)
    snapshot["blueprints"][0] = "agent.yaml"
    path.write_text(yaml.safe_dump(snapshot))
    (tmp_path / "agent.yaml").write_text(
        "apiVersion: mininet-ai/v1alpha4\nkind: AgentBlueprint\n"
        "metadata: {name: local-router}\nmodel:\n  file: models.yaml\n  entry: shared\n"
        "  overrides:\n    parameters:\n      host: http://user:SECRET_SENTINEL@localhost\n"
    )
    result = CliRunner().invoke(app, [command, str(path)])
    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    for context in (
        "local-router",
        "agent.yaml",
        "models.yaml",
        "shared",
        "effective",
        "model.overrides.parameters.host at line 9, column 7",
    ):
        assert context in output
    assert "SECRET_SENTINEL" not in output
    assert '"snapshot"' not in output


def test_programmatic_override_errors_do_not_claim_the_base_field_position(tmp_path):
    path, snapshot = project(tmp_path)
    snapshot["blueprints"][0]["model"]["overrides"] = {
        "parameters": {"think": "SECRET_SENTINEL"}
    }
    path.with_name("models.yaml").write_text(
        "models:\n  shared:\n    provider: ollama\n    name: base\n    parameters:\n      think: false\n"
    )
    loaded = resolve_experiment(Experiment.model_validate(snapshot), source=path)
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(loaded)
    assert "model.overrides.parameters.think: must be a boolean" in str(caught.value)
    assert "parameters.think at line 6" not in str(caught.value)
    assert "SECRET_SENTINEL" not in str(caught.value)


def test_loaded_experiment_legacy_defaults_and_replacement_preserve_resolution(
    tmp_path,
):
    path, _ = project(tmp_path)
    loaded = load_experiment(path)
    expected = compile_experiment(loaded)
    # Existing public construction can omit all per-blueprint source metadata.
    legacy = type(loaded)(
        loaded.source,
        loaded.experiment,
        loaded.topology,
        loaded.blueprints,
        loaded.capabilities,
    )
    assert compile_experiment(legacy).snapshot == expected.snapshot
    # dataclasses.replace remains supported and must not retain stale records.
    changed = loaded.blueprints[0].model_copy(update={"model": None})
    custom = changed.implementation.model_copy(
        update={"type": "python", "entrypoint": "not_installed:factory"}
    )
    changed = changed.model_copy(update={"implementation": custom})
    replaced = replace(legacy, blueprints=(changed, *legacy.blueprints[1:]))
    assert compile_experiment(replaced).snapshot["blueprints"][0]["model"] is None


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("command", ["validate", "plan"])
@pytest.mark.parametrize("model", ["SECRET_SENTINEL", ["SECRET_SENTINEL"]])
def test_nonobject_models_have_safe_source_context(tmp_path, external, command, model):
    path, snapshot = project(tmp_path)
    blueprint = snapshot["blueprints"][0]
    blueprint["model"] = model
    source = path
    if external:
        source = tmp_path / "agent.yaml"
        source.write_text(yaml.safe_dump(blueprint))
        snapshot["blueprints"][0] = "agent.yaml"
    path.write_text(yaml.safe_dump(snapshot))
    result = CliRunner().invoke(app, [command, str(path)])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    output = " ".join(result.output.split())
    for context in ("local-router", source.name, "model", "mapping"):
        assert context in output
    assert re.search(r"model at line [1-9]\d*, column [1-9]\d*", output)
    assert "SECRET_SENTINEL" not in output
    assert "ModelConfiguration" not in output
    assert "ModelReference" not in output
    assert '"snapshot"' not in output


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("command", ["validate", "plan"])
@pytest.mark.parametrize(
    "model,field,reason",
    [
        ({"overrides": {}}, "model.file", "Field required"),
        ({}, "model.provider", "Field required"),
        ({"file": "models.yaml"}, "model.entry", "Field required"),
        ({"entry": "shared"}, "model.file", "Field required"),
        (
            {"file": "models.yaml", "entry": "shared", "provider": "SECRET_SENTINEL"},
            "model.provider",
            "Extra inputs",
        ),
        (
            {
                "file": "models.yaml",
                "entry": "shared",
                "overrides": {"provider": "SECRET_SENTINEL"},
            },
            "model.overrides.provider",
            "Extra inputs",
        ),
        (
            {"file": "models.yaml", "entry": "shared", "unknown": "SECRET_SENTINEL"},
            "model.unknown",
            "Extra inputs",
        ),
    ],
)
def test_reference_shape_errors_are_actionable_at_the_source(
    tmp_path, external, command, model, field, reason
):
    path, snapshot = project(tmp_path)
    blueprint = snapshot["blueprints"][0]
    blueprint["model"] = model
    source = path
    if external:
        source = tmp_path / "agent.yaml"
        source.write_text(yaml.safe_dump(blueprint))
        snapshot["blueprints"][0] = "agent.yaml"
    path.write_text(yaml.safe_dump(snapshot))
    result = CliRunner().invoke(app, [command, str(path)])
    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    for context in ("local-router", source.name, field, reason):
        assert context in output
    if "file" in model:
        assert "models.yaml" in output
    if "entry" in model:
        assert "shared" in output
    assert re.search(r"line [1-9]\d*, column [1-9]\d*", output)
    assert "SECRET_SENTINEL" not in output
    assert '"snapshot"' not in output


def test_mixed_source_versions_compose_with_references_and_overrides(tmp_path):
    path, snapshot = project(tmp_path)
    snapshot["apiVersion"] = "mininet-ai/v1alpha3"
    first, second = snapshot["blueprints"]
    first["apiVersion"] = "mininet-ai/v1alpha4"
    second["apiVersion"] = "mininet-ai/v1alpha3"
    second["model"]["overrides"] = {"name": "overridden", "parameters": {"think": True}}
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    # Genuinely distinct parent directory, not traversal back into definitions.
    second["model"]["file"] = "../models.yaml"
    (definitions / "agent.yaml").write_text(yaml.safe_dump(second))
    snapshot["blueprints"][1] = "definitions/agent.yaml"
    snapshot["capabilityDefinitions"][0]["apiVersion"] = "mininet-ai/v1alpha3"
    path.write_text(yaml.safe_dump(snapshot))
    with pytest.warns(SpecificationDeprecationWarning) as warnings:
        loaded = load_experiment(path)
    assert len(warnings) == 3
    before = loaded.experiment.model_dump_json(by_alias=True)
    plan = compile_experiment(loaded)
    assert loaded.experiment.model_dump_json(by_alias=True) == before
    assert plan.api_version == "mininet-ai/v1alpha4"
    assert plan.snapshot["apiVersion"] == "mininet-ai/v1alpha4"
    assert {item["apiVersion"] for item in plan.snapshot["blueprints"]} == {
        "mininet-ai/v1alpha4"
    }
    assert plan.snapshot["blueprints"][1]["model"] == {
        "provider": "ollama",
        "name": "overridden",
        "parameters": {"think": True},
    }
    saved = DeploymentPlan.model_validate_json(plan.model_dump_json(by_alias=True))
    assert ExecutionCatalog(saved).agent_ids == ExecutionCatalog(plan).agent_ids


@pytest.mark.parametrize("command", ["validate", "plan"])
@pytest.mark.parametrize("invalid_base", [False, True])
@pytest.mark.parametrize("version", ["mininet-ai/v1alpha3", "mininet-ai/v1alpha4"])
def test_current_owner_checks_reject_invalid_base_and_effective_old_sources(
    tmp_path, command, invalid_base, version
):
    path, snapshot = project(tmp_path)
    snapshot["apiVersion"] = version
    for blueprint in snapshot["blueprints"]:
        blueprint["apiVersion"] = version
        blueprint["model"]["overrides"] = {
            "parameters": {"think": True if invalid_base else "SECRET_SENTINEL"}
        }
    path.write_text(yaml.safe_dump(snapshot))
    path.with_name("models.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "shared": {
                        "provider": "ollama",
                        "name": "base",
                        "parameters": {
                            "think": "SECRET_SENTINEL" if invalid_base else False
                        },
                    }
                }
            }
        )
    )
    result = CliRunner().invoke(app, [command, str(path)])
    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    for context in (
        "local-router",
        "models.yaml",
        "shared",
        "parameters.think",
        "base" if invalid_base else "effective",
        "must be a boolean",
    ):
        assert context in output
    assert "SECRET_SENTINEL" not in output
    assert '"snapshot"' not in output


@pytest.mark.parametrize("command", ["validate", "plan"])
@pytest.mark.parametrize(
    "text,field,positions",
    [
        ("models: {}\nmodels: {}\n", "models", [1, 2]),
        (
            "models:\n  shared: {provider: ollama, name: a}\n  shared: {provider: ollama, name: b}\n",
            "models.shared",
            [2, 3],
        ),
        (
            "models:\n  shared:\n    provider: ollama\n    name: base\n    parameters:\n      think: false\n      think: SECRET_SENTINEL\n",
            "models.shared.parameters.think",
            [6, 7],
        ),
        (
            "models: {shared: {provider: ollama, name: base}}\nunknown: SECRET_SENTINEL\n",
            "unknown",
            [2],
        ),
        (
            "models: {shared: {provider: ollama, name: base, unknown: SECRET_SENTINEL}}\n",
            "models.shared.unknown",
            [1],
        ),
        ("models: [SECRET_SENTINEL\n", "invalid YAML", [2]),
    ],
)
def test_cli_shared_file_error_matrix_is_safe_and_locatable(
    tmp_path, command, text, field, positions
):
    path, _ = project(tmp_path)
    path.with_name("models.yaml").write_text(text)
    result = CliRunner().invoke(app, [command, str(path)])
    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    for context in ("local-router", "models.yaml", "shared", field):
        assert context in output
    if "duplicate" in output:
        assert "first defined at" in output
    for line in positions:
        assert re.search(rf"line {line}, column [1-9]\d*", output)
    assert "SECRET_SENTINEL" not in output
    assert '"snapshot"' not in output


@pytest.mark.parametrize("command", ["validate", "plan"])
@pytest.mark.parametrize(
    "field", ["api_key", "api-key", "token", "password", "secret", "credentials"]
)
def test_cli_rejects_all_literal_credential_fields_in_unused_entries(
    tmp_path, command, field
):
    path, _ = project(tmp_path)
    path.with_name("models.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "shared": {"provider": "ollama", "name": "base"},
                    "unused": {
                        "provider": "custom",
                        "name": "other",
                        "parameters": {field: "SECRET_SENTINEL"},
                    },
                }
            }
        )
    )
    result = CliRunner().invoke(app, [command, str(path)])
    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    for context in (
        "local-router",
        "models.yaml",
        "shared",
        "unused",
        f"parameters.{field}",
    ):
        assert context in output
    assert re.search(r"line [1-9]\d*, column [1-9]\d*", output)
    assert "SECRET_SENTINEL" not in output
    assert '"snapshot"' not in output


@pytest.mark.parametrize("invalid_base", [False, True])
@pytest.mark.parametrize("parameter", ["response", "usage"])
def test_mock_base_and_effective_checks_compose_offline(
    tmp_path, invalid_base, parameter
):
    path, snapshot = project(tmp_path)
    valid = {"message": "retained"} if parameter == "response" else {}
    invalid = {"unknown": "SECRET_SENTINEL"}
    base = {
        "provider": "mock",
        "name": "offline",
        "parameters": {
            "response": {"message": "retained"},
            "usage": {},
        },
    }
    base["parameters"][parameter] = invalid if invalid_base else valid
    snapshot["blueprints"][0]["model"]["overrides"] = {
        "parameters": {parameter: valid if invalid_base else invalid}
    }
    path.write_text(yaml.safe_dump(snapshot))
    path.with_name("models.yaml").write_text(
        yaml.safe_dump({"models": {"shared": base}})
    )
    with (
        patch("os.getenv", side_effect=AssertionError("credential lookup")),
        patch(
            "httpx.Client.__init__", side_effect=AssertionError("client construction")
        ),
        patch("importlib.import_module", side_effect=AssertionError("factory import")),
        pytest.raises(SpecificationError) as caught,
    ):
        compile_experiment(path)
    message = str(caught.value)
    for context in (
        "local-router",
        "models.yaml",
        "shared",
        f"parameters.{parameter}",
        "base" if invalid_base else "effective",
    ):
        assert context in message
    assert "SECRET_SENTINEL" not in message


@pytest.mark.parametrize(
    "factory",
    ["create_prompt_parsed_agent", "create_deterministic_prompt_parsed_agent"],
)
def test_reference_overrides_preserve_bundled_factory_provider_restrictions(
    tmp_path, factory
):
    path, snapshot = project(tmp_path)
    snapshot["blueprints"][0]["implementation"] = {
        "type": "python",
        "entrypoint": f"mininet_ai.agents.agno.ollama_factory:{factory}",
    }
    snapshot["blueprints"][0]["model"]["overrides"] = {"name": "not-a-provider-change"}
    path.write_text(yaml.safe_dump(snapshot))
    path.with_name("models.yaml").write_text(
        "models: {shared: {provider: mock, name: base, parameters: {response: {message: ok}}}}"
    )
    with (
        patch("importlib.import_module", side_effect=AssertionError("factory import")),
        pytest.raises(
            SpecificationError, match="base.*provider.*requires an Ollama model"
        ),
    ):
        compile_experiment(path)


def test_shared_overrides_keep_custom_parameters_and_optional_models_offline(tmp_path):
    path, snapshot = project(tmp_path)
    for blueprint in snapshot["blueprints"]:
        blueprint["implementation"] = {
            "type": "python",
            "entrypoint": "not_installed:factory",
        }
    snapshot["blueprints"][0]["model"]["overrides"] = {
        "parameters": {"nested": {"replacement": [None]}, "nullable": None}
    }
    snapshot["blueprints"][1].pop("model")
    path.write_text(yaml.safe_dump(snapshot))
    path.with_name("models.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "shared": {
                        "provider": "ollama",
                        "name": "factory-owned",
                        "parameters": {
                            "think": "owned",
                            "nested": {"old": True},
                            "nullable": "old",
                            "keep": [1],
                        },
                    }
                }
            }
        )
    )
    with (
        patch("os.getenv", side_effect=AssertionError("credential lookup")),
        patch(
            "httpx.Client.__init__", side_effect=AssertionError("client construction")
        ),
        patch(
            "agno.models.ollama.Ollama.__init__",
            side_effect=AssertionError("model construction"),
        ),
        patch("importlib.import_module", side_effect=AssertionError("factory import")),
    ):
        saved = DeploymentPlan.model_validate_json(
            compile_experiment(path).model_dump_json(by_alias=True)
        )
    assert saved.snapshot["blueprints"][0]["model"]["parameters"] == {
        "think": "owned",
        "nested": {"replacement": [None]},
        "nullable": None,
        "keep": [1],
    }
    assert saved.snapshot["blueprints"][1]["model"] is None
