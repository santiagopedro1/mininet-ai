from __future__ import annotations

import multiprocessing
import os
import subprocess
import unittest
from multiprocessing.connection import Connection
from pathlib import Path

from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates import (
    MininetOVSRuntime,
    ObservationQuery,
    ResourceOperationalState,
)


ROOT = Path(__file__).parents[2]
EXPERIMENT = ROOT / "examples" / "phase2" / "experiment.yaml"
LIVE_TESTS = os.environ.get("MININET_AI_LIVE_TESTS") == "1"


def command(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=check,
        capture_output=True,
        text=True,
    )


def deploy_and_exit_without_teardown(connection: Connection) -> None:
    plan = compile_experiment(EXPERIMENT)
    runtime = MininetOVSRuntime(run_id_factory=lambda: "live-crash-recovery")
    run = runtime.deploy(plan)
    connection.send(run.id)
    connection.close()
    os._exit(0)


@unittest.skipUnless(LIVE_TESTS, "set MININET_AI_LIVE_TESTS=1 inside the Phase 2 VM")
class LiveMininetOVSRuntimeTests(unittest.TestCase):
    def test_fresh_runtime_recovers_a_crashed_owner(self) -> None:
        read_connection, write_connection = multiprocessing.Pipe(duplex=False)
        process = multiprocessing.Process(
            target=deploy_and_exit_without_teardown,
            args=(write_connection,),
        )
        process.start()
        write_connection.close()
        self.assertTrue(read_connection.poll(30))
        run_id = read_connection.recv()
        read_connection.close()
        process.join(timeout=30)

        self.assertEqual(process.exitcode, 0)
        self.assertEqual(run_id, "live-crash-recovery")
        command("ovs-vsctl", "br-exists", "s1")

        runtime = MininetOVSRuntime()
        snapshot = runtime.inspect(run_id)
        self.assertEqual(snapshot.run.issue.code, "runtime.run.orphaned")

        result = runtime.teardown(run_id)

        self.assertEqual(result.run.state.value, "stopped")
        self.assertNotEqual(
            command("ovs-vsctl", "br-exists", "s1", check=False).returncode,
            0,
        )
        self.assertFalse(Path("/run/mininet-ai/mininet-ovs.json").exists())

    def test_deploys_inspects_and_tears_down_real_topology(self) -> None:
        plan = compile_experiment(EXPERIMENT)
        runtime = MininetOVSRuntime(run_id_factory=lambda: "live-acceptance")
        run = None

        try:
            run = runtime.deploy(plan)
            snapshot = runtime.inspect(run.id)

            self.assertTrue(snapshot.resources)
            self.assertTrue(
                all(
                    resource.state == ResourceOperationalState.UP
                    for resource in snapshot.resources
                )
            )
            command("ovs-vsctl", "br-exists", "s1")
            self.assertEqual(
                command("ovs-vsctl", "get", "Bridge", "s1", "fail_mode")
                .stdout.strip()
                .strip('"'),
                "secure",
            )
            self.assertIn(
                "OpenFlow13",
                command("ovs-vsctl", "get", "Bridge", "s1", "protocols").stdout,
            )
            self.assertEqual(
                set(command("ovs-vsctl", "list-ports", "s1").stdout.split()),
                {"s1-eth1", "s1-eth2"},
            )
            self.assertEqual(
                command(
                    "ovs-vsctl", "get", "Interface", "s1-eth1", "ofport"
                ).stdout.strip(),
                "1",
            )
            qdiscs = command("tc", "qdisc", "show", "dev", "s1-eth1").stdout
            self.assertIn("htb", qdiscs)
            self.assertIn("netem", qdiscs)
            self.assertIn("delay 1ms", qdiscs)

            observations = {
                name: runtime.observe(
                    run.id,
                    ObservationQuery(name=name, targets=(target,)),
                ).values[target]
                for name, target in (
                    ("topology.resources", "s1"),
                    ("topology.neighbors", "s1"),
                    ("controller.events", "c0"),
                    ("openflow.flows", "s1"),
                    ("ovs.port-counters", "s1"),
                    ("tc.queue-occupancy", "s1"),
                    ("host.interfaces", "h1"),
                    ("host.processes", "h1"),
                    ("host.reachability", "h1"),
                )
            }
            self.assertEqual(observations["topology.resources"]["state"], "up")
            self.assertEqual(
                len(observations["topology.neighbors"]["links"]), 2
            )
            self.assertIn("events", observations["controller.events"])
            self.assertIn("switches", observations["openflow.flows"])
            self.assertEqual(
                len(observations["ovs.port-counters"]["ports"]), 2
            )
            self.assertEqual(
                len(observations["tc.queue-occupancy"]["ports"]), 2
            )
            self.assertEqual(
                observations["host.interfaces"]["interfaces"][0]["state"],
                "UP",
            )
            self.assertTrue(observations["host.processes"]["processes"])
            self.assertEqual(
                observations["host.reachability"]["probes"][0]["destination"],
                "h2",
            )
            self.assertTrue(
                observations["host.reachability"]["probes"][0]["reachable"]
            )
        finally:
            if run is not None:
                runtime.teardown(run.id)

        self.assertNotEqual(
            command("ovs-vsctl", "br-exists", "s1", check=False).returncode,
            0,
        )


if __name__ == "__main__":
    unittest.main()
