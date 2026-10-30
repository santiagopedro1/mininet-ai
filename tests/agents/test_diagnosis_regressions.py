"""Regressions for the final bounded-diagnosis review findings."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from mininet_ai.specification.models import AgentBlueprint, DecisionChoice
from tests.compiler.test_bounded_decision_contract import bounded_blueprint


@pytest.mark.parametrize("version", ["mininet-ai/v1alpha3", "mininet-ai/v1alpha4"])
@pytest.mark.parametrize("selection", [{"kind": "agno"}, None])
def test_historical_source_rejects_explicit_execution_field(version, selection):
    document = bounded_blueprint()
    document["apiVersion"] = version
    document["execution"] = selection
    with pytest.raises(
        ValidationError, match="execution.*requires mininet-ai/v1alpha5"
    ) as error:
        AgentBlueprint.model_validate(document)
    assert "historical versions omit the execution field to mean Agno" in str(
        error.value
    )


@pytest.mark.parametrize("field", ["capability", "target", "arguments"])
@pytest.mark.parametrize("bad_value", [[], None, 42, ""])
def test_fixed_proposal_fields_have_validated_types(field, bad_value):
    proposal = {"capability": "flow.install", "target": "s1", "arguments": {}}
    proposal[field] = bad_value
    with pytest.raises(ValidationError):
        DecisionChoice.model_validate(
            {
                "id": "install_recovery",
                "description": "Recover",
                "proposals": [proposal],
            }
        )
    for name in ("v1alpha5-experiment.json", "v1alpha5-decision-scenario.json"):
        schema_path = Path(__file__).parents[2] / "mininet_ai/compiler/schemas" / name
        schema = json.loads(schema_path.read_text())["$defs"]["DecisionChoice"]
        assert not Draft202012Validator(schema).is_valid(
            {
                "id": "install_recovery",
                "description": "Recover",
                "proposals": [proposal],
            }
        )


@pytest.mark.parametrize("version", ["mininet-ai/v1alpha3", "mininet-ai/v1alpha4"])
@pytest.mark.parametrize("field", ["decisionScenarios", "decisionInputs"])
def test_historical_experiment_rejects_explicit_new_empty_fields(version, field):
    from pathlib import Path

    import yaml

    from mininet_ai.specification.models import Experiment

    path = Path(__file__).parents[2] / "examples/diamond-recovery/experiment.yaml"
    document = yaml.safe_load(path.read_text())
    document["apiVersion"] = version
    document.pop("decisionScenarios")
    document["blueprints"][0].pop("execution")
    for deployment in document["agents"]:
        deployment.pop("decisionInputs")
    if field == "decisionScenarios":
        document[field] = []
    else:
        document["agents"][0][field] = {}
    with pytest.raises(ValidationError, match="require.*mininet-ai/v1alpha5"):
        Experiment.model_validate(document)


def test_startup_probe_uses_declared_budget_and_reports_timeout_not_invalid_interface():
    from mininet_ai.agents.bounded import NativeSystemOneClient
    from mininet_ai.errors import AgentRuntimeError

    class Transport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            assert kwargs["timeout_seconds"] == 5
            raise TimeoutError("probe expired")

    with pytest.raises(AgentRuntimeError) as caught:
        NativeSystemOneClient("http://localhost", Transport()).verify_startup(
            model_name="model",
            expected_identity={},
            instructions="Diagnose",
            criteria={"healthy": "Healthy", "degraded": "Degraded"},
            choices={"healthy": (), "degraded": ()},
            interface_timeout_seconds=5,
        )
    assert caught.value.code == "agent.bounded-decision.startup-timeout"


@pytest.mark.parametrize(
    "payload, status, code",
    [
        ({"error": "context limit exceeded"}, 400, "request-rejected"),
        (
            {"answers": {"next_action": {"type": "choice", "choice": "invented"}}},
            200,
            "invalid-output",
        ),
        (
            {
                "answers": {
                    "next_action": {
                        "type": "choice",
                        "choice": "healthy",
                        "confidence": 99,
                    }
                }
            },
            200,
            "invalid-output",
        ),
        (
            {
                "answers": {"next_action": {"type": "choice", "choice": "healthy"}},
                "usage": {"output_tokens": 2},
            },
            200,
            "native-contract-violation",
        ),
        (b'{"answers": invalid}', 200, "invalid-output"),
    ],
)
def test_native_failures_retain_exact_response_and_distinct_code(payload, status, code):
    from pathlib import Path

    import httpx

    from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
    from mininet_ai.compiler import compile_experiment
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime
    from mininet_ai.transports.http import HttpxJsonTransport

    plan = compile_experiment(
        Path(__file__).parents[2] / "examples/diamond-recovery/experiment.yaml"
    )
    substrate = FakeSubstrateRuntime()
    run = substrate.deploy(plan)
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    transport = HttpxJsonTransport(
        httpx.MockTransport(
            lambda request: (
                httpx.Response(status, content=payload)
                if isinstance(payload, bytes)
                else httpx.Response(status, json=payload)
            )
        )
    )
    runtime = OneShotAgentRuntime(
        plan, substrate, registries, decision_transport=transport
    )
    result = runtime.invoke(run.id, plan.agents[0].id, "diagnose")
    assert result.issue is not None
    assert result.issue.code == f"agent.bounded-decision.{code}"
    assert result.response is not None
    from typing import Any, cast

    record = cast(dict[str, Any], result.response.metadata["executionRecord"])
    from tests.agents.test_bounded_evidence_schemas import validators

    _, validator = validators()
    validator.validate(record)
    response = record["runtime"]["boundedDecision"]["response"]
    if isinstance(payload, bytes):
        from base64 import b64decode

        assert b64decode(response["transportError"]["bodyBase64"]) == payload
    elif status == 200:
        assert response == payload
    else:
        error = response["transportError"]
        assert error["httpStatus"] == 400
        assert "context limit exceeded" in error["bodyUtf8"]
