from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

from mininet_ai.cli import app
from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates import FakeSubstrateRuntime, RunState
from tests.agents.test_runtime import configured_plan
from tests.compiler.helpers import example_snapshot, experiment_from


def fake_plan():
    return compile_experiment(experiment_from(example_snapshot()))


class StoppableFakeRuntime(FakeSubstrateRuntime):
    def __init__(self) -> None:
        super().__init__(run_id_factory=lambda: "cli-stop-run")
        self.stop_requests: list[tuple[str, float]] = []

    def request_stop(self, run_id: str, *, timeout_seconds: float = 30):
        self.stop_requests.append((run_id, timeout_seconds))
        return self.teardown(run_id)


class RuntimeCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.plan = fake_plan()

    def test_run_dry_run_prints_plan_without_constructing_runtime(self) -> None:
        with (
            patch("mininet_ai.cli._compile_or_exit", return_value=self.plan),
            patch("mininet_ai.cli.create_substrate_runtime") as create,
        ):
            result = self.runner.invoke(
                app,
                ["run", "experiment.yaml", "--dry-run", "--format", "json"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)["kind"], "DeploymentPlan")
        create.assert_not_called()

    def test_run_owns_runtime_until_signal_latch_then_tears_down(self) -> None:
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-run")
        with (
            patch("mininet_ai.cli._compile_or_exit", return_value=self.plan),
            patch(
                "mininet_ai.cli.create_substrate_runtime",
                return_value=runtime,
            ),
            patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
        ):
            result = self.runner.invoke(app, ["run", "experiment.yaml"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Running cli-run", result.output)
        self.assertIn("Stopped cli-run", result.output)
        self.assertEqual(runtime.inspect("cli-run").run.state, RunState.STOPPED)

    def test_run_tears_down_if_reporting_the_started_run_fails(self) -> None:
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-broken-output")
        with (
            patch("mininet_ai.cli._compile_or_exit", return_value=self.plan),
            patch(
                "mininet_ai.cli.create_substrate_runtime",
                return_value=runtime,
            ),
            patch("mininet_ai.cli.console.print", side_effect=BrokenPipeError),
        ):
            result = self.runner.invoke(app, ["run", "experiment.yaml"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(
            runtime.inspect("cli-broken-output").run.state,
            RunState.STOPPED,
        )

    def test_status_supports_text_and_machine_readable_output(self) -> None:
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-status")
        run = runtime.deploy(self.plan)
        with patch(
            "mininet_ai.cli.create_substrate_runtime",
            return_value=runtime,
        ):
            text_result = self.runner.invoke(
                app,
                ["status", run.id, "--substrate", "fake"],
            )
            json_result = self.runner.invoke(
                app,
                [
                    "status",
                    run.id,
                    "--substrate",
                    "fake",
                    "--format",
                    "json",
                ],
            )

        self.assertEqual(text_result.exit_code, 0, text_result.output)
        self.assertIn("State: running", text_result.output)
        self.assertEqual(json.loads(json_result.output)["run"]["id"], run.id)

    def test_topology_prints_normalized_resources(self) -> None:
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-topology")
        run = runtime.deploy(self.plan)
        with patch(
            "mininet_ai.cli.create_substrate_runtime",
            return_value=runtime,
        ):
            result = self.runner.invoke(
                app,
                ["topology", run.id, "--substrate", "fake"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Resource", result.output)
        self.assertIn("network", result.output)
        self.assertIn("switch", result.output)

    def test_stop_uses_external_stop_interface_when_available(self) -> None:
        runtime = StoppableFakeRuntime()
        run = runtime.deploy(self.plan)
        with patch(
            "mininet_ai.cli.create_substrate_runtime",
            return_value=runtime,
        ):
            result = self.runner.invoke(
                app,
                [
                    "stop",
                    run.id,
                    "--substrate",
                    "fake",
                    "--timeout",
                    "4.5",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(runtime.stop_requests, [(run.id, 4.5)])
        self.assertEqual(runtime.inspect(run.id).run.state, RunState.STOPPED)

    def test_runtime_errors_are_reported_with_code_and_nonzero_exit(self) -> None:
        runtime = FakeSubstrateRuntime()
        with patch(
            "mininet_ai.cli.create_substrate_runtime",
            return_value=runtime,
        ):
            result = self.runner.invoke(
                app,
                ["status", "missing", "--substrate", "fake"],
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("runtime.run.unknown", result.output)

    def test_invoke_runs_one_agent_and_writes_audit_json_lines(self) -> None:
        plan = configured_plan(
            {
                "message": "install a safe rule",
                "proposals": [
                    {
                        "id": "proposal-1",
                        "capability": "openflow.flow.install",
                        "target": "s1",
                        "arguments": {"match": "ip", "actions": "normal"},
                    }
                ],
            }
        )
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-agent-run")
        run = runtime.deploy(plan)
        with TemporaryDirectory() as temporary:
            audit_path = Path(temporary) / "audit" / "events.jsonl"
            with (
                patch("mininet_ai.cli._compile_or_exit", return_value=plan),
                patch(
                    "mininet_ai.cli.create_substrate_runtime",
                    return_value=runtime,
                ),
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "invoke",
                        "experiment.yaml",
                        run.id,
                        "switch-router@s1",
                        "--intent",
                        "repair forwarding",
                        "--audit-log",
                        str(audit_path),
                        "--format",
                        "json",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            payload = json.loads(result.output)
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(payload["actionResults"][0]["status"], "succeeded")
            records = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[0]["type"], "agent.invocation.started")
            self.assertEqual(
                records[-1]["type"],
                "capability.execution.completed",
            )

    def test_invoke_prints_failed_result_and_returns_nonzero(self) -> None:
        plan = configured_plan({"metadata": {}})
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-failed-agent")
        run = runtime.deploy(plan)
        with TemporaryDirectory() as temporary:
            audit_path = Path(temporary) / "audit.jsonl"
            with (
                patch("mininet_ai.cli._compile_or_exit", return_value=plan),
                patch(
                    "mininet_ai.cli.create_substrate_runtime",
                    return_value=runtime,
                ),
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "invoke",
                        "experiment.yaml",
                        run.id,
                        "switch-router@s1",
                        "--intent",
                        "inspect",
                        "--audit-log",
                        str(audit_path),
                        "--format",
                        "json",
                    ],
                )

        self.assertEqual(result.exit_code, 1)
        payload = json.loads(result.output)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(
            payload["issue"]["code"],
            "agent.agno.deterministic-response-invalid",
        )

    def test_invoke_discovers_plugins_only_when_requested(self) -> None:
        plan = configured_plan({"message": "done"})
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-plugins")
        run = runtime.deploy(plan)
        with TemporaryDirectory() as temporary:
            audit_path = Path(temporary) / "audit.jsonl"
            with (
                patch("mininet_ai.cli._compile_or_exit", return_value=plan),
                patch(
                    "mininet_ai.cli.create_substrate_runtime",
                    return_value=runtime,
                ),
                patch("mininet_ai.cli.discover_plugins", return_value=()) as discover,
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "invoke",
                        "experiment.yaml",
                        run.id,
                        "switch-router@s1",
                        "--intent",
                        "inspect",
                        "--audit-log",
                        str(audit_path),
                        "--discover-plugins",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        discover.assert_called_once()


if __name__ == "__main__":
    unittest.main()
