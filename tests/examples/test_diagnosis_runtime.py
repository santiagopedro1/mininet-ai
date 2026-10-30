"""The amended example must cross the shared continuous runtime boundary."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from examples.diamond_recovery.diagnosis import evaluate_runtime, expected_diagnosis
from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates import FakeSubstrateRuntime
from tests.agents.test_bounded_evidence_schemas import validators
from tests.compiler.test_diagnostic_context import rich_document


@pytest.mark.parametrize("enriched", [False, True])
@pytest.mark.parametrize("force_healthy", [False, True])
@pytest.mark.parametrize(
    "fault",
    [
        "healthy",
        "reordered-flows",
        "down",
        "missing-flow",
        "missing-active-flow",
        "missing-flow-first",
        "missing-flow-same-count",
        "unavailable",
        "wrong-flow",
        "wrong-cookie",
    ],
)
def test_current_only_agents_retain_local_inputs_and_normalized_evidence(
    tmp_path, fault, force_healthy, enriched
):
    import yaml

    document = rich_document()
    if not enriched:
        document["decisionScenarios"][0].pop("diagnosticContext")
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    plan = compile_experiment(path)
    scenario = plan.agents[0].decision_scenario
    assert scenario is not None
    expected = scenario["localExpectations"]

    class LocalSubstrate(FakeSubstrateRuntime):
        def observe(self, run_id, query):
            result = super().observe(run_id, query)
            (target,) = query.targets
            local = deepcopy(expected[target])
            if query.name == "ovs.port-state":
                value = {
                    "ports": [{"name": port, "state": "up"} for port in local["ports"]]
                }
                if target == "s2" and fault == "down":
                    value["ports"][0]["state"] = "down"
                if target == "s2" and fault == "unavailable":
                    return result.model_copy(update={"values": {target: None}})
            else:
                if target == "s2" and fault == "missing-flow":
                    local["flows"].pop()
                if target == "s2" and fault == "missing-active-flow":
                    local["flows"].pop(1)
                if target == "s2" and fault == "missing-flow-first":
                    local["flows"].pop(0)
                if target == "s2" and fault == "missing-flow-same-count":
                    local["flows"][-1] = deepcopy(local["flows"][0])
                if target == "s2" and fault == "wrong-flow":
                    local["flows"][0]["actions"] = "NORMAL"
                if target == "s2" and fault == "wrong-cookie":
                    local["flows"][-1]["cookie"] = "0xdead"
                if fault == "reordered-flows":
                    local["flows"].reverse()
                value = {"switches": [{"name": target, "flows": local["flows"]}]}
            return result.model_copy(update={"values": {target: value}})

    class Transport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            assert url.endswith("/v1/systemone")
            assert set(payload["questions"]) == {"next_action"}
            question = payload["questions"]["next_action"]
            assert question["instructions"] == scenario["instructions"]
            assert question["criteria"] == {
                choice["id"]: choice["description"] for choice in scenario["choices"]
            }
            state = (
                json.loads(payload["state"])
                if payload["state"].startswith("{")
                else None
            )
            choice = "healthy"
            if state is not None and "switch" in state:
                assert "history" not in state
                (target,) = state["switch"]["targets"]
                assert state["switch"]["expectations"] == expected[target]
                if enriched:
                    assert (
                        state["apiVersion"]
                        == "mininet-ai/bounded-decision-context/v1alpha2"
                    )
                    assert (
                        state["switch"]["diagnosticScope"]
                        == "required-local-configuration"
                    )
                    assert (
                        state["switch"]["flowRoles"]
                        == scenario["diagnosticContext"]["flowRoles"][target]
                    )
                    assert all(
                        set(role) == {"purpose", "required", "requirement"}
                        for role in state["switch"]["flowRoles"].values()
                    )
                else:
                    assert (
                        state["apiVersion"]
                        == "mininet-ai/bounded-decision-context/v1alpha1"
                    )
                    assert "flowRoles" not in state["switch"]
                    assert "diagnosticScope" not in state["switch"]
                for observation in state["switch"]["diagnosis"].values():
                    assert set(observation["value"]) == {target}
                if target == "s2" and fault == "missing-flow":
                    # The failed native case removes a diagnostic-probe rule,
                    # while both active-address forwarding rules remain present.
                    flows = state["switch"]["diagnosis"]["flowState"]["value"][target][
                        "switches"
                    ][0]["flows"]
                    active = [
                        flow
                        for flow in expected[target]["flows"]
                        if flow["match"].get("nw_src") in {"10.0.0.1", "10.0.0.2"}
                    ]
                    assert len(active) == 2
                    assert all(flow in flows for flow in active)
                    assert expected[target]["flows"][-1] not in flows
                assert 0 < kwargs["timeout_seconds"] <= 5
                if (
                    not force_healthy
                    and target == "s2"
                    and fault not in {"healthy", "reordered-flows"}
                ):
                    choice = "degraded"
            return {
                "answers": {"next_action": {"type": "choice", "choice": choice}},
                "usage": {"output_tokens": 0},
            }

    summary = evaluate_runtime(
        plan, LocalSubstrate(), tmp_path / "evidence", decision_transport=Transport()
    )
    expected_pass = not (force_healthy and fault not in {"healthy", "reordered-flows"})
    assert summary["passed"] == expected_pass, summary
    assert summary["samples"] >= 4
    if expected_pass:
        assert summary["correct"] == summary["samples"]
    else:
        # Wrong native diagnoses remain successful transport/runtime operations,
        # but fail evaluation. Never turn the oracle label into model output.
        assert summary["operation"] == "succeeded"
        assert summary["correct"] < summary["samples"]
        assert all(item["status"] == "succeeded" for item in summary["evaluations"])
    records = [
        json.loads(line)
        for line in (tmp_path / "evidence/invocations.jsonl").read_text().splitlines()
    ]
    assert {record["agentId"] for record in records} == {
        agent.id for agent in plan.agents
    }
    for record in records:
        assert record["coordination"] is not None
        execution = record["result"]["response"]["metadata"]["executionRecord"]
        _, validator = validators()
        validator.validate(execution)
        decision = execution["runtime"]["boundedDecision"]
        assert decision["actionOutcomes"] == []
        if enriched:
            from jsonschema import Draft202012Validator

            schema = json.loads(
                (
                    Path(__file__).parents[2]
                    / "mininet_ai/compiler/schemas/bounded-decision-context-v1alpha2.json"
                ).read_text()
            )
            Draft202012Validator(schema).validate(
                json.loads(decision["request"]["state"])
            )
        if force_healthy:
            assert decision["selectedChoice"] == "healthy"
        else:
            assert (
                expected_diagnosis(json.loads(decision["request"]["state"]))
                == decision["selectedChoice"]
            )
