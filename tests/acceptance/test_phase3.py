from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from importlib.metadata import EntryPoint, EntryPoints
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from typer.testing import CliRunner

from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
from mininet_ai.audit import AuditEventType, AuditRecorder, MemoryAuditSink
from mininet_ai.cli import app
from mininet_ai.compiler import compile_experiment
from mininet_ai.plugins import ProviderRegistries, discover_plugins
from mininet_ai.runtime import (
    ContinuousAgentRuntime,
    ManualIntentPayload,
    RuntimeEvent,
    RuntimeEventType,
)
from mininet_ai.sdk import InvocationStatus
from mininet_ai.substrates import ActionStatus, FakeSubstrateRuntime

ROOT = Path(__file__).parents[2]
EXPERIMENT = ROOT / "examples" / "phase3" / "experiment.yaml"


def capability_entry_points() -> tuple[EntryPoint, ...]:
    group = "mininet_ai.capabilities"
    return (
        EntryPoint(
            name="example.telemetry",
            value="examples.phase3.providers:telemetry_plugin",
            group=group,
        ),
        EntryPoint(
            name="example.action",
            value="examples.phase3.providers:action_plugin",
            group=group,
        ),
    )


class Phase3AcceptanceTests(unittest.TestCase):
    def invoke(self, plan):
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "phase3-run")
        run = substrate.deploy(plan)
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        loaded = discover_plugins(
            registries,
            entry_points=capability_entry_points(),
        )
        audit_sink = MemoryAuditSink()
        runtime = OneShotAgentRuntime(
            plan,
            substrate,
            registries,
            audit=AuditRecorder(audit_sink),
            invocation_id_factory=lambda: "phase3-invocation",
        )
        result = runtime.invoke(
            run.id,
            "edge-operator@s1",
            "Inspect s1 and apply the declared safe change",
        )
        return result, audit_sink.events, loaded

    def test_user_plugins_execute_without_core_source_changes(self) -> None:
        plan = compile_experiment(EXPERIMENT)

        result, events, loaded = self.invoke(plan)

        self.assertEqual(result.status, InvocationStatus.SUCCEEDED)
        self.assertEqual(
            tuple(plugin.name for plugin in loaded),
            ("example.action", "example.telemetry"),
        )
        self.assertEqual(len(result.action_results), 2)
        telemetry, action = result.action_results
        self.assertEqual(telemetry.status, ActionStatus.SUCCEEDED)
        self.assertFalse(telemetry.changed)
        self.assertEqual(telemetry.output["target"], "s1")
        self.assertEqual(action.status, ActionStatus.SUCCEEDED)
        self.assertTrue(action.changed)
        self.assertEqual(action.output, {"applied": True, "target": "s1"})
        self.assertEqual(events[0].type, AuditEventType.AGENT_STARTED)
        self.assertEqual(events[-1].type, AuditEventType.CAPABILITY_COMPLETED)
        invocation_event = next(
            event for event in events if event.type == AuditEventType.AGENT_COMPLETED
        )
        runtime_data = cast(dict[str, Any], invocation_event.data["runtime"])
        metrics = cast(dict[str, Any], runtime_data["metrics"])
        self.assertEqual(runtime_data["name"], "agno")
        self.assertEqual(runtime_data["agnoRunId"], "phase3-invocation")
        self.assertEqual(
            metrics["totalTokens"],
            55,
        )
        self.assertEqual(metrics["cacheReadTokens"], 5)
        self.assertEqual(metrics["reasoningTokens"], 3)
        self.assertEqual(metrics["cost"], 0.001)

    def test_manual_event_runs_the_complete_agno_and_capability_path(self) -> None:
        plan = compile_experiment(EXPERIMENT)
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "continuous-run")
        run = substrate.deploy(plan)
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        discover_plugins(registries, entry_points=capability_entry_points())
        one_shot = OneShotAgentRuntime(
            plan,
            substrate,
            registries,
            invocation_id_factory=lambda: "continuous-invocation",
        )
        runtime = ContinuousAgentRuntime(plan, run.id, one_shot)
        payload = ManualIntentPayload(
            agentId="edge-operator@s1",
            intent="Inspect s1 and apply the declared safe change",
        )
        observed_at = datetime.now(UTC)
        event = RuntimeEvent(
            eventId="manual-1",
            runId=run.id,
            type=RuntimeEventType.MANUAL_INTENT,
            source="acceptance-test",
            subject="edge-operator@s1",
            occurredAt=observed_at,
            observedAt=observed_at,
            sequence=1,
            payload=payload.model_dump(mode="json", by_alias=True),
        )

        runtime.start()
        runtime.publish(event)
        report = runtime.stop()

        self.assertEqual(report.completed, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(len(report.invocations[0].result.action_results), 2)

    def test_out_of_scope_plugin_action_is_rejected_before_execution(self) -> None:
        plan = compile_experiment(EXPERIMENT)
        snapshot = copy.deepcopy(plan.snapshot)
        proposals = snapshot["blueprints"][0]["model"]["parameters"][
            "response"
        ]["proposals"]
        proposals[-1]["target"] = "s2"
        unsafe_plan = plan.model_copy(update={"snapshot": snapshot})

        result, events, _ = self.invoke(unsafe_plan)

        self.assertEqual(result.status, InvocationStatus.REJECTED)
        self.assertEqual(result.action_results[-1].status, ActionStatus.REJECTED)
        issue = result.action_results[-1].issue
        assert issue is not None
        self.assertEqual(
            issue.code,
            "capability.target.out-of-scope",
        )
        completed = [
            event
            for event in events
            if event.type == AuditEventType.CAPABILITY_COMPLETED
        ]
        completed_data = cast(dict[str, Any], completed[-1].data)
        self.assertEqual(
            completed_data["result"]["issue"]["code"],
            "capability.target.out-of-scope",
        )

    def test_cli_discovers_user_plugins_and_emits_audited_json_result(self) -> None:
        plan = compile_experiment(EXPERIMENT)
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "phase3-cli-run")
        run = substrate.deploy(plan)

        with TemporaryDirectory() as temporary:
            audit_path = Path(temporary) / "phase3-audit.jsonl"
            with (
                patch(
                    "mininet_ai.cli.create_substrate_runtime",
                    return_value=substrate,
                ),
                patch(
                    "mininet_ai.plugins.discovery.metadata.entry_points",
                    return_value=EntryPoints(capability_entry_points()),
                ),
            ):
                result = CliRunner().invoke(
                    app,
                    [
                        "invoke",
                        str(EXPERIMENT),
                        run.id,
                        "edge-operator@s1",
                        "--intent",
                        "Inspect and apply the safe change",
                        "--discover-plugins",
                        "--audit-log",
                        str(audit_path),
                        "--agno-db",
                        str(Path(temporary) / "agno.sqlite3"),
                        "--shared-state-db",
                        str(Path(temporary) / "state.sqlite3"),
                        "--format",
                        "json",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            payload = json.loads(result.output)
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(len(payload["actionResults"]), 2)
            records = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[0]["runId"], run.id)
            self.assertEqual(records[-1]["data"]["result"]["status"], "succeeded")

    def test_demo_command_runs_the_complete_workflow_in_one_process(self) -> None:
        with TemporaryDirectory() as temporary:
            audit_path = Path(temporary) / "phase3-demo-audit.jsonl"
            agno_db = Path(temporary) / "phase3-demo-agno.sqlite3"
            state_db = Path(temporary) / "phase3-demo-state.sqlite3"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "examples.phase3",
                    "--audit-log",
                    str(audit_path),
                    "--agno-db",
                    str(agno_db),
                    "--shared-state-db",
                    str(state_db),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["result"]["status"], "succeeded")
            self.assertEqual(len(payload["result"]["actionResults"]), 2)
            self.assertEqual(
                payload["loadedPlugins"],
                ["example.action", "example.telemetry"],
            )
            self.assertEqual(payload["teardown"]["run"]["state"], "stopped")
            self.assertTrue(audit_path.is_file())
            records = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertGreaterEqual(len(records), 6)


if __name__ == "__main__":
    unittest.main()
