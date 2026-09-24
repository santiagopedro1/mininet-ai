from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import time
import unittest
from multiprocessing.connection import Connection
from pathlib import Path

from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates import (
    ActionRequest,
    ActionStatus,
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
    process = runtime.execute(
        run.id,
        ActionRequest(
            id="crash-survivor",
            name="host.process.start",
            target="h1",
            parameters={"command": "sleep", "arguments": ["300"]},
        ),
    )
    if process.status != ActionStatus.SUCCEEDED:
        raise RuntimeError(f"could not start managed process: {process.issue}")
    connection.send((run.id, process.output["pid"]))
    connection.close()
    os._exit(0)


@unittest.skipUnless(LIVE_TESTS, "set MININET_AI_LIVE_TESTS=1 inside the Phase 2 VM")
class LiveMininetOVSRuntimeTests(unittest.TestCase):
    def test_cli_runs_inspects_and_cooperatively_stops_owner(self) -> None:
        state_path = Path("/run/mininet-ai/mininet-ovs.json")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mininet_ai.cli",
                "run",
                str(EXPERIMENT),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        run_id = None
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output, error = process.communicate()
                    self.fail(f"run command exited early: {output}\n{error}")
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if state["run"]["state"] == "running":
                        run_id = state["run"]["id"]
                        break
                time.sleep(0.1)
            self.assertIsNotNone(run_id, "run command did not become ready")
            assert run_id is not None

            status = command(
                sys.executable,
                "-m",
                "mininet_ai.cli",
                "status",
                run_id,
                "--format",
                "json",
            )
            topology = command(
                sys.executable,
                "-m",
                "mininet_ai.cli",
                "topology",
                run_id,
                "--format",
                "json",
            )
            command("ip", "link", "set", "dev", "s1-eth1", "down")
            degraded_topology = command(
                sys.executable,
                "-m",
                "mininet_ai.cli",
                "topology",
                run_id,
                "--format",
                "json",
            )
            command("ip", "link", "set", "dev", "s1-eth1", "up")
            stopped = command(
                sys.executable,
                "-m",
                "mininet_ai.cli",
                "stop",
                run_id,
                "--timeout",
                "15",
            )

            self.assertEqual(json.loads(status.stdout)["run"]["state"], "running")
            resources = json.loads(topology.stdout)["resources"]
            self.assertTrue(any(item["name"] == "s1" for item in resources))
            degraded_resources = {
                item["name"]: item
                for item in json.loads(degraded_topology.stdout)["resources"]
            }
            self.assertEqual(degraded_resources["s1-eth1"]["state"], "down")
            self.assertEqual(degraded_resources["h1-s1"]["state"], "down")
            self.assertIn(f"Stopped {run_id}", stopped.stdout)
            output, error = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, f"{output}\n{error}")
            self.assertIn(f"Stopped {run_id}", output)
            self.assertFalse(state_path.exists())
            repeated_stop = command(
                sys.executable,
                "-m",
                "mininet_ai.cli",
                "stop",
                run_id,
            )
            stopped_status = command(
                sys.executable,
                "-m",
                "mininet_ai.cli",
                "status",
                run_id,
                "--format",
                "json",
            )
            self.assertIn(f"Stopped {run_id}", repeated_stop.stdout)
            self.assertEqual(
                json.loads(stopped_status.stdout)["run"]["state"],
                "stopped",
            )
            self.assertNotEqual(
                command(
                    "ovs-vsctl", "br-exists", "s1", check=False
                ).returncode,
                0,
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if state_path.exists() and run_id is not None:
                MininetOVSRuntime().teardown(run_id)

    def test_fresh_runtime_recovers_a_crashed_owner(self) -> None:
        read_connection, write_connection = multiprocessing.Pipe(duplex=False)
        process = multiprocessing.Process(
            target=deploy_and_exit_without_teardown,
            args=(write_connection,),
        )
        process.start()
        write_connection.close()
        self.assertTrue(read_connection.poll(30))
        run_id, managed_pid = read_connection.recv()
        read_connection.close()
        process.join(timeout=30)

        self.assertEqual(process.exitcode, 0)
        self.assertEqual(run_id, "live-crash-recovery")
        command("ovs-vsctl", "br-exists", "s1")

        runtime = MininetOVSRuntime()
        snapshot = runtime.inspect(run_id)
        assert snapshot.run.issue is not None
        self.assertEqual(snapshot.run.issue.code, "runtime.run.orphaned")

        result = runtime.teardown(run_id)

        self.assertEqual(result.run.state.value, "stopped")
        self.assertNotEqual(
            command("ovs-vsctl", "br-exists", "s1", check=False).returncode,
            0,
        )
        self.assertFalse(Path("/run/mininet-ai/mininet-ovs.json").exists())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and Path(
            f"/proc/{managed_pid}"
        ).exists():
            time.sleep(0.05)
        self.assertFalse(Path(f"/proc/{managed_pid}").exists())

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

            disabled = runtime.execute(
                run.id,
                ActionRequest(
                    id="disable-host-link",
                    name="link.disable",
                    target="h1-s1",
                ),
            )
            self.assertEqual(disabled.status, ActionStatus.SUCCEEDED)
            disabled_snapshot = runtime.inspect(run.id)
            disabled_link = next(
                resource
                for resource in disabled_snapshot.resources
                if resource.name == "h1-s1"
            )
            self.assertEqual(disabled_link.state, ResourceOperationalState.DOWN)

            enabled = runtime.execute(
                run.id,
                ActionRequest(
                    id="enable-host-link",
                    name="link.enable",
                    target="h1-s1",
                ),
            )
            self.assertEqual(enabled.status, ActionStatus.SUCCEEDED)

            configured = runtime.execute(
                run.id,
                ActionRequest(
                    id="reshape-host-link",
                    name="link.configure",
                    target="h1-s1",
                    parameters={"bandwidth": 50, "delay": "2ms", "loss": 0},
                ),
            )
            self.assertEqual(configured.status, ActionStatus.SUCCEEDED)
            changed_qdiscs = command(
                "tc", "qdisc", "show", "dev", "s1-eth1"
            ).stdout
            self.assertIn("delay 2ms", changed_qdiscs)

            installed = runtime.execute(
                run.id,
                ActionRequest(
                    id="install-test-flow",
                    name="openflow.flow.install",
                    target="s1",
                    parameters={
                        "cookie": "0x1234",
                        "priority": 100,
                        "match": {"in_port": 1},
                        "actions": "output:2",
                    },
                ),
            )
            self.assertEqual(installed.status, ActionStatus.SUCCEEDED)
            flow_dump = command(
                "ovs-ofctl", "-O", "OpenFlow13", "dump-flows", "s1"
            ).stdout
            self.assertIn("cookie=0x1234", flow_dump)

            removed = runtime.execute(
                run.id,
                ActionRequest(
                    id="remove-test-flow",
                    name="openflow.flow.remove",
                    target="s1",
                    parameters={
                        "cookie": "0x1234",
                        "cookieMask": "0xffffffffffffffff",
                        "match": "",
                    },
                ),
            )
            self.assertEqual(removed.status, ActionStatus.SUCCEEDED)
            flow_dump = command(
                "ovs-ofctl", "-O", "OpenFlow13", "dump-flows", "s1"
            ).stdout
            self.assertNotIn("cookie=0x1234", flow_dump)

            started = runtime.execute(
                run.id,
                ActionRequest(
                    id="managed-sleeper",
                    name="host.process.start",
                    target="h1",
                    parameters={"command": "sleep", "arguments": ["30"]},
                ),
            )
            self.assertEqual(started.status, ActionStatus.SUCCEEDED)
            stopped = runtime.execute(
                run.id,
                ActionRequest(
                    id="stop-managed-sleeper",
                    name="host.process.stop",
                    target="h1",
                    parameters={"processId": "managed-sleeper"},
                ),
            )
            self.assertEqual(stopped.status, ActionStatus.SUCCEEDED)
        finally:
            if run is not None:
                runtime.teardown(run.id)

        self.assertNotEqual(
            command("ovs-vsctl", "br-exists", "s1", check=False).returncode,
            0,
        )


if __name__ == "__main__":
    unittest.main()
