from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from mininet_ai.agents import AgnoAgentFactory
from mininet_ai.cli import app
from mininet_ai.compiler import DeploymentPlan, compile_experiment
from mininet_ai.errors import AgentRuntimeError, SpecificationError
from mininet_ai.sdk import AgentProviderError, ExecutionCatalog
from mininet_ai.specification.models import Experiment, SpecificationDeprecationWarning

LEGACY_PLAN = Path(__file__).parent / "fixtures/plans/v1alpha3-deployment-plan.json"


def original_snapshot():
    return json.loads(LEGACY_PLAN.read_text())["snapshot"]


def record_snapshot_digest(payload):
    canonical = json.dumps(payload["snapshot"], sort_keys=True, separators=(",", ":"))
    payload["digest"] = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def test_native_inline_source_compiles_serializes_and_reconstructs(tmp_path):
    snapshot = original_snapshot()
    for document in (
        snapshot,
        *snapshot["blueprints"],
        *snapshot["capabilityDefinitions"],
    ):
        document["apiVersion"] = "mininet-ai/v1alpha4"
    source = tmp_path / "experiment.yaml"
    source.write_text(yaml.safe_dump(snapshot))

    plan = compile_experiment(source)
    restored = DeploymentPlan.model_validate_json(plan.model_dump_json(by_alias=True))
    source.unlink()

    assert restored.api_version == "mininet-ai/v1alpha4"
    assert restored.snapshot == snapshot
    model = ExecutionCatalog(restored).resolve("switch-router@s1").blueprint.model
    assert model is not None
    assert model.name == "llama3.1:8b"


@pytest.mark.parametrize(
    "external_version", ["mininet-ai/v1alpha3", "mininet-ai/v1alpha4"]
)
@pytest.mark.parametrize(
    "source_version", ["mininet-ai/v1alpha3", "mininet-ai/v1alpha4"]
)
@pytest.mark.parametrize("reference_key", ["blueprints", "capabilityDefinitions"])
def test_old_sources_warn_normalize_and_resolve_mixed_definitions(
    tmp_path, external_version, source_version, reference_key
):
    snapshot = original_snapshot()
    snapshot["apiVersion"] = source_version
    reference = snapshot[reference_key][0]
    reference["apiVersion"] = external_version
    (tmp_path / "definition.yaml").write_text(yaml.safe_dump(reference))
    snapshot[reference_key] = ["definition.yaml"]
    source = tmp_path / "experiment.yaml"
    source.write_text(yaml.safe_dump(snapshot))

    with pytest.warns(SpecificationDeprecationWarning, match="v1alpha3.*v1alpha4"):
        plan = compile_experiment(source)

    assert plan.api_version == "mininet-ai/v1alpha4"
    assert plan.snapshot["apiVersion"] == "mininet-ai/v1alpha4"
    assert plan.snapshot["blueprints"][0]["apiVersion"] == "mininet-ai/v1alpha4"
    assert (
        plan.snapshot["capabilityDefinitions"][0]["apiVersion"] == "mininet-ai/v1alpha4"
    )
    assert ExecutionCatalog(plan).agent_ids


def test_programmatic_old_sources_normalize_without_mutating_input():
    snapshot = original_snapshot()
    with pytest.warns(SpecificationDeprecationWarning, match="v1alpha3.*v1alpha4"):
        experiment = Experiment.model_validate(snapshot)
    assert experiment.api_version == "mininet-ai/v1alpha4"
    assert snapshot["apiVersion"] == "mininet-ai/v1alpha3"


@pytest.mark.parametrize("key", [None, "blueprints", "capabilityDefinitions"])
def test_old_source_unknown_fields_are_not_discarded(tmp_path, key):
    snapshot = original_snapshot()
    document = snapshot if key is None else snapshot[key][0]
    document["unsupported"] = True
    source = tmp_path / "experiment.yaml"
    source.write_text(yaml.safe_dump(snapshot))
    with pytest.raises(SpecificationError, match="unsupported"):
        compile_experiment(source)


