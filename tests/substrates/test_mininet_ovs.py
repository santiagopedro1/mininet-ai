from __future__ import annotations

import unittest

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import CompilationError
from mininet_ai.specification.models import Experiment
from mininet_ai.substrates import MininetOVSDriver, substrate_registry
from tests.compiler.helpers import example_snapshot, experiment_from, named
from tests.substrates.contract import SubstrateDriverContract


def mininet_experiment() -> Experiment:
    snapshot = example_snapshot()
    snapshot["substrate"]["driver"] = "mininet-ovs"
    return experiment_from(snapshot)


class MininetOVSContractTests(SubstrateDriverContract, unittest.TestCase):
    def make_driver(self) -> MininetOVSDriver:
        return MininetOVSDriver()


class MininetOVSDriverTests(unittest.TestCase):
    def test_builtin_registry_constructs_driver(self) -> None:
        driver = substrate_registry.create("mininet-ovs")

        self.assertIsInstance(driver, MininetOVSDriver)
        self.assertEqual(driver.name, "mininet-ovs")

    def test_phase1_topology_compiles_for_mininet_without_importing_it(self) -> None:
        plan = compile_experiment(mininet_experiment())

        self.assertEqual(plan.substrate, "mininet-ovs")
        self.assertEqual(len(plan.resources), 16)
        self.assertEqual(len(plan.agents), 6)

    def test_unknown_options_are_rejected_deterministically(self) -> None:
        snapshot = example_snapshot()
        snapshot["substrate"]["driver"] = "mininet-ovs"
        snapshot["substrate"]["options"] = {"z-option": True, "a-option": True}

        with self.assertRaises(CompilationError) as context:
            compile_experiment(experiment_from(snapshot))

        message = str(context.exception)
        self.assertIn("options.a-option", message)
        self.assertIn("options.z-option", message)
        self.assertLess(
            message.index("options.a-option"), message.index("options.z-option")
        )

    def test_userspace_ovs_datapath_is_accepted(self) -> None:
        snapshot = example_snapshot()
        snapshot["substrate"]["driver"] = "mininet-ovs"
        resources = snapshot["substrate"]["topology"]["resources"]
        named(resources, "s1")["datapath"] = "userspace"

        plan = compile_experiment(experiment_from(snapshot))

        switch = next(resource for resource in plan.resources if resource.name == "s1")
        self.assertEqual(switch.datapath.value, "userspace")

    def test_unsupported_controller_and_tc_combinations_are_rejected(self) -> None:
        snapshot = example_snapshot()
        snapshot["substrate"]["driver"] = "mininet-ovs"
        resources = snapshot["substrate"]["topology"]["resources"]
        named(resources, "c0")["protocol"] = "ssl"
        link = named(snapshot["substrate"]["topology"]["links"], "h1-s1")
        link["bandwidth"] = 1_001
        link["delay"] = None
        link["jitter"] = "1ms"

        with self.assertRaises(CompilationError) as context:
            compile_experiment(experiment_from(snapshot))

        message = str(context.exception)
        self.assertIn("SSL controllers", message)
        self.assertIn("TCLink bandwidth 1001 Mbps exceeds 1000 Mbps", message)
        self.assertIn("link jitter requires a base delay", message)

    def test_linux_interface_and_openflow_port_limits_are_rejected(self) -> None:
        snapshot = example_snapshot()
        snapshot["substrate"]["driver"] = "mininet-ovs"
        topology = snapshot["substrate"]["topology"]
        resources = topology["resources"]
        switch = named(resources, "s1")
        switch["ports"][0]["name"] = "s1-interface-name-too-long"
        switch["ports"][0]["number"] = 65_280
        named(topology["links"], "h1-s1")["endpoints"][1]["adapter"] = (
            "s1-interface-name-too-long"
        )

        with self.assertRaises(CompilationError) as context:
            compile_experiment(experiment_from(snapshot))

        message = str(context.exception)
        self.assertIn("Linux interface name", message)
        self.assertIn("OpenFlow physical port number 65280", message)

    def test_builtin_controllers_must_use_distinct_local_ports(self) -> None:
        snapshot = example_snapshot()
        snapshot["substrate"]["driver"] = "mininet-ovs"
        resources = snapshot["substrate"]["topology"]["resources"]
        controller = dict(named(resources, "c0"))
        controller["name"] = "c1"
        resources.append(controller)

        with self.assertRaisesRegex(
            CompilationError,
            "built-in controllers 'c0' and 'c1' share local listening port 6653",
        ):
            compile_experiment(experiment_from(snapshot))

    def test_declared_ports_must_be_attached_to_links(self) -> None:
        snapshot = example_snapshot()
        snapshot["substrate"]["driver"] = "mininet-ovs"
        resources = snapshot["substrate"]["topology"]["resources"]
        named(resources, "s1")["ports"].append(
            {"name": "s1-eth3", "number": 3}
        )

        with self.assertRaisesRegex(
            CompilationError, "port 's1-eth3' is not attached to a link"
        ):
            compile_experiment(experiment_from(snapshot))

    def test_controller_addresses_and_host_routes_must_be_mininet_safe(self) -> None:
        snapshot = example_snapshot()
        snapshot["substrate"]["driver"] = "mininet-ovs"
        resources = snapshot["substrate"]["topology"]["resources"]
        controller = named(resources, "c0")
        controller["type"] = "remote"
        controller["address"] = "2001:db8::1"
        named(resources, "h1")["defaultRoute"] = "via 10.0.0.254; touch /tmp/x"

        with self.assertRaises(CompilationError) as context:
            compile_experiment(experiment_from(snapshot))

        message = str(context.exception)
        self.assertIn("require an IPv4 address", message)
        self.assertIn("default route must use", message)


if __name__ == "__main__":
    unittest.main()
