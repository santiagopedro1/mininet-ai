from __future__ import annotations

import unittest

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import CompilationError
from tests.compiler.helpers import example_snapshot, experiment_from, named


class CompilerReferenceTests(unittest.TestCase):
    def test_unknown_blueprint_is_rejected(self) -> None:
        snapshot = example_snapshot()
        snapshot["agents"][0]["blueprint"] = "missing-blueprint"

        with self.assertRaisesRegex(CompilationError, "unknown blueprint"):
            compile_experiment(experiment_from(snapshot))

    def test_explicit_selector_name_must_exist_at_selected_kind(self) -> None:
        snapshot = example_snapshot()
        global_agent = named(snapshot["agents"], "global-router")
        global_agent["placement"]["targets"]["names"] = ["s1"]

        with self.assertRaisesRegex(
            CompilationError, "missing or not network resources: s1"
        ):
            compile_experiment(experiment_from(snapshot))

    def test_unknown_resource_parent_is_rejected(self) -> None:
        snapshot = example_snapshot()
        resources = snapshot["substrate"]["topology"]["resources"]
        named(resources, "h1")["parent"] = "missing-parent"

        with self.assertRaisesRegex(CompilationError, "unknown parent 'missing-parent'"):
            compile_experiment(experiment_from(snapshot))

    def test_unknown_link_endpoint_node_is_rejected(self) -> None:
        snapshot = example_snapshot()
        endpoint = snapshot["substrate"]["topology"]["links"][0]["endpoints"][0]
        endpoint["node"] = "missing-node"

        with self.assertRaisesRegex(CompilationError, "unknown endpoint node"):
            compile_experiment(experiment_from(snapshot))

    def test_unknown_link_adapter_is_rejected(self) -> None:
        snapshot = example_snapshot()
        endpoint = snapshot["substrate"]["topology"]["links"][0]["endpoints"][0]
        endpoint["adapter"] = "missing-adapter"

        with self.assertRaisesRegex(CompilationError, "unknown adapter"):
            compile_experiment(experiment_from(snapshot))

    def test_link_adapter_must_belong_to_endpoint_node(self) -> None:
        snapshot = example_snapshot()
        endpoint = snapshot["substrate"]["topology"]["links"][0]["endpoints"][0]
        endpoint["adapter"] = "h2-eth0"

        with self.assertRaisesRegex(CompilationError, "does not belong to 'h1'"):
            compile_experiment(experiment_from(snapshot))


class ResourceGraphValidationTests(unittest.TestCase):
    def test_self_parent_cycle_is_rejected(self) -> None:
        snapshot = example_snapshot()
        resources = snapshot["substrate"]["topology"]["resources"]
        named(resources, "h1")["parent"] = "h1"

        with self.assertRaisesRegex(CompilationError, "parent cycle detected at 'h1'"):
            compile_experiment(experiment_from(snapshot))

    def test_multi_resource_parent_cycle_is_rejected(self) -> None:
        snapshot = example_snapshot()
        resources = snapshot["substrate"]["topology"]["resources"]
        named(resources, "network")["parent"] = "h1"
        named(resources, "h1")["parent"] = "network"

        with self.assertRaisesRegex(CompilationError, "parent cycle detected"):
            compile_experiment(experiment_from(snapshot))


class ResourceLimitTests(unittest.TestCase):
    def test_expanded_instance_count_cannot_exceed_limit(self) -> None:
        snapshot = example_snapshot()
        snapshot["resourceLimits"]["max-instances"] = 5

        with self.assertRaisesRegex(
            CompilationError, "compiled 6 agent instances, exceeding max-instances 5"
        ):
            compile_experiment(experiment_from(snapshot))

    def test_expanded_instance_count_may_equal_limit(self) -> None:
        snapshot = example_snapshot()
        snapshot["resourceLimits"]["max-instances"] = 6

        plan = compile_experiment(experiment_from(snapshot))

        self.assertEqual(len(plan.agents), 6)


class ObserverValidationTests(unittest.TestCase):
    def test_read_only_observer_compiles(self) -> None:
        snapshot = example_snapshot()
        observer = named(snapshot["agents"], "host-router")
        observer["placement"]["layer"] = "observer"
        observer["placement"]["runtime"] = "process"

        plan = compile_experiment(experiment_from(snapshot))
        instances = [
            agent for agent in plan.agents if agent.deployment == "host-router"
        ]

        self.assertEqual(len(instances), 2)
        self.assertTrue(all(not instance.capabilities for instance in instances))

    def test_observer_with_capability_is_rejected(self) -> None:
        snapshot = example_snapshot()
        observer = named(snapshot["agents"], "switch-router")
        observer["placement"]["layer"] = "observer"
        observer["placement"]["runtime"] = "process"

        with self.assertRaisesRegex(
            CompilationError, "observer agent 'switch-router' cannot have capabilities"
        ):
            compile_experiment(experiment_from(snapshot))

    def test_unavailable_observation_is_rejected(self) -> None:
        snapshot = example_snapshot()
        global_agent = named(snapshot["agents"], "global-router")
        global_agent["observe"] = ["host.interfaces"]

        with self.assertRaisesRegex(
            CompilationError,
            "observation 'host.interfaces' is not available at layer 'global'",
        ):
            compile_experiment(experiment_from(snapshot))


if __name__ == "__main__":
    unittest.main()
