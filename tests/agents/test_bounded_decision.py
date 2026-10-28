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


def test_omitted_native_diagnostics_remain_unavailable():
    result = map_choice(
        {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}},
        {"no_action": (), "install_recovery": ()},
    )

    assert result.probability is None
    assert result.confidence is None


@pytest.mark.parametrize("field", ["probability", "confidence"])
@pytest.mark.parametrize(
    "value", [None, True, "0.5", -0.01, 1.01, float("nan"), float("inf")]
)
def test_present_native_diagnostics_must_be_valid_numbers(field, value):
    response = {
        "answers": {
            "next_action": {"type": "choice", "choice": "no_action", field: value}
        }
    }

    with pytest.raises(AgentRuntimeError) as error:
        map_choice(response, {"no_action": (), "install_recovery": ()})

    assert error.value.code == "agent.bounded-decision.invalid-output"
    assert field in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        {"answers": {}},
        {"answers": {"next_action": {"type": "choice", "choice": "other"}}},
        {"answers": {"next_action": {"type": "text", "choice": "no_action"}}},
        {
            "answers": {
                "next_action": {
                    "type": "choice",
                    "choice": "install_recovery",
                    "proposals": [{"capability": "invented"}],
                }
            }
        },
    ],
)
def test_invalid_native_choice_is_not_coerced_to_no_action(response):
    with pytest.raises(AgentRuntimeError, match="choice"):
        map_choice(response, {"no_action": (), "install_recovery": ()})


def test_exact_recovery_choice_maps_fixed_bundle_even_at_low_confidence():
    bundle = (
        {
            "capability": "openflow.flow.install",
            "target": "s1",
            "arguments": {"rule": 1},
        },
        {
            "capability": "openflow.flow.install",
            "target": "s2",
            "arguments": {"rule": 2},
        },
    )
    result = map_choice(
        {
            "answers": {
                "next_action": {
                    "type": "choice",
                    "choice": "install_recovery",
                    "probability": 0.01,
                    "confidence": 0.02,
                }
            }
        },
        {"no_action": (), "install_recovery": bundle},
    )

    assert result.choice_id == "install_recovery"
    assert result.proposals == bundle
    assert result.probability == 0.01
    assert result.confidence == 0.02


def test_map_choice_rejects_non_contract_choice_even_if_bundle_is_present():
    with pytest.raises(AgentRuntimeError) as error:
        map_choice(
            {"answers": {"next_action": {"type": "choice", "choice": "emergency"}}},
            {
                "no_action": (),
                "install_recovery": (),
                "emergency": ({"capability": "fake"},),
            },
        )

    assert error.value.code == "agent.bounded-decision.invalid-output"


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
            return {
                "answers": {"next_action": {"type": "choice", "choice": "no_action"}}
            }

    transport = Transport()
    client = NativeSystemOneClient("http://localhost:8080/", transport)
    result = client.decide(
        state='{"observations":[],"rounds":3}',
        instructions="Choose only from the declared choices.",
        criteria=("no_action", "install_recovery"),
        choices={
            "no_action": (),
            "install_recovery": ({"capability": "flow.install"},),
        },
        timeout_seconds=0.8,
    )

    assert result.choice_id == "no_action"
    url, payload, kwargs = transport.call
    assert url == "http://localhost:8080/v1/systemone"
    assert payload["state"] == '{"observations":[],"rounds":3}'
    assert tuple(payload["questions"]) == ("next_action",)
    assert payload["questions"]["next_action"]["criteria"] == [
        "no_action",
        "install_recovery",
    ]
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
        proposalMapping={"choice": "no_action", "proposals": []},
        authorization={"status": "not-required"},
        admission={"status": "not-required"},
        actionOutcomes=(),
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


def test_startup_distinguishes_unverifiable_identity_from_service_interface_failure():
    class Transport:
        def __init__(self):
            self.props = {}

        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else self.props

        def post_json(self, url, payload, **kwargs):
            return {
                "answers": {"next_action": {"type": "choice", "choice": "no_action"}}
            }

    transport = Transport()
    client = NativeSystemOneClient("http://127.0.0.1:8080", transport)
    result = client.verify_startup(
        model_name="expected-model",
        expected_identity={
            "repository": "org/model",
            "revision": "rev",
            "artifact": "model.gguf",
            "sha256": "0" * 64,
        },
        instructions="Choose a fixed action.",
        criteria=("no_action", "install_recovery"),
        choices={"no_action": (), "install_recovery": ()},
    )
    assert result["status"] == "unverified"
    assert result["interface"] == "verified"

    transport.props = {"model_alias": "different-model"}
    with pytest.raises(AgentRuntimeError) as error:
        client.verify_startup(
            model_name="expected-model",
            expected_identity={
                "repository": "org/model",
                "revision": "rev",
                "artifact": "model.gguf",
                "sha256": "0" * 64,
            },
            instructions="Choose a fixed action.",
            criteria=("no_action", "install_recovery"),
            choices={"no_action": (), "install_recovery": ()},
        )
    assert error.value.code == "agent.bounded-decision.startup-identity-mismatch"
