import pytest

from mininet_ai.agents.bounded import NativeSystemOneClient, map_choice
from mininet_ai.agents.bounded_contracts import (
    AgentExecutionRecord,
    BoundedDecisionRecord,
)
from mininet_ai.errors import AgentRuntimeError


def test_exact_no_action_choice_maps_to_no_proposals():
    result = map_choice(
        {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}},
        {"no_action": (), "install_recovery": ({"capability": "flow.install"},)},
    )

    assert result.choice_id == "no_action"
    assert result.proposals == ()


@pytest.mark.parametrize(
    "response",
    [
        {"answers": {}},
        {"answers": {"next_action": {"type": "choice", "choice": "other"}}},
        {"answers": {"next_action": {"type": "text", "choice": "no_action"}}},
    ],
)
def test_invalid_native_choice_is_not_coerced_to_no_action(response):
    with pytest.raises(AgentRuntimeError, match="choice"):
        map_choice(response, {"no_action": (), "install_recovery": ()})


def test_recovery_choice_is_explicitly_rejected_before_proposals_escape():
    with pytest.raises(AgentRuntimeError, match="not supported") as error:
        map_choice(
            {"answers": {"next_action": {"type": "choice", "choice": "install_recovery"}}},
            {"no_action": (), "install_recovery": ({"capability": "flow.install"},)},
        )

    assert error.value.code == "agent.bounded-decision.choice-not-supported"


def test_no_action_cannot_carry_a_mutation_bundle():
    with pytest.raises(AgentRuntimeError, match="must have no proposals"):
        map_choice(
            {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}},
            {"no_action": ({"capability": "flow.install"},), "install_recovery": ()},
        )


def test_native_client_posts_one_question_and_retains_canonical_state():
    class Transport:
        def post_json(self, url, payload, **kwargs):
            self.call = (url, payload, kwargs)
            return {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}}

    transport = Transport()
    client = NativeSystemOneClient("http://localhost:8080/", transport)
    result = client.decide(
        state='{"observations":[],"rounds":3}',
        instructions="Choose only from the declared choices.",
        criteria=("no_action", "install_recovery"),
        choices={"no_action": (), "install_recovery": ({"capability": "flow.install"},)},
        timeout_seconds=0.8,
    )

    assert result.choice_id == "no_action"
    url, payload, kwargs = transport.call
    assert url == "http://localhost:8080/v1/systemone"
    assert payload["state"] == '{"observations":[],"rounds":3}'
    assert tuple(payload["questions"]) == ("next_action",)
    assert payload["questions"]["next_action"]["criteria"] == ["no_action", "install_recovery"]
    assert kwargs["timeout_seconds"] == 0.8


def test_native_response_duplicate_keys_are_invalid():
    with pytest.raises(AgentRuntimeError, match="duplicate JSON key"):
        map_choice(
            '{"answers":{"next_action":{"type":"choice","choice":"no_action","choice":"install_recovery"}}}',
            {"no_action": (), "install_recovery": ()},
        )


def test_bounded_evidence_is_versioned_without_agno_identity_or_metrics():
    record = BoundedDecisionRecord(
        runId="run-1",
        agentId="decision-maker",
        invocationId="invoke-1",
        correlationId="tick-1",
        scenario={"name": "diamond"},
        effectiveConfiguration={"endpoint": "http://localhost:8080"},
        request={"state": "{}"},
        response={"choice": "no_action"},
        selectedChoice="no_action",
        modelProvenance={"verification": "unverified"},
        timings={"decision": 0.01},
        gaps=("server build unavailable",),
    )
    execution = AgentExecutionRecord(
        executionKind="bounded-decision",
        outcome="succeeded",
        runtime={"decision": record.model_dump(mode="json", by_alias=True)},
    )

    dumped = execution.model_dump(mode="json", by_alias=True)
    assert dumped["apiVersion"] == "mininet-ai/agent-execution/v1alpha1"
    assert dumped["agnoRunId"] is None
    assert dumped["sessionId"] is None
    assert dumped["metrics"] is None
