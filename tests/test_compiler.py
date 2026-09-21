from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import CompilationError
from mininet_ai.specification.models import Experiment


ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "phase1" / "experiment.yaml"


class CompilerTests(unittest.TestCase):
    def test_acceptance_example_compiles_same_blueprint_at_four_layers(self) -> None:
        plan = compile_experiment(EXAMPLE)

        self.assertEqual(len(plan.agents), 6)
        self.assertEqual({agent.blueprint for agent in plan.agents}, {"local-router"})
        self.assertEqual(
            {agent.attachment.layer.value for agent in plan.agents},
            {"global", "control", "data", "host"},
        )
        self.assertEqual(
            [agent.id for agent in plan.agents if agent.deployment == "switch-router"],
            ["switch-router@s1", "switch-router@s2"],
        )

    def test_compilation_is_deterministic(self) -> None:
        first = compile_experiment(EXAMPLE)
        second = compile_experiment(EXAMPLE)

        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.model_dump(), second.model_dump())

    def test_python_specification_api_compiles_normalized_snapshot(self) -> None:
        yaml_plan = compile_experiment(EXAMPLE)
        specification = Experiment.model_validate(yaml_plan.snapshot)

        python_plan = compile_experiment(specification)

        self.assertEqual(python_plan.digest, yaml_plan.digest)
        self.assertEqual(python_plan.agents, yaml_plan.agents)

    def test_capability_effects_become_least_privilege_set(self) -> None:
        plan = compile_experiment(EXAMPLE)
        switches = [
            agent for agent in plan.agents if agent.deployment == "switch-router"
        ]

        self.assertTrue(switches)
        self.assertTrue(all(agent.privileges == ("dataplane.write",) for agent in switches))

    def test_per_group_cardinality_collects_targets_by_label(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        resources = snapshot["substrate"]["topology"]["resources"]
        next(item for item in resources if item["name"] == "s2")["labels"][
            "region"
        ] = "west"
        deployment = next(
            item for item in snapshot["agents"] if item["name"] == "switch-router"
        )
        deployment["placement"]["cardinality"] = "per-group"
        deployment["placement"]["groupBy"] = "region"

        plan = compile_experiment(Experiment.model_validate(snapshot))
        grouped = next(
            item for item in plan.agents if item.deployment == "switch-router"
        )

        self.assertEqual(grouped.id, "switch-router@region-west")
        self.assertEqual(grouped.attachment.targets, ("s1", "s2"))

    def test_centralized_coordination_expands_to_instance_edges(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        snapshot["coordination"] = {
            "mode": "centralized",
            "coordinator": "global-router",
        }

        plan = compile_experiment(Experiment.model_validate(snapshot))

        self.assertEqual(len(plan.coordination.edges), 5)
        self.assertEqual(
            {edge.source for edge in plan.coordination.edges}, {"global-router"}
        )

    def test_registered_custom_layer_compiles(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        snapshot["substrate"]["options"] = {
            "custom-layers": [
                {
                    "name": "experimental",
                    "targets": ["switch"],
                    "runtimes": ["edge-runtime"],
                }
            ]
        }
        deployment = next(
            item for item in snapshot["agents"] if item["name"] == "switch-router"
        )
        deployment["placement"]["layer"] = "custom"
        deployment["placement"]["custom-layer"] = "experimental"
        deployment["placement"]["runtime"] = "edge-runtime"
        snapshot["capabilityDefinitions"][0]["layers"].append("experimental")

        plan = compile_experiment(Experiment.model_validate(snapshot))

        custom = [item for item in plan.agents if item.deployment == "switch-router"]
        self.assertEqual(len(custom), 2)
        self.assertTrue(
            all(item.attachment.custom_layer == "experimental" for item in custom)
        )

    def test_invalid_layer_target_is_rejected(self) -> None:
        source = EXAMPLE.read_text().replace("layer: data", "layer: host", 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experiment.yaml"
            # Make referenced paths absolute because this temporary experiment moved.
            source = source.replace(
                "./agent-blueprints/local-router.yaml",
                str(EXAMPLE.parent / "agent-blueprints" / "local-router.yaml"),
            ).replace(
                "./capabilities/openflow-flow-install.yaml",
                str(EXAMPLE.parent / "capabilities" / "openflow-flow-install.yaml"),
            )
            path.write_text(source)

            with self.assertRaisesRegex(CompilationError, "cannot target 'switch'"):
                compile_experiment(path)

    def test_unknown_capability_is_rejected(self) -> None:
        source = EXAMPLE.read_text().replace(
            "capabilities: [openflow.flow.install]",
            "capabilities: [missing.capability]",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experiment.yaml"
            source = source.replace(
                "./agent-blueprints/local-router.yaml",
                str(EXAMPLE.parent / "agent-blueprints" / "local-router.yaml"),
            ).replace(
                "./capabilities/openflow-flow-install.yaml",
                str(EXAMPLE.parent / "capabilities" / "openflow-flow-install.yaml"),
            )
            path.write_text(source)

            with self.assertRaisesRegex(CompilationError, "unknown capabilities"):
                compile_experiment(path)


if __name__ == "__main__":
    unittest.main()
