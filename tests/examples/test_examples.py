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
        ("s3", 1, ["output:2"]),
        ("s3", 2, ["output:1"]),
        ("s4", 3, ["output:1"]),
        ("s4", 1, ["output:3"]),
        ("s1", 1, ["output:3"]),
        ("s1", 3, ["output:1"]),
        ("s2", 1, ["drop"]),
        ("s2", 2, ["drop"]),
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
        "post-recovery-restraint",
    } <= labels
    assert all(case["expected"] in {"no_action", "install_recovery"} for case in cases)
    assert all(
        case["expected"] == "no_action"
        for case in cases
        if case["label"] != "sustained-primary-failure"
    )


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
