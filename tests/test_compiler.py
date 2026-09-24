from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import CompilationError
from mininet_ai.specification.models import Experiment, ResourceKind

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

    def test_topology_compiles_to_mininet_ready_resources(self) -> None:
        plan = compile_experiment(EXAMPLE)
        resources: dict[str, Any] = {
            resource.name: resource for resource in plan.resources
        }

        self.assertEqual(len(resources), 16)
        self.assertEqual(resources["c0"].kind, ResourceKind.CONTROLLER)
        self.assertEqual(resources["c0"].port, 6653)
        self.assertEqual(resources["control-domain"].controllers, ("c0",))
        self.assertEqual(resources["s1"].fail_mode.value, "secure")
        self.assertEqual(resources["s1"].controllers, ("c0",))
        self.assertEqual(resources["s1"].protocols[0].value, "OpenFlow13")

        self.assertEqual(resources["h1-eth0"].kind, ResourceKind.PORT)
        self.assertEqual(resources["h1-eth0"].role, "host-interface")
        self.assertEqual(resources["h1-eth0"].ipv4, "10.0.0.1/24")
        self.assertEqual(resources["h1-eth0"].mac, "02:00:00:00:00:01")
        self.assertEqual(resources["s1-eth2"].number, 2)

        link = resources["s1-s2"]
        self.assertEqual(link.endpoints, ("s1-eth2", "s2-eth1"))
        self.assertEqual(link.bandwidth, 1000)
        self.assertEqual(link.delay, "1ms")
        self.assertEqual(link.jitter, "100us")
        self.assertEqual(link.loss, 0.1)

    def test_omitted_adapters_are_created_deterministically(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        topology = snapshot["substrate"]["topology"]
        for resource in topology["resources"]:
            if resource["kind"] == "host":
                resource["interfaces"] = []
            elif resource["kind"] == "switch":
                resource["ports"] = []
        for link in topology["links"]:
            for endpoint in link["endpoints"]:
                endpoint["adapter"] = None

        plan = compile_experiment(Experiment.model_validate(snapshot))
        resources: dict[str, Any] = {
            resource.name: resource for resource in plan.resources
        }

        self.assertEqual(resources["h1-s1"].endpoints, ("h1-eth0", "s1-eth1"))
        self.assertEqual(resources["s1-s2"].endpoints, ("s1-eth2", "s2-eth1"))
        self.assertEqual(resources["s2-h2"].endpoints, ("s2-eth2", "h2-eth0"))
        self.assertEqual(resources["h2-eth0"].ipv4, "10.0.0.2/24")

    def test_explicit_addresses_are_preserved_and_auto_allocation_skips_them(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        resources = snapshot["substrate"]["topology"]["resources"]
        h1 = next(resource for resource in resources if resource["name"] == "h1")
        h1["interfaces"][0]["ipv4"] = "10.0.0.10/24"
        h1["interfaces"][0]["mac"] = "02:00:00:00:00:aa"

        plan = compile_experiment(Experiment.model_validate(snapshot))
        planned: dict[str, Any] = {
            resource.name: resource for resource in plan.resources
        }

        self.assertEqual(planned["h1-eth0"].ipv4, "10.0.0.10/24")
        self.assertEqual(planned["h1-eth0"].mac, "02:00:00:00:00:aa")
        self.assertEqual(planned["h2-eth0"].ipv4, "10.0.0.1/24")
        self.assertEqual(planned["h2-eth0"].mac, "02:00:00:00:00:01")

    def test_unknown_controller_is_rejected(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        resources = snapshot["substrate"]["topology"]["resources"]
        switch = next(resource for resource in resources if resource["name"] == "s1")
        switch["controllers"] = ["missing-controller"]

        with self.assertRaisesRegex(CompilationError, "unknown controller"):
            compile_experiment(Experiment.model_validate(snapshot))

    def test_unknown_substrate_driver_is_rejected_by_registry(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        snapshot["substrate"]["driver"] = "missing-driver"

        with self.assertRaisesRegex(
            CompilationError, "unknown substrate driver.*available: fake"
        ):
            compile_experiment(Experiment.model_validate(snapshot))

    def test_invalid_substrate_options_are_rejected_before_compilation(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        snapshot["substrate"]["options"] = {"unknown-option": True}

        with self.assertRaisesRegex(
            CompilationError, "options.unknown-option.*unknown fake substrate option"
        ):
            compile_experiment(Experiment.model_validate(snapshot))

    def test_adapter_cannot_be_reused_by_multiple_links(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        links = snapshot["substrate"]["topology"]["links"]
        links[1]["endpoints"][0]["adapter"] = "s1-eth1"

        with self.assertRaisesRegex(CompilationError, "more than one link"):
            compile_experiment(Experiment.model_validate(snapshot))

    def test_explicit_address_must_belong_to_allocation_subnet(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        resources = snapshot["substrate"]["topology"]["resources"]
        host = next(resource for resource in resources if resource["name"] == "h1")
        host["interfaces"][0]["ipv4"] = "192.168.1.10/24"

        with self.assertRaisesRegex(CompilationError, "outside 10.0.0.0/24"):
            compile_experiment(Experiment.model_validate(snapshot))

    def test_duplicate_ip_and_mac_allocations_are_rejected(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        hosts = [
            resource
            for resource in snapshot["substrate"]["topology"]["resources"]
            if resource["kind"] == "host"
        ]
        for host in hosts:
            host["interfaces"][0]["ipv4"] = "10.0.0.10/24"
            host["interfaces"][0]["mac"] = "02:00:00:00:00:10"

        with self.assertRaisesRegex(CompilationError, "share address"):
            compile_experiment(Experiment.model_validate(snapshot))

        hosts[1]["interfaces"][0]["ipv4"] = "10.0.0.11/24"
        with self.assertRaisesRegex(CompilationError, "share MAC"):
            compile_experiment(Experiment.model_validate(snapshot))

    def test_remote_controller_and_link_constraints_are_schema_validated(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        resources = snapshot["substrate"]["topology"]["resources"]
        controller = next(
            resource for resource in resources if resource["kind"] == "controller"
        )
        controller["type"] = "remote"
        controller["address"] = None

        with self.assertRaisesRegex(ValidationError, "requires an address"):
            Experiment.model_validate(snapshot)

        controller["address"] = "127.0.0.1"
        snapshot["substrate"]["topology"]["links"][0]["loss"] = 101
        with self.assertRaisesRegex(ValidationError, "less than or equal to 100"):
            Experiment.model_validate(snapshot)

    def test_topology_neighbor_coordination_uses_port_owners(self) -> None:
        snapshot = compile_experiment(EXAMPLE).snapshot
        snapshot["coordination"] = {
            "mode": "distributed",
            "peers": "topology-neighbors",
        }

        plan = compile_experiment(Experiment.model_validate(snapshot))
        edges = {(edge.source, edge.target) for edge in plan.coordination.edges}

        self.assertIn(("switch-router@s1", "switch-router@s2"), edges)
        self.assertIn(("switch-router@s2", "switch-router@s1"), edges)
        self.assertIn(("host-router@h1", "switch-router@s1"), edges)
        self.assertIn(("switch-router@s2", "host-router@h2"), edges)

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
                    "observations": [
                        "ovs.port-counters",
                        "tc.queue-occupancy",
                        "topology.neighbors",
                    ],
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
