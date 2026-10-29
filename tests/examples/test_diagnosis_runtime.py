"""The amended example must cross the shared continuous runtime boundary."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from examples.diamond_recovery.diagnosis import evaluate_runtime, expected_diagnosis
from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates import FakeSubstrateRuntime
from tests.agents.test_bounded_evidence_schemas import validators


@pytest.mark.parametrize(
    "fault", ["healthy", "down", "missing-flow", "unavailable", "wrong-flow"]
)
def test_current_only_agents_retain_local_inputs_and_normalized_evidence(
    tmp_path, fault
):
    path = Path(__file__).parents[2] / "examples/diamond-recovery/experiment.yaml"
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
                if target == "s2" and fault == "wrong-flow":
                    local["flows"][0]["actions"] = "NORMAL"
                value = {"switches": [{"name": target, "flows": local["flows"]}]}
            return result.model_copy(update={"values": {target: value}})

    class Transport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            assert url.endswith("/v1/systemone")
            assert set(payload["questions"]) == {"next_action"}
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
                for observation in state["switch"]["diagnosis"].values():
                    assert set(observation["value"]) == {target}
                assert 0 < kwargs["timeout_seconds"] <= 5
                if target == "s2" and fault != "healthy":
                    choice = "degraded"
            return {
                "answers": {"next_action": {"type": "choice", "choice": choice}},
                "usage": {"output_tokens": 0},
            }

    summary = evaluate_runtime(
        plan, LocalSubstrate(), tmp_path / "evidence", decision_transport=Transport()
    )
    assert summary["passed"], summary
    assert summary["samples"] >= 4
    assert summary["correct"] == summary["samples"]
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
        assert (
            expected_diagnosis(json.loads(decision["request"]["state"]))
            == decision["selectedChoice"]
        )
