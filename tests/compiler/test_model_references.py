"""Shared model authoring through the compiler and saved execution seams."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from mininet_ai.agents import AgnoAgentFactory, AgnoAgentProvider
from mininet_ai.cli import app
from mininet_ai.compiler import compile_experiment
from mininet_ai.compiler.models import DeploymentPlan
from mininet_ai.errors import AgentRuntimeError, SpecificationError
from mininet_ai.sdk import AgentContext
from mininet_ai.sdk.catalog import ExecutionCatalog
from mininet_ai.specification.loader import load_experiment, resolve_experiment
from mininet_ai.specification.models import (
    Experiment,
    ModelConfiguration,
    ModelReference,
)
from tests.compiler.helpers import compiler_multilayer_snapshot


def project(tmp_path: Path) -> tuple[Path, dict]:
    snapshot = compiler_multilayer_snapshot()
    blueprint = snapshot["blueprints"][0]
    blueprint["model"] = {"file": "models.yaml", "entry": "shared"}
    second = deepcopy(blueprint)
    second["metadata"]["name"] = "second-router"
    snapshot["blueprints"].append(second)
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(snapshot))
    (tmp_path / "models.yaml").write_text(
        "models:\n  shared:\n    provider: ollama\n    name: llama3.2:3b\n"
    )
    return path, snapshot


def test_shared_models_are_resolved_saved_and_source_independent(
    tmp_path: Path,
) -> None:
    path, snapshot = project(tmp_path)
    referenced = compile_experiment(path)
    effective = {"provider": "ollama", "name": "llama3.2:3b", "parameters": {}}
    for blueprint in snapshot["blueprints"]:
        blueprint["model"] = effective
    path.write_text(yaml.safe_dump(snapshot))
    inline = compile_experiment(path)
    assert referenced.snapshot == inline.snapshot
    assert referenced.digest == inline.digest
    assert referenced.model_provenance
    assert not inline.model_provenance
    (tmp_path / "models.yaml").unlink()
    saved = DeploymentPlan.model_validate_json(
        referenced.model_dump_json(by_alias=True)
    )
    catalog = ExecutionCatalog(saved)
    model = catalog.resolve(catalog.agent_ids[0]).blueprint.model
    assert isinstance(model, ModelConfiguration)
    assert model.model_dump() == effective


def test_invalid_unused_entry_is_rejected_with_safe_source_location(
    tmp_path: Path,
) -> None:
    path, _ = project(tmp_path)
    (tmp_path / "models.yaml").write_text(
        "models:\n  shared: {provider: ollama, name: valid}\n"
        "  unused:\n    provider: ollama\n    name: other\n"
        "    parameters: {host: 'http://user:SECRET_SENTINEL@localhost'}\n"
    )
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(path)
    message = str(caught.value)
    for context in ("local-router", "models.yaml", "shared", "models.unused", "line 3"):
        assert context in message
    assert "SECRET_SENTINEL" not in message


def test_duplicate_nested_parameter_keys_report_both_locations(tmp_path: Path) -> None:
    path, _ = project(tmp_path)
    (tmp_path / "models.yaml").write_text(
        "models:\n  shared:\n    provider: ollama\n    name: model\n"
        "    parameters:\n      host: SECRET_SENTINEL\n      host: other\n"
    )
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(path)
    message = str(caught.value)
    for context in (
        "duplicate",
        "models.shared.parameters.host",
        "line 6",
        "line 7",
        "column 7",
    ):
        assert context in message
    assert "SECRET_SENTINEL" not in message


def test_sdk_rejects_unresolved_models_even_in_unused_blueprints(
    tmp_path: Path,
) -> None:
    path, _ = project(tmp_path)
    plan = compile_experiment(path)
    plan.snapshot["blueprints"][1]["model"] = {
        "file": "removed.yaml",
        "entry": "shared",
    }
    with pytest.raises(AgentRuntimeError, match="unresolved model"):
        ExecutionCatalog(plan)


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("absolute", [False, True])
def test_document_relative_paths_survive_cwd_changes(
    tmp_path, monkeypatch, external, absolute
):
    path, snapshot = project(tmp_path)
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    # A decoy at the experiment origin catches external-blueprint path mistakes.
    target = definitions / "models.yaml" if external else tmp_path / "models.yaml"
    target.write_text("models: {shared: {provider: ollama, name: selected}}")
    for index, blueprint in enumerate(snapshot["blueprints"]):
        blueprint["model"]["file"] = (
            str(target)
            if absolute
            else "../definitions/models.yaml"
            if external
            else "./models.yaml"
        )
        if external:
            blueprint_path = definitions / f"agent-{index}.yaml"
            blueprint_path.write_text(yaml.safe_dump(blueprint))
            snapshot["blueprints"][index] = f"definitions/agent-{index}.yaml"
    path.write_text(yaml.safe_dump(snapshot))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    plan = compile_experiment(path)
    assert all(b["model"]["name"] == "selected" for b in plan.snapshot["blueprints"])
    assert all(p.file == str(target) for p in plan.model_provenance.values())


def test_programmatic_source_location_and_cwd_fallback(tmp_path, monkeypatch):
    path, snapshot = project(tmp_path)
    experiment = Experiment.model_validate(snapshot)
    explicit = compile_experiment(resolve_experiment(experiment, source=path))
    monkeypatch.chdir(tmp_path)
    implicit = compile_experiment(experiment)
    assert explicit.snapshot == implicit.snapshot
    assert explicit.digest == implicit.digest


def test_one_read_per_compilation_but_later_compilation_observes_edits(
    tmp_path, monkeypatch
):
    path, snapshot = project(tmp_path)
    # Canonical aliases share one parsed source.
    snapshot["blueprints"][1]["model"]["file"] = str(tmp_path / "models.yaml")
    path.write_text(yaml.safe_dump(snapshot))
    loaded = load_experiment(path)
    original_open = Path.open
    reads = []

    @contextmanager
    def changing_open(file, *args, **kwargs):
        with original_open(file, *args, **kwargs) as stream:
            yield stream
        if file == tmp_path / "models.yaml" and "w" not in args:
            reads.append(file)
            with original_open(file, "w") as stream:
                stream.write("models: {shared: {provider: ollama, name: changed}}")

    monkeypatch.setattr(Path, "open", changing_open)
    first = compile_experiment(loaded)
    assert len(reads) == 1
    assert {b["model"]["name"] for b in first.snapshot["blueprints"]} == {"llama3.2:3b"}
    second = compile_experiment(loaded)
    assert len(reads) == 2
    assert {b["model"]["name"] for b in second.snapshot["blueprints"]} == {"changed"}
    assert isinstance(loaded.blueprints[0].model, ModelReference)
    assert loaded.blueprints[0].model.file == "models.yaml"


@pytest.mark.parametrize(
    "file",
    [
        "https://user:SECRET_SENTINEL@host/models.yaml",
        "~/models.yaml",
        "$MODELS/file.yaml",
        "${MODELS}/file.yaml",
    ],
)
def test_unsupported_path_forms_are_rejected_safely(tmp_path, file):
    path, snapshot = project(tmp_path)
    snapshot["blueprints"][0]["model"]["file"] = file
    path.write_text(yaml.safe_dump(snapshot))
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(path)
    assert "local path" in str(caught.value)
    assert "SECRET_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize(
    "model",
    [
        {"file": "models.yaml"},
        {"entry": "shared"},
        {
            "file": "models.yaml",
            "entry": "shared",
            "provider": "ollama",
            "name": "mixed",
        },
        {"file": "models.yaml", "entry": "shared", "overrides": {}},
    ],
)
def test_incomplete_mixed_and_override_forms_are_rejected(tmp_path, model):
    path, snapshot = project(tmp_path)
    snapshot["blueprints"][0]["model"] = model
    path.write_text(yaml.safe_dump(snapshot))
    with pytest.raises(SpecificationError, match="model"):
        compile_experiment(path)


@pytest.mark.parametrize(
    "text,field",
    [
        ("models: {}\nkind: SECRET_SENTINEL", "kind"),
        ("models: {shared: {provider: ollama}}", "name"),
        (
            "models: {shared: {provider: ollama, name: valid, extra: SECRET_SENTINEL}}",
            "extra",
        ),
        ("models: {shared: {file: models.yaml, entry: shared}}", "provider"),
        (
            "models: {shared: {provider: ollama, name: valid, parameters: null}}",
            "parameters",
        ),
        (
            "models: {shared: {provider: ollama, name: valid}, 'bad/name': {provider: ollama, name: valid}}",
            "bad/name",
        ),
        (
            "models: {shared: {provider: ollama, name: valid}, '': {provider: ollama, name: valid}}",
            "models",
        ),
    ],
)
def test_whole_file_structure_is_strict_and_safe(tmp_path, text, field):
    path, _ = project(tmp_path)
    (tmp_path / "models.yaml").write_text(text)
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(path)
    assert field in str(caught.value)
    assert "line" in str(caught.value)
    assert "SECRET_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize(
    "text",
    [
        "models: {}\nmodels: {}\n",
        "models:\n  shared: {provider: ollama, name: a}\n  shared: {provider: ollama, name: b}\n",
        "models: {shared: {provider: ollama, provider: mock, name: a}}",
        "models: {shared: {provider: ollama, name: a, parameters: {nested: [{x: 1, x: 2}]}}}",
        "models: {shared: {provider: ollama, name: a}, unused: {provider: ollama, name: b, parameters: {x: 1, x: 2}}}",
        "models: {shared: {<<: &base {provider: ollama, name: a}, name: b}}",
    ],
)
def test_duplicate_keys_at_any_depth_are_rejected(tmp_path, text):
    path, _ = project(tmp_path)
    (tmp_path / "models.yaml").write_text(text)
    with pytest.raises(
        SpecificationError, match="duplicate.*line.*first defined at.*line"
    ):
        compile_experiment(path)


@pytest.mark.parametrize(
    "text",
    [
        "models: [SECRET_SENTINEL",
        "models: !unknown SECRET_SENTINEL",
        "? [a, b]\n: SECRET_SENTINEL",
        "models: &recursive {shared: *recursive}",
    ],
)
def test_malformed_and_unconstructable_yaml_is_safe(tmp_path, text):
    path, _ = project(tmp_path)
    (tmp_path / "models.yaml").write_text(text)
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(path)
    assert "SECRET_SENTINEL" not in str(caught.value)
    assert "local-router" in str(caught.value)


def test_missing_entry_file_and_unreadable_file_are_contextual(tmp_path, monkeypatch):
    path, _ = project(tmp_path)
    target = tmp_path / "models.yaml"
    target.write_text("models: {other: {provider: ollama, name: model}}")
    with pytest.raises(
        SpecificationError, match="shared.*selected entry does not exist"
    ):
        compile_experiment(path)
    target.unlink()
    with pytest.raises(
        SpecificationError, match="local-router.*models.yaml.*shared.*cannot read"
    ):
        compile_experiment(path)
    original = Path.open

    def unreadable(file, *args, **kwargs):
        if file == target:
            raise PermissionError("SECRET_SENTINEL")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unreadable)
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(path)
    assert "cannot read" in str(caught.value)
    assert "SECRET_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize("python_factory", [False, True])
def test_saved_models_execute_offline_after_sources_move(
    tmp_path, monkeypatch, python_factory
):
    path, snapshot = project(tmp_path)
    model = {
        "provider": "mock",
        "name": "saved-model",
        "parameters": {"response": {"message": "saved response"}},
    }
    if python_factory:
        model = {
            "provider": "custom",
            "name": "saved-model",
            "parameters": {
                "message": "saved response",
                "custom-option": {"any": [1, None]},
            },
        }
        for blueprint in snapshot["blueprints"]:
            blueprint["implementation"] = {
                "type": "python",
                "entrypoint": "tests.agents.agno_helpers:consuming_model_factory",
            }
    path.write_text(yaml.safe_dump(snapshot))
    (tmp_path / "models.yaml").write_text(yaml.safe_dump({"models": {"shared": model}}))
    with patch(
        "tests.agents.agno_helpers.consuming_model_factory",
        side_effect=AssertionError("compiler invoked factory"),
    ):
        plan = compile_experiment(path)
    serialized = plan.model_dump_json(by_alias=True)
    (tmp_path / "models.yaml").rename(tmp_path / "moved.yaml")

    def forbidden(*args, **kwargs):
        pytest.fail("saved execution attempted to load model sources")

    monkeypatch.setattr(
        "mininet_ai.specification.model_sources.read_model_configurations", forbidden
    )
    catalog = ExecutionCatalog(DeploymentPlan.model_validate_json(serialized))
    definition = catalog.resolve("switch-router@s1")
    attachment = definition.instance.attachment
    context = AgentContext(
        invocationId="invoke-saved",
        runId="run-saved",
        agentId=definition.instance.id,
        deployment=definition.instance.deployment,
        layer=attachment.layer,
        customLayer=attachment.custom_layer,
        targetKind=attachment.target_kind,
        targets=attachment.targets,
        capabilities=definition.instance.capabilities,
        observations={},
        intent="Inspect offline",
        priority=definition.instance.priority,
        invokedAt=datetime(2026, 1, 1, tzinfo=UTC),
    )
    factory = AgnoAgentFactory()
    provider = AgnoAgentProvider(definition, factory=factory)
    assert provider.invoke(context).message == "saved response"
    if python_factory:
        from tests.agents.agno_helpers import StaticModel

        constructed = factory.create(definition).model
        assert isinstance(constructed, StaticModel)
        assert constructed.id == "saved-model"


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize("python_factory", [False, True])
def test_literal_credentials_are_rejected_without_value_disclosure(
    tmp_path, reference, python_factory
):
    path, snapshot = project(tmp_path)
    model = {
        "provider": "ollama",
        "name": "model",
        "parameters": {"api_key": "SECRET_SENTINEL"},
    }
    for blueprint in snapshot["blueprints"]:
        if not reference:
            blueprint["model"] = model
        if python_factory:
            blueprint["implementation"] = {
                "type": "python",
                "entrypoint": "not_imported:factory",
            }
    path.write_text(yaml.safe_dump(snapshot))
    (tmp_path / "models.yaml").write_text(yaml.safe_dump({"models": {"shared": model}}))
    for command in (["validate", str(path)], ["plan", str(path), "--format", "json"]):
        result = CliRunner().invoke(app, command)
        assert result.exit_code == 1, result.output
        assert "api_key" in result.output
        assert "SECRET_SENTINEL" not in result.output
        assert '"kind": "DeploymentPlan"' not in result.output


def test_provenance_does_not_affect_executable_identity(tmp_path):
    path, _ = project(tmp_path)
    plan = compile_experiment(path)
    payload = plan.model_dump(mode="json", by_alias=True)
    payload["modelProvenance"]["local-router"]["file"] = "/another/location/models.yaml"
    changed = DeploymentPlan.model_validate(payload)
    assert changed.digest == plan.digest
    assert changed.snapshot == plan.snapshot
    assert ExecutionCatalog(changed).agent_ids == ExecutionCatalog(plan).agent_ids


def test_offline_compilation_does_not_lookup_credentials_or_construct_models(
    tmp_path, monkeypatch
):
    path, _ = project(tmp_path)
    monkeypatch.setenv("OLLAMA_API_KEY", "SECRET_SENTINEL")
    with (
        patch(
            "mininet_ai.agents.agno.provider._default_model_resolver",
            side_effect=AssertionError("constructed model"),
        ),
        patch("os.getenv", side_effect=AssertionError("read environment")),
    ):
        plan = compile_experiment(path)
    assert "SECRET_SENTINEL" not in plan.model_dump_json(by_alias=True)
    assert plan.snapshot["blueprints"][0]["model"]["parameters"] == {}


def test_recursive_yaml_merge_is_a_safe_specification_error(tmp_path):
    path, _ = project(tmp_path)
    (tmp_path / "models.yaml").write_text("models: &recursive {<<: *recursive}")
    with pytest.raises(SpecificationError, match="recursive YAML"):
        compile_experiment(path)


def test_unresolvable_symlink_is_a_contextual_specification_error(tmp_path):
    path, _ = project(tmp_path)
    target = tmp_path / "models.yaml"
    target.unlink()
    target.symlink_to("models.yaml")
    with pytest.raises(SpecificationError, match="local-router.*model.file"):
        compile_experiment(path)


@pytest.mark.parametrize("entry", ["a", "A.a_b-12", "a" * 128])
def test_configuration_identifiers_are_distinct_from_provider_model_ids(
    tmp_path, entry
):
    path, snapshot = project(tmp_path)
    for blueprint in snapshot["blueprints"]:
        blueprint["model"]["entry"] = entry
    path.write_text(yaml.safe_dump(snapshot))
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    entry: {"provider": "ollama", "name": "organization/model:tag"}
                }
            }
        )
    )
    plan = compile_experiment(path)
    assert all(
        b["model"]["name"] == "organization/model:tag"
        for b in plan.snapshot["blueprints"]
    )


@pytest.mark.parametrize("entry", ["a" * 129, "1start", "é", "a/b"])
def test_invalid_identifiers_in_unused_entries_are_rejected(tmp_path, entry):
    path, _ = project(tmp_path)
    model = {"provider": "ollama", "name": "valid"}
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump({"models": {"shared": model, entry: model}})
    )
    with pytest.raises(SpecificationError, match="models"):
        compile_experiment(path)


def test_cli_validates_and_serializes_resolved_references(tmp_path):
    path, _ = project(tmp_path)
    validated = CliRunner().invoke(app, ["validate", str(path)])
    assert validated.exit_code == 0, validated.output
    planned = CliRunner().invoke(app, ["plan", str(path), "--format", "json"])
    assert planned.exit_code == 0, planned.output
    plan = DeploymentPlan.model_validate_json(planned.stdout)
    assert plan.model_provenance
    assert all("file" not in b["model"] for b in plan.snapshot["blueprints"])


@pytest.mark.parametrize(
    "entrypoint",
    [
        None,
        "mininet_ai.agents.agno.ollama_factory:create_prompt_parsed_agent",
        "mininet_ai.agents.agno.ollama_factory:create_deterministic_prompt_parsed_agent",
    ],
)
def test_referenced_bases_use_source_only_construction_validation(tmp_path, entrypoint):
    path, snapshot = project(tmp_path)
    if entrypoint:
        for blueprint in snapshot["blueprints"]:
            blueprint["implementation"] = {"type": "python", "entrypoint": entrypoint}
    path.write_text(yaml.safe_dump(snapshot))
    (tmp_path / "models.yaml").write_text(
        "models: {shared: {provider: ollama, name: model, parameters: {think: SECRET_SENTINEL}}}"
    )
    result = CliRunner().invoke(app, ["plan", str(path), "--format", "json"])
    assert result.exit_code == 1, result.output
    for context in (
        "local-router",
        "models.yaml",
        "shared",
        "base",
        "parameters.think",
    ):
        assert context in result.output
    assert "SECRET_SENTINEL" not in result.output
    assert '"snapshot"' not in result.output


def test_shared_file_preserves_distinct_construction_ownership(tmp_path):
    path, snapshot = project(tmp_path)
    # One file can organize multiple Providers and owners. A declarative consumer
    # must not impose its parameter semantics on another factory's entry.
    snapshot["blueprints"][1]["implementation"] = {
        "type": "python",
        "entrypoint": "not_imported:custom_factory",
    }
    snapshot["blueprints"][1]["model"]["entry"] = "factory-owned"
    path.write_text(yaml.safe_dump(snapshot))
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "shared": {
                        "provider": "ollama",
                        "name": "shared",
                        "parameters": {"think": False},
                    },
                    "factory-owned": {
                        "provider": "custom",
                        "name": "factory-model",
                        "parameters": {"nested-option": {"any": None}},
                    },
                }
            }
        )
    )
    plan = compile_experiment(path)
    factory_model = plan.snapshot["blueprints"][1]["model"]
    assert factory_model["parameters"] == {"nested-option": {"any": None}}


def test_invalid_unused_literal_credentials_are_rejected_before_selection(tmp_path):
    path, _ = project(tmp_path)
    (tmp_path / "models.yaml").write_text(
        "models:\n  shared: {provider: ollama, name: model}\n"
        "  unused: {provider: custom, name: other, parameters: {api_key: SECRET_SENTINEL}}\n"
    )
    with pytest.raises(SpecificationError) as caught:
        compile_experiment(path)
    for context in ("unused", "parameters.api_key", "line 3", "shared", "local-router"):
        assert context in str(caught.value)
    assert "SECRET_SENTINEL" not in str(caught.value)
