"""Explicit static diagnosis context must not enrich historical plans silently."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from mininet_ai.compiler import (
    compile_experiment,
    load_deployment_plan,
    save_deployment_plan,
)
from mininet_ai.specification.models import DecisionScenario


def rich_document():
    source = Path(__file__).parents[2] / "examples/diamond-recovery/experiment.yaml"
    document = yaml.safe_load(source.read_text())
    scenario = document["decisionScenarios"][0]
    scenario["diagnosticContext"] = {
        "apiVersion": "mininet-ai/bounded-decision-context/v1alpha2",
        "diagnosticScope": "required-local-configuration",
        "flowRoles": {
            target: {
                flow["cookie"]: {
                    "purpose": "Required local flow",
                    "required": True,
                    "requirement": "Mandatory regardless of active-traffic connectivity",
                }
                for flow in expected["flows"]
            }
            for target, expected in scenario["localExpectations"].items()
        },
    }
    return document


def test_static_diagnostic_context_is_embedded_and_saved_without_source_rereads(
    tmp_path,
):
    document = rich_document()
    source = tmp_path / "experiment.yaml"
    source.write_text(yaml.safe_dump(document, sort_keys=False))
    plan = compile_experiment(source)
    saved = save_deployment_plan(plan, tmp_path / "plan.json")
    original = saved.read_bytes()
    source.unlink()

    restored = load_deployment_plan(saved)
    assert restored.digest == plan.digest
    assert saved.read_bytes() == original
    for agent in restored.agents:
        assert agent.decision_scenario is not None
        assert (
            agent.decision_scenario["diagnosticContext"]
            == document["decisionScenarios"][0]["diagnosticContext"]
        )
    schema = json.loads(
        (
            Path(__file__).parents[2]
            / "mininet_ai/compiler/schemas/v1alpha5-decision-scenario.json"
        ).read_text()
    )
    Draft202012Validator(schema).validate(document["decisionScenarios"][0])


def test_unannotated_v1alpha5_saved_plan_does_not_acquire_context_defaults(tmp_path):
    document = rich_document()
    document["decisionScenarios"][0].pop("diagnosticContext")
    parsed = DecisionScenario.model_validate(document["decisionScenarios"][0])
    assert "diagnosticContext" not in parsed.model_dump(mode="json", by_alias=True)
    source = tmp_path / "experiment.yaml"
    source.write_text(yaml.safe_dump(document, sort_keys=False))
    plan = compile_experiment(source)
    saved = save_deployment_plan(plan, tmp_path / "plan.json")
    original = saved.read_bytes()
    restored = load_deployment_plan(saved)
    assert restored.model_dump(mode="json", by_alias=True) == plan.model_dump(
        mode="json", by_alias=True
    )
    assert saved.read_bytes() == original
    assert all(
        "diagnosticContext" not in agent.decision_scenario
        for agent in restored.agents
        if agent.decision_scenario is not None
    )


@pytest.mark.parametrize(
    "damage",
    [
        "version",
        "scope",
        "missing-target",
        "extra-target",
        "missing-cookie",
        "extra-cookie",
        "duplicate-expected-cookie",
        "optional-role",
        "coerced-required",
        "computed-label",
        "observed-presence",
    ],
)
def test_context_rejects_unknown_versions_incomplete_bindings_and_observed_hints(
    damage,
):
    document = rich_document()["decisionScenarios"][0]
    context = document["diagnosticContext"]
    roles = context["flowRoles"]["s2"]
    cookie = next(iter(roles))
    if damage == "version":
        context["apiVersion"] = "mininet-ai/bounded-decision-context/v99"
    elif damage == "scope":
        context["diagnosticScope"] = "network-recovery"
    elif damage == "missing-target":
        context["flowRoles"].pop("s2")
    elif damage == "extra-target":
        context["flowRoles"]["s99"] = deepcopy(roles)
    elif damage == "missing-cookie":
        roles.pop(cookie)
    elif damage == "extra-cookie":
        roles["0xdead"] = deepcopy(roles[cookie])
    elif damage == "duplicate-expected-cookie":
        document["localExpectations"]["s2"]["flows"][-1]["cookie"] = cookie
    elif damage == "optional-role":
        roles[cookie]["required"] = False
    elif damage == "coerced-required":
        roles[cookie]["required"] = 1
    elif damage == "computed-label":
        context["expectedDiagnosis"] = "degraded"
    elif damage == "observed-presence":
        roles[cookie]["missing"] = True
    with pytest.raises(ValidationError):
        DecisionScenario.model_validate(document)


def test_recovery_history_scenario_cannot_opt_into_local_annotations(tmp_path):
    from tests.compiler.test_bounded_decision_contract import _source_for_compile

    source = _source_for_compile(tmp_path)
    document = yaml.safe_load(source.read_text())["decisionScenarios"][0]
    document["diagnosticContext"] = rich_document()["decisionScenarios"][0][
        "diagnosticContext"
    ]
    with pytest.raises(ValidationError, match="diagnosticContext.*current-only"):
        DecisionScenario.model_validate(document)


@pytest.mark.parametrize("damage", ["version", "verdict", "presence", "raw-role"])
def test_published_state_schema_rejects_unversioned_or_oracle_fields(damage):
    scenario = rich_document()["decisionScenarios"][0]
    state = {
        "apiVersion": "mininet-ai/bounded-decision-context/v1alpha2",
        "scenario": scenario["metadata"],
        "identity": {
            "runId": "run-test",
            "agentId": "test@s2",
            "invocationId": "invoke-test",
            "tickId": "tick-test",
            "invokedAt": "2026-10-30T00:00:00+00:00",
        },
        "switch": {
            "targets": ["s2"],
            "diagnosis": {
                "portState": {
                    "source": "ovs.port-state",
                    "availability": "missing",
                    "value": None,
                    "observedAt": None,
                }
            },
            "expectations": deepcopy(scenario["localExpectations"]["s2"]),
            "diagnosticScope": "required-local-configuration",
            "flowRoles": deepcopy(scenario["diagnosticContext"]["flowRoles"]["s2"]),
        },
    }
    schema = json.loads(
        (
            Path(__file__).parents[2]
            / "mininet_ai/compiler/schemas/bounded-decision-context-v1alpha2.json"
        ).read_text()
    )
    validator = Draft202012Validator(schema)
    validator.validate(state)
    if damage == "version":
        state["apiVersion"] = "mininet-ai/bounded-decision-context/v1alpha1"
    elif damage == "verdict":
        state["switch"]["expectedDiagnosis"] = "degraded"
    elif damage == "presence":
        next(iter(state["switch"]["flowRoles"].values()))["missing"] = True
    elif damage == "raw-role":
        state["switch"]["expectations"]["flows"][0]["purpose"] = "Drop"
    assert not validator.is_valid(state)
