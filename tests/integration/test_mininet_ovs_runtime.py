from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates import MininetOVSRuntime, ResourceOperationalState


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


@unittest.skipUnless(LIVE_TESTS, "set MININET_AI_LIVE_TESTS=1 inside the Phase 2 VM")
class LiveMininetOVSRuntimeTests(unittest.TestCase):
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
        finally:
            if run is not None:
                runtime.teardown(run.id)

        self.assertNotEqual(
            command("ovs-vsctl", "br-exists", "s1", check=False).returncode,
            0,
        )


if __name__ == "__main__":
    unittest.main()
