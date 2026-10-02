"""Tests for maintained examples.

Each example is validated and planned to catch regressions as the compiler
evolves. Golden plans are not used for examples — they are meant to be
readable, not frozen.
"""

from pathlib import Path

from mininet_ai.compiler import compile_experiment
from mininet_ai.specification import load_experiment

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


def test_hierarchical_routing_has_five_agents() -> None:
    """The hierarchical-routing example has five agent instances."""
    experiment_path = EXAMPLES_DIR / "hierarchical-routing" / "experiment.yaml"
    experiment = load_experiment(experiment_path)
    plan = compile_experiment(experiment)
    assert len(plan.agents) == 5


def test_iperf_throughput_has_two_agents() -> None:
    """The iperf-throughput example has two agent instances."""
    experiment_path = EXAMPLES_DIR / "iperf-throughput" / "experiment.yaml"
    experiment = load_experiment(experiment_path)
    plan = compile_experiment(experiment)
    assert len(plan.agents) == 2
