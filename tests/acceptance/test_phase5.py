from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, cast

from mininet_ai.compiler import compile_experiment


ROOT = Path(__file__).parents[2]
EXPERIMENT = ROOT / "examples" / "phase5" / "experiment.yaml"


class Phase5AcceptanceTests(unittest.TestCase):
    def test_centralized_delegation_and_conflict_rejection_end_to_end(
        self,
    ) -> None:
        plan = compile_experiment(EXPERIMENT)
        self.assertEqual(plan.metadata.name, "phase5-centralized-coordination")

        result = subprocess.run(
            [sys.executable, "-m", "examples.phase5"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        event = cast(dict[str, Any], payload["event"])
        report = cast(dict[str, Any], payload["report"])
        continuous = cast(dict[str, Any], report["continuous"])
        record = cast(dict[str, Any], continuous["invocations"][0])
        outcome = cast(dict[str, Any], record["coordination"])
        messages = cast(list[dict[str, Any]], outcome["messages"])
        invocations = cast(list[dict[str, Any]], outcome["invocations"])
        arbitration = cast(dict[str, Any], outcome["arbitration"])
        decisions = cast(list[dict[str, Any]], arbitration["decisions"])

        self.assertEqual(report["state"], "stopped")
        self.assertEqual(continuous["completed"], 1)
        self.assertEqual(continuous["failed"], 0)
        self.assertEqual(
            outcome["contractVersion"],
            "mininet-ai/coordination-outcome/v1alpha1",
        )
        self.assertEqual(outcome["correlationId"], event["eventId"])
        self.assertEqual(outcome["requestedAgentId"], "primary-remediator@s1")
        self.assertEqual(outcome["entryAgentId"], "global-coordinator")
        self.assertEqual(
            [message["kind"] for message in messages],
            ["intent", "delegation", "delegation"],
        )
        self.assertEqual(
            [message["targetAgentId"] for message in messages],
            [
                "global-coordinator",
                "primary-remediator@s1",
                "secondary-remediator@s1",
            ],
        )
        self.assertTrue(
            all(message["correlationId"] == event["eventId"] for message in messages)
        )
        self.assertEqual(
            [invocation["result"]["status"] for invocation in invocations],
            ["succeeded", "succeeded", "rejected"],
        )
        self.assertEqual(arbitration["policy"], "reject")
        self.assertEqual(
            [decision["disposition"] for decision in decisions],
            ["execute", "reject"],
        )
        self.assertEqual(decisions[0]["conflictKeys"], ["s1:dataplane.write"])
        self.assertEqual(
            decisions[1]["winnerCandidateId"],
            decisions[0]["candidateId"],
        )
        primary_action = invocations[1]["result"]["actionResults"][0]
        secondary_action = invocations[2]["result"]["actionResults"][0]
        self.assertEqual(primary_action["status"], "succeeded")
        self.assertEqual(secondary_action["status"], "rejected")
        self.assertEqual(
            secondary_action["issue"]["code"],
            "coordination.conflict.rejected",
        )
        self.assertEqual(report["teardown"]["run"]["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
