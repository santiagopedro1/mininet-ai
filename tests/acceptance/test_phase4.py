from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from mininet_ai.compiler import compile_experiment


ROOT = Path(__file__).parents[2]
EXPERIMENT = ROOT / "examples" / "phase4" / "experiment.yaml"


class Phase4AcceptanceTests(unittest.TestCase):
    def test_autonomous_experiment_runs_from_detection_through_teardown(
        self,
    ) -> None:
        plan = compile_experiment(EXPERIMENT)
        self.assertEqual(plan.metadata.name, "phase4-autonomous-runtime")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "examples.phase4",
                    "--ledger-db",
                    str(root / "ledger.sqlite3"),
                    "--agno-db",
                    str(root / "agno.sqlite3"),
                    "--shared-state-db",
                    str(root / "state.sqlite3"),
                    "--timeout",
                    "2",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        report = cast(dict[str, Any], payload["report"])
        continuous = cast(dict[str, Any], report["continuous"])
        telemetry = cast(dict[str, Any], report["telemetry"])
        invocation = continuous["invocations"][0]["result"]
        action = invocation["actionResults"][0]

        self.assertEqual(report["state"], "stopped")
        self.assertEqual(continuous["completed"], 1)
        self.assertEqual(continuous["failed"], 0)
        self.assertGreaterEqual(telemetry["detectorEvents"], 1)
        self.assertEqual(action["status"], "succeeded")
        self.assertTrue(action["postconditions"][0]["satisfied"])
        self.assertIsNotNone(invocation["timings"]["actionEffectSeconds"])
        self.assertEqual(report["teardown"]["run"]["state"], "stopped")
        self.assertIn("queue.congested", payload["recordTypes"])
        self.assertIn("agent.invocation.completed", payload["recordTypes"])
        self.assertIn("capability.execution.completed", payload["recordTypes"])
        self.assertEqual(payload["recordTypes"][-1], "run.stopped")
