"""Tests for maintained examples.

Each example is validated and planned to catch regressions as the compiler
evolves. Golden plans are not used for examples — they are meant to be
readable, not frozen.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from mininet_ai.capabilities.verification import PostconditionVerifier
from mininet_ai.compiler import compile_experiment
from mininet_ai.sdk import ActionProposal, AgentContext, ExecutionCatalog
from mininet_ai.specification import load_experiment
from mininet_ai.specification.models import (
    AttachmentLayer,
    ModelConfiguration,
    ResourceKind,
)
from mininet_ai.substrates.mininet_ovs.observations import MininetOVSObservations
from mininet_ai.substrates.runtime import ObservationQuery, ObservationResult

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"

EXAMPLE_NAMES = [
    "diamond-recovery",
    "getting-started",
    "autonomous-operation",
    "hierarchical-routing",
    "iperf-throughput",
]


def test_diamond_recovery_compiles_exact_recovery_bundle() -> None:
    """The maintained diamond plan embeds its complete fixed recovery policy."""
    plan = compile_experiment(EXAMPLES_DIR / "diamond-recovery/experiment.yaml")
    assert plan.api_version == "mininet-ai/v1alpha5"
    assert len(plan.agents) == 1
    instance = plan.agents[0]
    assert set(instance.observes) == {
        "diamond.path-probes",
        "diamond.link-states",
        "diamond.policy-state",
        "diamond.action-outcomes",
    }
    assert instance.decision_inputs == {
        "pathProbes": "diamond.path-probes",
        "linkStates": "diamond.link-states",
        "policyState": "diamond.policy-state",
        "actionOutcomes": "diamond.action-outcomes",
    }
    scenario = instance.decision_scenario
    assert scenario is not None
    assert scenario["paths"] == {
        "primary": ["h1", "s1", "s2", "s4", "h2"],
        "recovery": ["h1", "s1", "s3", "s4", "h2"],
    }
    choices = scenario["choices"]
    assert [choice["id"] for choice in choices] == ["no_action", "install_recovery"]
    assert choices[0]["proposals"] == []
    rules = choices[1]["proposals"]
    assert len(rules) == 8
    assert [
        (
            rule["target"],
            rule["arguments"]["match"]["in_port"],
            rule["arguments"]["actions"],
        )
        for rule in rules
    ] == [
        ("s3", 1, "output:2"),
        ("s3", 2, "output:1"),
        ("s4", 3, "output:1"),
        ("s4", 1, "output:3"),
        ("s1", 1, "output:3"),
        ("s1", 3, "output:1"),
        ("s2", 1, "drop"),
        ("s2", 2, "drop"),
    ]
    assert [rule["arguments"]["priority"] for rule in rules] == [200] * 8
    assert [rule["arguments"]["hardTimeout"] for rule in rules] == [0] * 8
    assert [rule["arguments"]["cookie"] for rule in rules] == list(range(12201, 12209))
    assert all(
        rule["arguments"]["match"]["dl_type"] == 2048
        and rule["arguments"]["match"]["nw_src"] in {"10.0.0.1", "10.0.0.2"}
        and rule["arguments"]["match"]["nw_dst"] in {"10.0.0.1", "10.0.0.2"}
        for rule in rules
    )
    assert scenario["observations"]["historyRounds"] == 3
    assert scenario["timing"] == {"cadence": "1s", "decisionDeadline": "800ms"}
    resources = plan.snapshot["substrate"]["topology"]["resources"]
    switch_ports = {
        resource["name"]: {port["number"] for port in resource["ports"]}
        for resource in resources
        if resource["kind"] == "switch"
    }
    assert switch_ports == {
        "s1": {1, 2, 3},
        "s2": {1, 2},
        "s3": {1, 2},
        "s4": {1, 2, 3},
    }
    assert plan.agents[0].attachment.targets == ("s1", "s2", "s3", "s4")
    from examples.diamond_recovery import INITIAL_RULES, RECOVERY_RULES

    assert scenario["policy"]["initial"]["rules"] == list(INITIAL_RULES)
    assert scenario["policy"]["initial"]["tableMiss"] == "drop"
    assert scenario["policy"]["initial"]["failMode"] == "secure"
    assert scenario["policy"]["initial"]["controllerLearning"] is False
    assert scenario["policy"]["initial"]["normalFallback"] is False
    assert scenario["policy"]["initial"]["staticNeighbors"] == {
        "h1": {
            "10.0.0.2": "02:00:00:00:00:02",
            "10.0.0.12": "02:00:00:00:00:02",
            "10.0.0.22": "02:00:00:00:00:02",
        },
        "h2": {
            "10.0.0.1": "02:00:00:00:00:01",
            "10.0.0.11": "02:00:00:00:00:01",
            "10.0.0.21": "02:00:00:00:00:01",
        },
    }
    assert [rule["arguments"]["idleTimeout"] for rule in rules] == [0] * 8
    assert [
        {
            "switch": rule["target"],
            **rule["arguments"],
        }
        for rule in rules
    ] == list(RECOVERY_RULES)
    link_map = {
        link["name"]: tuple(
            (endpoint["node"], endpoint.get("adapter"))
            for endpoint in link["endpoints"]
        )
        for link in plan.snapshot["substrate"]["topology"]["links"]
    }
    assert link_map == {
        "h1-s1": (("h1", "h1-eth0"), ("s1", "s1-eth1")),
        "s1-s2": (("s1", "s1-eth2"), ("s2", "s2-eth1")),
        "s2-s4": (("s2", "s2-eth2"), ("s4", "s4-eth2")),
        "s1-s3": (("s1", "s1-eth3"), ("s3", "s3-eth1")),
        "s3-s4": (("s3", "s3-eth2"), ("s4", "s4-eth3")),
        "s4-h2": (("s4", "s4-eth1"), ("h2", "h2-eth0")),
    }


def test_diamond_recovery_oracle_covers_required_decision_classes() -> None:
    fixture = (
        EXAMPLES_DIR.parent
        / "tests/fixtures/specifications/diamond-recovery/oracle.json"
    )
    cases = json.loads(fixture.read_text())["cases"]
    labels = {case["label"] for case in cases}
    assert {
        "healthy",
        "startup-insufficient-history",
        "one-round-transient-loss",
        "two-round-transient-loss",
        "sustained-primary-failure",
        "stale-evidence",
        "missing-round",
        "conflicting-link-and-probe-evidence",
        "failed-recovery-probes",
        "partial-or-unknown-policy",
        "post-recovery-restraint",
    } <= labels
    assert all(case["expected"] in {"no_action", "install_recovery"} for case in cases)
    assert all(
        case["expected"] == "no_action"
        for case in cases
        if case["label"] != "sustained-primary-failure"
    )


def test_diamond_oracle_carries_bidirectional_round_evidence_for_each_link() -> None:
    fixture = (
        EXAMPLES_DIR.parent
        / "tests/fixtures/specifications/diamond-recovery/oracle.json"
    )
    cases = json.loads(fixture.read_text())["cases"]
    sustained = [case for case in cases if case["label"] == "sustained-primary-failure"]

    assert {case["failedLink"] for case in sustained} == {"s1-s2", "s2-s4"}
    for case in cases:
        assert len(case["rounds"]) == 3
        assert (
            sum(round_["complete"] for round_ in case["rounds"])
            == case["completeRounds"]
        )
        for round_ in case["rounds"]:
            assert set(round_["primary"]) == {"forward", "reverse"}
            assert set(round_["recovery"]) == {"forward", "reverse"}
            assert set(round_["links"]) == {"s1-s2", "s2-s4", "s1-s3", "s3-s4"}
            assert "observedAt" in round_


def test_diamond_initial_policy_and_probe_payload_use_independent_paths() -> None:
    from examples.diamond_recovery import (
        INITIAL_RULES,
        PATHS,
        collect_observation_round,
        static_neighbors,
    )
    from mininet_ai.substrates import FakeSubstrateRuntime

    class FakeDiamondSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__(run_id_factory=lambda: "diamond-fake")
            self.probe_calls = []

        def probe(self, source, destination, source_ip, destination_ip, timeout):
            self.probe_calls.append(
                (source, destination, source_ip, destination_ip, timeout)
            )
            return {
                "sent": 1,
                "received": 1,
                "state": "success",
                "observedAt": "2026-10-28T12:00:00Z",
            }

    assert len(INITIAL_RULES) == 18
    assert {rule["priority"] for rule in INITIAL_RULES} == {100}
    assert {rule["hardTimeout"] for rule in INITIAL_RULES} == {0}
    assert [rule["cookie"] for rule in INITIAL_RULES] == list(range(11101, 11119))
    assert [rule["path"] for rule in INITIAL_RULES].count("primary") == 12
    assert [rule["path"] for rule in INITIAL_RULES].count("recovery") == 6
    assert PATHS == {
        "primary": ("h1", "s1", "s2", "s4", "h2"),
        "recovery": ("h1", "s1", "s3", "s4", "h2"),
    }
    assert static_neighbors() == {
        "h1": {
            "10.0.0.2": "02:00:00:00:00:02",
            "10.0.0.12": "02:00:00:00:00:02",
            "10.0.0.22": "02:00:00:00:00:02",
        },
        "h2": {
            "10.0.0.1": "02:00:00:00:00:01",
            "10.0.0.11": "02:00:00:00:00:01",
            "10.0.0.21": "02:00:00:00:00:01",
        },
    }

    substrate = FakeDiamondSubstrate()
    substrate.deploy(
        compile_experiment(EXAMPLES_DIR / "diamond-recovery/experiment.yaml")
    )

    payload = collect_observation_round(
        substrate.probe,
        observed_at="2026-10-28T12:00:00Z",
        link_states={"s1-s2": "up", "s1-s3": "up", "s2-s4": "up", "s3-s4": "up"},
        policy_state="primary",
        previous_outcomes=[],
    )
    assert (
        len(substrate.probe_calls) == 6
    )  # four diagnostics and two active confirmations
    assert {call[2] for call in substrate.probe_calls} == {
        "10.0.0.1",
        "10.0.0.2",
        "10.0.0.11",
        "10.0.0.12",
        "10.0.0.21",
        "10.0.0.22",
    }
    assert all(call[-1] == 1 for call in substrate.probe_calls)
    assert len(payload["pathProbes"]) == 4
    assert len(payload["activeConfirmation"]) == 2
    assert payload["paths"] == {
        "primary": ["h1", "s1", "s2", "s4", "h2"],
        "recovery": ["h1", "s1", "s3", "s4", "h2"],
    }
    assert all(
        probe["observedAt"] == "2026-10-28T12:00:00Z" for probe in payload["pathProbes"]
    )
    assert payload["linkStates"] == {
        "s1-s2": {"state": "up", "observedAt": "2026-10-28T12:00:00Z"},
        "s1-s3": {"state": "up", "observedAt": "2026-10-28T12:00:00Z"},
        "s2-s4": {"state": "up", "observedAt": "2026-10-28T12:00:00Z"},
        "s3-s4": {"state": "up", "observedAt": "2026-10-28T12:00:00Z"},
    }


def test_diamond_history_requires_three_consecutive_complete_scheduled_rounds() -> None:
    from examples.diamond_recovery import collect_observation_round

    def successful_probe(*_args):
        return {"sent": 1, "received": 1, "state": "success"}

    link_states = {name: "up" for name in ("s1-s2", "s1-s3", "s2-s4", "s3-s4")}
    first = collect_observation_round(
        successful_probe,
        observed_at="2026-10-28T12:00:00Z",
        link_states=link_states,
        policy_state="primary",
        previous_outcomes=[],
        round_number=1,
        scheduled_at="2026-10-28T12:00:00Z",
    )
    assert len(first["history"]["completedRounds"]) == 1
    assert first["history"]["gaps"] == [
        {"code": "history.insufficient-complete-rounds", "required": 3, "available": 1}
    ]
    second = collect_observation_round(
        successful_probe,
        observed_at="2026-10-28T12:00:01Z",
        link_states=link_states,
        policy_state="primary",
        previous_outcomes=[],
        previous_rounds=(first,),
        round_number=2,
        scheduled_at="2026-10-28T12:00:01Z",
    )
    third = collect_observation_round(
        successful_probe,
        observed_at="2026-10-28T12:00:02Z",
        link_states=link_states,
        policy_state="primary",
        previous_outcomes=[],
        previous_rounds=(first, second),
        round_number=3,
        scheduled_at="2026-10-28T12:00:02Z",
    )
    assert [
        round_data["roundNumber"] for round_data in third["history"]["completedRounds"]
    ] == [1, 2, 3]
    assert third["history"]["gaps"] == []
    assert third["invocationSnapshot"]["scenario"] == "diamond-recovery"
    assert third["invocationSnapshot"]["inputs"]["history"]["requiredRounds"] == 3
    frozen_inputs = third["invocationSnapshot"]["inputs"]
    third["policyState"] = "mutated-after-assembly"
    assert frozen_inputs["policyState"] == "primary"

    def incomplete_probe(source, _destination, _source_ip, _destination_ip, _timeout):
        if source == "h1":
            return {"sent": None, "received": None, "state": "unknown"}
        return {"sent": 1, "received": 1, "state": "success"}

    fourth = collect_observation_round(
        incomplete_probe,
        observed_at="2026-10-28T12:00:03Z",
        link_states=link_states,
        policy_state="primary",
        previous_outcomes=[],
        previous_rounds=(second, third),
        round_number=4,
        scheduled_at="2026-10-28T12:00:03Z",
    )
    assert len(fourth["history"]["completedRounds"]) == 2
    assert any(gap["code"] == "round.incomplete" for gap in fourth["history"]["gaps"])
    assert any(probe["sent"] is None for probe in fourth["pathProbes"])


def test_examples_validate_and_plan() -> None:
    """All maintained examples validate and plan without errors."""
    for name in EXAMPLE_NAMES:
        experiment_path = EXAMPLES_DIR / name / "experiment.yaml"
        experiment = load_experiment(experiment_path)
        plan = compile_experiment(experiment)
        assert plan is not None
        assert plan.snapshot is not None


def test_getting_started_has_one_agent() -> None:
    """The getting-started example has exactly one agent."""
    experiment_path = EXAMPLES_DIR / "getting-started" / "experiment.yaml"
    experiment = load_experiment(experiment_path)
    plan = compile_experiment(experiment)
    assert len(plan.agents) == 1


def test_autonomous_operation_has_four_agents() -> None:
    """The autonomous-operation example has four agent instances."""
    experiment_path = EXAMPLES_DIR / "autonomous-operation" / "experiment.yaml"
    experiment = load_experiment(experiment_path)
    plan = compile_experiment(experiment)
    assert len(plan.agents) == 4


def test_autonomous_operation_shares_a_model_without_changing_responses() -> None:
    plan = compile_experiment(EXAMPLES_DIR / "autonomous-operation/experiment.yaml")
    assert {agent.id for agent in plan.agents} == {
        "switch-monitor@s1",
        "switch-monitor@s2",
        "host-traffic@h1",
        "host-traffic@h2",
    }
    assert set(plan.model_provenance) == {"switch-monitor", "host-traffic"}
    assert {source.entry for source in plan.model_provenance.values()} == {
        "shared-operation"
    }
    assert len({source.file for source in plan.model_provenance.values()}) == 1
    catalog = ExecutionCatalog(plan)
    for agent_id in catalog.agent_ids:
        model = catalog.resolve(agent_id).blueprint.model
        assert isinstance(model, ModelConfiguration)
        assert model.provider == "mock"
        assert model.name == "deterministic"
        assert model.parameters["usage"] == {}
        response = model.parameters["response"]
        if agent_id.startswith("switch-monitor"):
            assert response == {
                "message": "Congestion detected. Proposing flow installation to reroute traffic.",
                "proposals": [
                    {
                        "id": "reroute-flow",
                        "capability": "openflow.flow.install",
                        "target": "s1",
                        "arguments": {
                            "match": {"ip": True, "nw_dst": "10.0.0.13"},
                            "actions": ["output:3"],
                        },
                        "reason": "Reroute traffic to alternate path via s1-eth3.",
                        "timeoutSeconds": 30,
                    }
                ],
                "delegations": [],
                "sharedStateUpdates": [],
                "metadata": {},
            }
        else:
            assert response == {
                "message": "Traffic generation complete.",
                "proposals": [],
                "delegations": [],
                "sharedStateUpdates": [],
                "metadata": {},
            }
    assert plan.model_provenance["switch-monitor"].overrides == {}
    assert set(plan.model_provenance["host-traffic"].overrides["parameters"]) == {
        "response"
    }


def test_hierarchical_routing_has_six_agents() -> None:
    """The hierarchical-routing example includes its traffic server."""
    experiment_path = EXAMPLES_DIR / "hierarchical-routing" / "experiment.yaml"
    experiment = load_experiment(experiment_path)
    plan = compile_experiment(experiment)
    assert len(plan.agents) == 6
    assert {agent.id for agent in plan.agents} == {
        "global-orchestrator@network",
        "switch-router@s1",
        "switch-router@s2",
        "host-traffic@h1",
        "host-traffic@h2",
        "host-server@h3",
    }


def test_hierarchical_flow_contract_matches_native_openflow_arguments() -> None:
    plan = compile_experiment(EXAMPLES_DIR / "hierarchical-routing/experiment.yaml")
    definition = ExecutionCatalog(plan).resolve("switch-router@s1")
    capability = next(
        item
        for item in definition.capabilities
        if item.metadata.name == "openflow.flow.install"
    )
    validator = Draft202012Validator(capability.input_schema)

    # ovs-ofctl consumes actions as one comma-separated string, not a JSON array.
    validator.validate({"match": {"in_port": 1}, "actions": "output:2"})
    assert not validator.is_valid({"actions": "output:2"})
    assert not validator.is_valid({"match": {}, "actions": ["output:2"]})
    assert not validator.is_valid({"match": {}, "actions": ""})


def test_hierarchical_routing_has_a_scoped_traffic_server() -> None:
    """TCP clients need a managed listener on the destination host."""
    plan = compile_experiment(EXAMPLES_DIR / "hierarchical-routing/experiment.yaml")
    servers = [agent for agent in plan.agents if agent.id == "host-server@h3"]
    assert len(servers) == 1, "h3 has no agent to start the iperf listener"
    definition = ExecutionCatalog(plan).resolve("host-server@h3")
    assert any(
        capability.metadata.name == "host.process.start"
        for capability in definition.capabilities
    )


@pytest.mark.parametrize(
    ("actions", "satisfied"),
    [("NORMAL", True), ("drop", False), ("output:2", False), (None, False)],
)
def test_hierarchical_flow_postcondition_uses_native_telemetry(
    actions: str | None,
    satisfied: bool,
) -> None:
    plan = compile_experiment(EXAMPLES_DIR / "hierarchical-routing/experiment.yaml")
    definition = ExecutionCatalog(plan).resolve("switch-router@s1")
    flow = MininetOVSObservations._parse_flow(
        "cookie=0x0, duration=1s, table=0, n_packets=0, n_bytes=0, "
        f"priority=32768 actions={actions}"
    )

    class Observer:
        def observe(self, run_id: str, query: ObservationQuery) -> ObservationResult:
            return ObservationResult(
                run_id=run_id,
                query=query,
                observed_at=datetime.now(UTC),
                values={
                    "s1": {
                        "switches": [
                            {
                                "name": "s1",
                                "flows": [flow] if actions is not None else [],
                            }
                        ]
                    }
                },
            )

    context = AgentContext(
        invocationId="check-flow",
        runId="test-run",
        agentId="switch-router@s1",
        deployment="switch-router",
        layer=AttachmentLayer.DATA,
        targetKind=ResourceKind.SWITCH,
        targets=("s1",),
        intent="Install policy",
        invokedAt=datetime.now(UTC),
    )
    ticks = iter(range(100))
    verifier = PostconditionVerifier(
        Observer(), monotonic_clock=lambda: float(next(ticks)), sleeper=lambda _: None
    )
    report = verifier.verify(
        context,
        ActionProposal(
            id="policy-flow",
            capability="openflow.flow.install",
            target="s1",
            arguments={"match": {}, "actions": "NORMAL"},
        ),
        tuple(definition.capabilities[0].postconditions),
    )
    assert report.satisfied is satisfied, report.checks


def test_hierarchical_routing_uses_requested_ollama_configuration() -> None:
    plan = compile_experiment(EXAMPLES_DIR / "hierarchical-routing/experiment.yaml")
    catalog = ExecutionCatalog(plan)
    for agent in plan.agents:
        blueprint = catalog.resolve(agent.id).blueprint
        assert blueprint.implementation.type == "python"
        assert blueprint.implementation.entrypoint == (
            "mininet_ai.agents.agno.ollama_factory:create_deterministic_prompt_parsed_agent"
        )
        model = blueprint.model
        from mininet_ai.specification.models import ModelConfiguration

        assert isinstance(model, ModelConfiguration)
        assert model.provider == "ollama"
        assert model.name == "qwen3.5:latest"
        assert model.parameters["host"] == "http://10.10.10.201:11434"
        assert model.parameters["think"] is False


def test_iperf_throughput_has_two_agents() -> None:
    """The iperf-throughput example has two agent instances."""
    experiment_path = EXAMPLES_DIR / "iperf-throughput" / "experiment.yaml"
    experiment = load_experiment(experiment_path)
    plan = compile_experiment(experiment)
    assert len(plan.agents) == 2
