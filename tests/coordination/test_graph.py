from __future__ import annotations

import unittest

from mininet_ai.compiler import compile_experiment
from mininet_ai.compiler.models import CoordinationEdge, CoordinationPlan
from mininet_ai.coordination import CoordinationGraph, CoordinationGraphError
from mininet_ai.specification.models import CoordinationMode
from tests.compiler.helpers import example_snapshot, experiment_from


def graph_for(configuration: dict[str, object]) -> CoordinationGraph:
    snapshot = example_snapshot()
    snapshot["coordination"] = configuration
    return CoordinationGraph(compile_experiment(experiment_from(snapshot)))


class CoordinationGraphTests(unittest.TestCase):
    def test_independent_intent_enters_at_requested_agent(self) -> None:
        graph = graph_for({"mode": "independent"})

        self.assertEqual(
            graph.entry_agent("switch-router@s1"),
            "switch-router@s1",
        )
        self.assertEqual(graph.destinations("switch-router@s1"), ())
        with self.assertRaises(CoordinationGraphError) as denied:
            graph.authorize("switch-router@s1", "switch-router@s2")
        self.assertEqual(
            denied.exception.code,
            "coordination.graph.delivery-denied",
        )

    def test_centralized_intent_enters_at_single_coordinator(self) -> None:
        graph = graph_for(
            {"mode": "centralized", "coordinator": "global-router"}
        )

        self.assertEqual(
            graph.entry_agent("switch-router@s1"),
            "global-router",
        )
        self.assertEqual(
            graph.destinations("global-router"),
            (
                "control-router@control-domain",
                "host-router@h1",
                "host-router@h2",
                "switch-router@s1",
                "switch-router@s2",
            ),
        )
        edge = graph.authorize("global-router", "switch-router@s1")
        self.assertEqual(edge.relationship, "coordinates")

    def test_singleton_centralized_graph_needs_no_synthetic_edge(self) -> None:
        snapshot = example_snapshot()
        snapshot["agents"] = [snapshot["agents"][0]]
        snapshot["coordination"] = {
            "mode": "centralized",
            "coordinator": "global-router",
        }

        graph = CoordinationGraph(
            compile_experiment(experiment_from(snapshot))
        )

        self.assertEqual(graph.entry_agent("global-router"), "global-router")
        self.assertEqual(graph.destinations("global-router"), ())

    def test_hierarchical_delivery_is_directed(self) -> None:
        graph = graph_for(
            {
                "mode": "hierarchical",
                "relationships": [
                    {"source": "global-router", "targets": ["control-router"]},
                    {"source": "control-router", "targets": ["switch-router"]},
                ],
            }
        )

        edge = graph.authorize(
            "control-router@control-domain",
            "switch-router@s1",
        )
        self.assertEqual(edge.relationship, "parent")
        with self.assertRaises(CoordinationGraphError) as reverse:
            graph.authorize(
                "switch-router@s1",
                "control-router@control-domain",
            )
        self.assertEqual(
            reverse.exception.code,
            "coordination.graph.delivery-denied",
        )

    def test_topology_neighbor_peers_allow_only_compiled_neighbors(self) -> None:
        graph = graph_for(
            {"mode": "distributed", "peers": "topology-neighbors"}
        )

        self.assertEqual(
            graph.authorize("host-router@h1", "switch-router@s1").relationship,
            "peer",
        )
        self.assertEqual(
            graph.authorize("switch-router@s1", "host-router@h1").relationship,
            "peer",
        )
        with self.assertRaises(CoordinationGraphError):
            graph.authorize("global-router", "control-router@control-domain")

    def test_unknown_entry_agent_is_rejected_with_typed_issue(self) -> None:
        graph = graph_for({"mode": "independent"})

        with self.assertRaises(CoordinationGraphError) as raised:
            graph.entry_agent("missing-agent")

        self.assertEqual(raised.exception.code, "coordination.graph.agent-unknown")
        self.assertEqual(raised.exception.target, "missing-agent")

    def test_edge_with_unknown_agent_makes_plan_unexecutable(self) -> None:
        plan = compile_experiment(experiment_from(example_snapshot()))
        invalid = plan.model_copy(
            update={
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.HIERARCHICAL,
                    edges=(
                        CoordinationEdge(
                            source="global-router",
                            target="missing-agent",
                            relationship="parent",
                        ),
                    ),
                )
            }
        )

        with self.assertRaises(CoordinationGraphError) as raised:
            CoordinationGraph(invalid)

        self.assertEqual(raised.exception.code, "coordination.graph.invalid-plan")
        self.assertIn("unknown target", str(raised.exception))

    def test_relationship_that_does_not_match_mode_is_rejected(self) -> None:
        plan = compile_experiment(experiment_from(example_snapshot()))
        invalid = plan.model_copy(
            update={
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.HIERARCHICAL,
                    edges=(
                        CoordinationEdge(
                            source="global-router",
                            target="control-router@control-domain",
                            relationship="peer",
                        ),
                    ),
                )
            }
        )

        with self.assertRaisesRegex(
            CoordinationGraphError,
            "requires 'parent' edges",
        ):
            CoordinationGraph(invalid)

    def test_hierarchical_cycle_makes_plan_unexecutable(self) -> None:
        plan = compile_experiment(experiment_from(example_snapshot()))
        invalid = plan.model_copy(
            update={
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.HIERARCHICAL,
                    edges=(
                        CoordinationEdge(
                            source="global-router",
                            target="control-router@control-domain",
                            relationship="parent",
                        ),
                        CoordinationEdge(
                            source="control-router@control-domain",
                            target="global-router",
                            relationship="parent",
                        ),
                    ),
                )
            }
        )

        with self.assertRaisesRegex(CoordinationGraphError, "contains a cycle"):
            CoordinationGraph(invalid)

    def test_asymmetric_peer_graph_makes_plan_unexecutable(self) -> None:
        plan = compile_experiment(experiment_from(example_snapshot()))
        invalid = plan.model_copy(
            update={
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.DISTRIBUTED,
                    edges=(
                        CoordinationEdge(
                            source="global-router",
                            target="control-router@control-domain",
                            relationship="peer",
                        ),
                    ),
                )
            }
        )

        with self.assertRaisesRegex(CoordinationGraphError, "no reverse edge"):
            CoordinationGraph(invalid)

    def test_incomplete_centralized_graph_makes_plan_unexecutable(self) -> None:
        plan = compile_experiment(experiment_from(example_snapshot()))
        invalid = plan.model_copy(
            update={
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.CENTRALIZED,
                    edges=(
                        CoordinationEdge(
                            source="global-router",
                            target="control-router@control-domain",
                            relationship="coordinates",
                        ),
                    ),
                )
            }
        )

        with self.assertRaisesRegex(
            CoordinationGraphError,
            "every other agent",
        ):
            CoordinationGraph(invalid)


if __name__ == "__main__":
    unittest.main()
