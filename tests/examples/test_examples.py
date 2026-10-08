"""Tests for maintained examples.

Each example is validated and planned to catch regressions as the compiler
evolves. Golden plans are not used for examples — they are meant to be
readable, not frozen.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from mininet_ai.capabilities.verification import PostconditionVerifier
from mininet_ai.compiler import compile_experiment
from mininet_ai.sdk import ActionProposal, AgentContext, ExecutionCatalog
from mininet_ai.specification import load_experiment
from mininet_ai.specification.models import AttachmentLayer, ResourceKind
from mininet_ai.substrates.mininet_ovs.observations import MininetOVSObservations
from mininet_ai.substrates.runtime import ObservationQuery, ObservationResult

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"

EXAMPLE_NAMES = [
    "getting-started",
    "autonomous-operation",
    "hierarchical-routing",
    "iperf-throughput",
]


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
        assert model is not None
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
