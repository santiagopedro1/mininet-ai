from __future__ import annotations

import unittest

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import CompilationError
from tests.compiler.helpers import example_snapshot, experiment_from


class CoordinationCompilerTests(unittest.TestCase):
    def test_hierarchical_relationships_expand_to_instance_edges(self) -> None:
        snapshot = example_snapshot()
        snapshot["coordination"] = {
            "mode": "hierarchical",
            "relationships": [
                {"source": "global-router", "targets": ["control-router"]},
                {"source": "control-router", "targets": ["switch-router"]},
            ],
        }

        plan = compile_experiment(experiment_from(snapshot))
        edges = {
            (edge.source, edge.target, edge.relationship)
            for edge in plan.coordination.edges
        }

        self.assertEqual(
            edges,
            {
                ("global-router", "control-router@control-domain", "parent"),
                ("control-router@control-domain", "switch-router@s1", "parent"),
                ("control-router@control-domain", "switch-router@s2", "parent"),
            },
        )

    def test_distributed_all_creates_complete_directed_peer_graph(self) -> None:
        snapshot = example_snapshot()
        snapshot["coordination"] = {"mode": "distributed", "peers": "all"}

        plan = compile_experiment(experiment_from(snapshot))
        pairs = [(edge.source, edge.target) for edge in plan.coordination.edges]

        self.assertEqual(len(pairs), 30)
        self.assertEqual(len(set(pairs)), 30)
        self.assertTrue(all(source != target for source, target in pairs))
        self.assertTrue(
            all(edge.relationship == "peer" for edge in plan.coordination.edges)
        )

    def test_unknown_centralized_coordinator_is_rejected(self) -> None:
        snapshot = example_snapshot()
        snapshot["coordination"] = {
            "mode": "centralized",
            "coordinator": "missing-agent",
        }

        with self.assertRaisesRegex(
            CompilationError, "unknown coordinator deployment: 'missing-agent'"
        ):
            compile_experiment(experiment_from(snapshot))

    def test_centralized_coordinator_must_be_singleton(self) -> None:
        snapshot = example_snapshot()
        snapshot["coordination"] = {
            "mode": "centralized",
            "coordinator": "switch-router",
        }

        with self.assertRaisesRegex(
            CompilationError, "centralized coordinator must compile to one instance"
        ):
            compile_experiment(experiment_from(snapshot))

    def test_unknown_hierarchical_source_and_target_are_rejected(self) -> None:
        cases = (
            (
                {"source": "missing-parent", "targets": ["switch-router"]},
                "unknown coordination source deployment: 'missing-parent'",
            ),
            (
                {"source": "global-router", "targets": ["missing-child"]},
                "unknown coordination target deployment: 'missing-child'",
            ),
        )
        for relationship, message in cases:
            with self.subTest(relationship=relationship):
                snapshot = example_snapshot()
                snapshot["coordination"] = {
                    "mode": "hierarchical",
                    "relationships": [relationship],
                }
                with self.assertRaisesRegex(CompilationError, message):
                    compile_experiment(experiment_from(snapshot))


if __name__ == "__main__":
    unittest.main()
