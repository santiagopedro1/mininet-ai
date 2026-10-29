"""Regressions for the final bounded-diagnosis review findings."""

import pytest
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
    ):
        AgentBlueprint.model_validate(document)


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


def test_native_http_rejection_is_distinct_and_retains_diagnostics():
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
            lambda request: httpx.Response(
                400, json={"error": "context limit exceeded"}
            )
        )
    )
    runtime = OneShotAgentRuntime(
        plan, substrate, registries, decision_transport=transport
    )
    result = runtime.invoke(run.id, plan.agents[0].id, "diagnose")
    assert result.issue is not None
    assert result.issue.code == "agent.bounded-decision.request-rejected"
    assert result.response is not None
    from typing import Any, cast

    record = cast(dict[str, Any], result.response.metadata["executionRecord"])
    error = record["runtime"]["boundedDecision"]["response"]["transportError"]
    assert error["httpStatus"] == 400
    assert "context limit exceeded" in error["bodyUtf8"]