def test_original_saved_plan_reconstructs_without_sources_or_normalization(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("saved execution must not compile or load sources")

    monkeypatch.setattr("mininet_ai.compiler.compiler.compile_experiment", forbidden)
    monkeypatch.setattr("mininet_ai.specification.loader._read_yaml", forbidden)
    original = json.loads(LEGACY_PLAN.read_text())
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always")
        plan = DeploymentPlan.model_validate(original)
        catalog = ExecutionCatalog(plan)
        definition = catalog.resolve("switch-router@s1")

    assert not observed
    assert plan.api_version == "mininet-ai/v1alpha3"
    assert plan.snapshot == original["snapshot"]
    assert plan.digest == original["digest"]
    assert catalog.plan_digest == original["digest"]
    assert definition.blueprint.api_version == "mininet-ai/v1alpha3"
    serialized = json.loads(plan.model_dump_json(by_alias=True))
    assert serialized["snapshot"] == original["snapshot"]
    assert serialized["digest"] == original["digest"]


@pytest.mark.parametrize(
    "damage", ["digest", "plan-shape", "snapshot-shape", "snapshot-version"]
)
def test_original_saved_plan_rejects_invalid_shape_or_digest(damage):
    original = json.loads(LEGACY_PLAN.read_text())
    if damage == "digest":
        original["digest"] = "sha256:" + "0" * 64
    elif damage == "plan-shape":
        original["unexpected"] = True
    elif damage == "snapshot-shape":
        original["snapshot"]["blueprints"][0]["unexpected"] = True
        record_snapshot_digest(original)
    else:
        original["snapshot"]["apiVersion"] = "mininet-ai/v1alpha4"
        record_snapshot_digest(original)

    with pytest.raises(ValidationError):
        DeploymentPlan.model_validate(original)


def test_legacy_parameter_construction_failures_stay_runtime_failures():
    original = json.loads(LEGACY_PLAN.read_text())
    original["snapshot"]["blueprints"][0]["model"]["parameters"] = {"think": "invalid"}
    record_snapshot_digest(original)

    plan = DeploymentPlan.model_validate(original)
    definition = ExecutionCatalog(plan).resolve("switch-router@s1")
    assert definition.blueprint.model is not None
    assert definition.blueprint.model.parameters == {"think": "invalid"}
    with pytest.raises(AgentProviderError, match="think"):
        AgnoAgentFactory().create(definition)


@pytest.mark.parametrize("command", ["validate", "plan"])
def test_cli_warns_on_old_source_and_only_plans_new_contract(tmp_path, command):
    source = tmp_path / "experiment.yaml"
    source.write_text(yaml.safe_dump(original_snapshot()))
    arguments = [command, str(source)]
    if command == "plan":
        arguments.extend(["--format", "json"])
    with pytest.warns(
        SpecificationDeprecationWarning, match="mininet-ai/v1alpha3 is deprecated"
    ):
        result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.output
    if command == "plan":
        assert json.loads(result.stdout)["apiVersion"] == "mininet-ai/v1alpha4"


def test_cli_invalid_old_source_produces_no_plan(tmp_path):
    snapshot = original_snapshot()
    snapshot["blueprints"][0]["model"]["unsupported"] = "do-not-echo"
    source = tmp_path / "experiment.yaml"
    source.write_text(yaml.safe_dump(snapshot))
    result = CliRunner().invoke(app, ["plan", str(source)])
    assert result.exit_code == 1
    assert "unsupported" in result.output
    assert "do-not-echo" not in result.output
    assert '"kind": "DeploymentPlan"' not in result.output


def test_new_saved_snapshot_is_not_normalized_as_old_source():
    snapshot = original_snapshot()
    with pytest.warns(SpecificationDeprecationWarning):
        plan = compile_experiment(Experiment.model_validate(snapshot))
    snapshot = plan.snapshot
    snapshot["blueprints"][0]["apiVersion"] = "mininet-ai/v1alpha3"
    with (
        warnings.catch_warnings(record=True) as observed,
        pytest.raises(AgentRuntimeError, match="snapshot is not executable"),
    ):
        ExecutionCatalog(plan)
    assert not observed


def test_legacy_digest_validation_cannot_be_bypassed_with_python_field_name():
    original = json.loads(LEGACY_PLAN.read_text())
    original["api_version"] = original.pop("apiVersion")
    original["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError):
        DeploymentPlan.model_validate(original)


def test_cli_deprecation_is_visible_with_normal_python_warning_filters(tmp_path):
    source = tmp_path / "experiment.yaml"
    source.write_text(yaml.safe_dump(original_snapshot()))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from mininet_ai.cli import app; app()",
            "validate",
            str(source),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "mininet-ai/v1alpha3 is deprecated" in result.stderr
