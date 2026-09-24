from __future__ import annotations

import os
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates import ObservationQuery, ResourceOperationalState
from mininet_ai.substrates.mininet_ovs.observations import (
    CommandResult,
    MininetOVSObservations,
    ObservationCollectionError,
)
from tests.substrates.test_mininet_ovs_runtime import mininet_plan

ROOT = Path(__file__).parents[2]
PHASE2_EXPERIMENT = ROOT / "examples" / "phase2" / "experiment.yaml"


class Node:
    def __init__(self, name: str, *, pid: int | None = None) -> None:
        self.name = name
        self.pid = pid


class Network:
    def __init__(self, names: tuple[str, ...], *, pid: int | None = None) -> None:
        self.nodes = {name: Node(name, pid=pid) for name in names}

    def get(self, name: str) -> Node:
        return self.nodes[name]


class ScriptedExecutor:
    def __init__(self) -> None:
        self.responses: dict[tuple[str | None, tuple[str, ...]], CommandResult] = {}
        self.calls: list[tuple[str | None, tuple[str, ...]]] = []

    def add(
        self,
        arguments: tuple[str, ...],
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        node: str | None = None,
    ) -> None:
        self.responses[(node, arguments)] = CommandResult(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )

    def run(
        self,
        arguments: Sequence[str],
        *,
        node: Any | None = None,
        timeout_seconds: float = 10,
    ) -> CommandResult:
        del timeout_seconds
        key = (getattr(node, "name", None), tuple(arguments))
        self.calls.append(key)
        try:
            return self.responses[key]
        except KeyError as error:
            raise AssertionError(f"unexpected telemetry command: {key}") from error


def network_for(plan, *, pid: int | None = None) -> Network:
    names = tuple(
        resource.name
        for resource in plan.resources
        if resource.kind.value in {"controller", "switch", "host"}
    )
    return Network(names, pid=pid)


class MininetOVSObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = mininet_plan()
        self.executor = ScriptedExecutor()
        self.provider = MininetOVSObservations(
            self.plan,
            network_for(self.plan),
            executor=self.executor,
        )

    def observe(
        self,
        name: str,
        *targets: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.provider.collect(
            ObservationQuery(
                name=name,
                targets=targets,
                parameters=parameters or {},
            )
        )

    def test_topology_neighbors_are_normalized_from_planned_links(self) -> None:
        values = self.observe("topology.neighbors", "s1")

        self.assertEqual(
            [link["name"] for link in values["s1"]["links"]],
            ["h1-s1", "s1-s2"],
        )
        self.assertEqual(
            values["s1"]["links"][0]["endpoints"],
            [
                {"port": "h1-eth0", "node": "h1"},
                {"port": "s1-eth1", "node": "s1"},
            ],
        )

        controller = self.observe("topology.neighbors", "c0")
        self.assertEqual(
            [link["name"] for link in controller["c0"]["links"]],
            ["h1-s1", "s1-s2", "s2-h2"],
        )

    def test_ovs_port_counters_are_named_and_zero_fill_missing_values(self) -> None:
        self.executor.add(
            (
                "ovs-vsctl",
                "--timeout=5",
                "get",
                "Interface",
                "s1-eth1",
                "statistics",
            ),
            stdout="{rx_bytes=120, rx_packets=3, tx_bytes=80, tx_packets=2}",
        )
        self.executor.add(
            (
                "ovs-vsctl",
                "--timeout=5",
                "get",
                "Interface",
                "s1-eth2",
                "statistics",
            ),
            stdout="{}",
        )

        values = self.observe("ovs.port-counters", "s1")

        first = values["s1"]["ports"][0]
        self.assertEqual(first["name"], "s1-eth1")
        self.assertEqual(first["rxBytes"], 120)
        self.assertEqual(first["txPackets"], 2)
        self.assertEqual(first["rxErrors"], 0)

    def test_openflow_dump_is_normalized_without_losing_match_fields(self) -> None:
        self.executor.add(
            ("ovs-ofctl", "-O", "OpenFlow13", "dump-flows", "s1"),
            stdout=(
                "OFPST_FLOW reply (OF1.3) (xid=0x2):\n"
                " cookie=0x0, duration=4.5s, table=0, n_packets=3, "
                "n_bytes=252, priority=10,in_port=1 actions=output:2\n"
            ),
        )

        values = self.observe("openflow.flows", "s1")

        flow = values["s1"]["switches"][0]["flows"][0]
        self.assertEqual(flow["durationSeconds"], 4.5)
        self.assertEqual(flow["packets"], 3)
        self.assertEqual(flow["match"], {"in_port": "1"})
        self.assertEqual(flow["actions"], "output:2")

    def test_queue_telemetry_runs_in_each_ports_namespace(self) -> None:
        qdisc = (
            '[{"kind":"netem","handle":"10:","bytes":400,'
            '"packets":4,"drops":1,"backlog":128,"qlen":2}]'
        )
        arguments = ("tc", "-j", "-s", "qdisc", "show", "dev", "h1-eth0")
        self.executor.add(arguments, stdout=qdisc, node="h1")
        arguments = ("tc", "-j", "-s", "qdisc", "show", "dev", "s1-eth1")
        self.executor.add(arguments, stdout=qdisc)

        values = self.observe("tc.queue-occupancy", "h1-s1")

        ports = values["h1-s1"]["ports"]
        self.assertEqual([port["name"] for port in ports], ["h1-eth0", "s1-eth1"])
        self.assertEqual(ports[0]["qdiscs"][0]["qlen"], 2)
        self.assertEqual(ports[0]["qdiscs"][0]["backlog"], 128)

    def test_host_interfaces_have_normalized_addresses_and_statistics(self) -> None:
        arguments = (
            "ip",
            "-j",
            "-s",
            "address",
            "show",
            "dev",
            "h1-eth0",
        )
        self.executor.add(
            arguments,
            node="h1",
            stdout=(
                '[{"ifindex":2,"ifname":"h1-eth0","flags":["UP"],'
                '"mtu":1500,"operstate":"UP","address":"02:00:00:00:00:01",'
                '"addr_info":[{"family":"inet","local":"10.0.0.1",'
                '"prefixlen":24,"scope":"global"}],"stats64":{'
                '"rx":{"bytes":10,"packets":1,"errors":0,"dropped":0},'
                '"tx":{"bytes":20,"packets":2,"errors":0,"dropped":0}}}]'
            ),
        )

        values = self.observe("host.interfaces", "h1")

        interface = values["h1"]["interfaces"][0]
        self.assertEqual(interface["state"], "UP")
        self.assertEqual(interface["addresses"][0]["local"], "10.0.0.1")
        self.assertEqual(interface["tx"]["bytes"], 20)

    def test_host_processes_include_only_the_host_shell_tree(self) -> None:
        self.provider = MininetOVSObservations(
            self.plan,
            network_for(self.plan, pid=1000),
            executor=self.executor,
        )
        self.executor.add(
            ("ps", "-eo", "pid=,ppid=,stat=,comm=,args=", "--sort=pid"),
            stdout=(
                "1 0 Ss init /sbin/init\n"
                "1000 1 S bash bash --norc mininet:h1\n"
                "1001 1000 S iperf iperf -s\n"
                "2000 1 S unrelated unrelated\n"
            ),
        )

        values = self.observe("host.processes", "h1")

        self.assertEqual(
            [process["pid"] for process in values["h1"]["processes"]],
            [1000, 1001],
        )

    def test_host_reachability_uses_only_declared_host_addresses(self) -> None:
        self.executor.add(
            ("ping", "-n", "-c", "1", "-W", "1", "10.0.0.2"),
            node="h1",
            stdout=(
                "64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.123 ms\n"
            ),
        )

        values = self.observe(
            "host.reachability",
            "h1",
            parameters={"destinations": ["h2"], "timeoutSeconds": 0.5},
        )

        self.assertEqual(
            values["h1"]["probes"],
            [
                {
                    "source": "h1",
                    "destination": "h2",
                    "address": "10.0.0.2",
                    "reachable": True,
                    "latencyMs": 0.123,
                }
            ],
        )

    def test_controller_events_are_bounded_and_attributed(self) -> None:
        self.executor.add(
            ("tail", "-n", "2", "/tmp/c0.log"),
            stdout="connected\npacket-in\n",
        )

        values = self.observe(
            "controller.events", "control-domain", parameters={"limit": 2}
        )

        self.assertEqual(
            values["control-domain"]["events"],
            [
                {"controller": "c0", "message": "connected"},
                {"controller": "c0", "message": "packet-in"},
            ],
        )

    def test_controller_event_command_failures_are_typed(self) -> None:
        self.executor.add(
            ("tail", "-n", "100", "/tmp/c0.log"),
            stderr="permission denied",
            returncode=1,
        )

        with self.assertRaises(ObservationCollectionError) as context:
            self.observe("controller.events", "c0")

        self.assertEqual(context.exception.code, "runtime.observation.failed")

    def test_invalid_target_and_parameters_have_stable_error_codes(self) -> None:
        with self.assertRaises(ObservationCollectionError) as target:
            self.observe("host.interfaces", "s1")
        with self.assertRaises(ObservationCollectionError) as parameters:
            self.observe(
                "host.processes", "h1", parameters={"limit": True}
            )
        with self.assertRaises(ObservationCollectionError) as destination:
            self.observe(
                "host.reachability",
                "h1",
                parameters={"destinations": ["missing"]},
            )

        self.assertEqual(target.exception.code, "runtime.observation.invalid-target")
        self.assertEqual(
            parameters.exception.code,
            "runtime.observation.invalid-parameters",
        )
        self.assertEqual(
            destination.exception.code,
            "runtime.observation.invalid-parameters",
        )

    def test_snapshot_discovers_operational_state_and_interface_details(self) -> None:
        plan = compile_experiment(PHASE2_EXPERIMENT)
        network = network_for(plan, pid=os.getpid())
        executor = ScriptedExecutor()
        executor.add(("ovs-vsctl", "--timeout=5", "br-exists", "s1"))
        for name, node in (
            ("s1-eth1", None),
            ("s1-eth2", None),
            ("h1-eth0", "h1"),
            ("h2-eth0", "h2"),
        ):
            executor.add(
                ("ip", "-j", "-s", "address", "show", "dev", name),
                node=node,
                stdout=(
                    f'[{{"ifname":"{name}","flags":["UP"],'
                    '"operstate":"UP","carrier":1}]'
                ),
            )
        provider = MininetOVSObservations(plan, network, executor=executor)

        snapshot = provider.snapshot()

        self.assertTrue(
            all(
                resource.state == ResourceOperationalState.UP
                for resource in snapshot
            )
        )
        port = next(resource for resource in snapshot if resource.name == "h1-eth0")
        self.assertEqual(port.attributes["operState"], "UP")
        self.assertEqual(port.attributes["carrier"], 1)


if __name__ == "__main__":
    unittest.main()
