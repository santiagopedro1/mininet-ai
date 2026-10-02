from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

from mininet_ai.agents import register_builtin_providers
from mininet_ai.audit import AuditRecorder, MemoryAuditSink
from mininet_ai.cli import app
from mininet_ai.compiler import compile_experiment
from mininet_ai.experiment import ExperimentRuntime
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime.control import IntentServer
from mininet_ai.substrates import FakeSubstrateRuntime, RunState
from tests.agents.test_runtime import configured_plan
from tests.compiler.helpers import compiler_multilayer_snapshot, experiment_from


def fake_plan():
    return compile_experiment(experiment_from(compiler_multilayer_snapshot()))


class StoppableFakeRuntime(FakeSubstrateRuntime):
    def __init__(self) -> None:
        super().__init__(run_id_factory=lambda: "cli-stop-run")
        self.stop_requests: list[tuple[str, float]] = []

    def request_stop(self, run_id: str, *, timeout_seconds: float = 30):
        self.stop_requests.append((run_id, timeout_seconds))
        return self.teardown(run_id)


class AutoStopLatch:
    """Test latch that only releases when runtime failure requests a stop."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *error: object) -> None:
        pass

    def request_stop(self) -> None:
        self._event.set()

    def wait(self) -> None:
        if not self._event.wait(1):
            raise TimeoutError("run did not stop after its invocation failed")


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
            TemporaryDirectory() as temporary,
            patch("mininet_ai.cli._compile_or_exit", return_value=self.plan),
            patch(
                "mininet_ai.cli.create_substrate_runtime",
                return_value=runtime,
            ),
            patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
        ):
            result = self.runner.invoke(
                app,
                ["run", "experiment.yaml", *self.run_databases(temporary)],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Running cli-run", result.output)
        self.assertIn("Stopped cli-run", result.output)
        self.assertEqual(runtime.inspect("cli-run").run.state, RunState.STOPPED)

    def test_run_accepts_initial_intent_and_prints_final_json_report(self) -> None:
        plan = configured_plan({"message": "handled"})
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-json-run")
        with (
            TemporaryDirectory() as temporary,
            patch("mininet_ai.cli._compile_or_exit", return_value=plan),
            patch(
                "mininet_ai.cli.create_substrate_runtime",
                return_value=runtime,
            ),
            patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
        ):
            result = self.runner.invoke(
                app,
                [
                    "run",
                    "experiment.yaml",
                    "--intent",
                    "switch-router@s1=inspect forwarding",
                    "--format",
                    "json",
                    *self.run_databases(temporary),
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.output)
        self.assertEqual(report["state"], "stopped")
        self.assertEqual(report["continuous"]["completed"], 1)
        self.assertEqual(report["continuous"]["failed"], 0)
        self.assertEqual(
            runtime.inspect("cli-json-run").run.state,
            RunState.STOPPED,
        )

    def test_run_verbose_streams_progress_and_writes_text_log(self) -> None:
        plan = configured_plan({"message": "handled"})
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-verbose-run")
        with TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "run.log"
            with (
                patch("mininet_ai.cli._compile_or_exit", return_value=plan),
                patch(
                    "mininet_ai.cli.create_substrate_runtime",
                    return_value=runtime,
                ),
                patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "run",
                        "experiment.yaml",
                        "--verbose",
                        "--intent",
                        "switch-router@s1=inspect forwarding",
                        *self.run_databases(temporary),
                    ],
                )

            log = log_path.read_text(encoding="utf-8")
            log_mode = log_path.stat().st_mode & 0o777

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Starting fake substrate and runtime services", result.output)
        self.assertIn("Queued intent", result.output)
        self.assertIn("agent.invocation.started", result.output)
        self.assertIn("Run cli-verbose-run stopped", result.output)
        self.assertIn("Prepared experiment", log)
        self.assertIn("agent.invocation.completed", log)
        self.assertIn("Run cli-verbose-run stopped", log)
        self.assertEqual(log_mode, 0o600)

    def test_run_reports_invocation_failure_live_and_stops_automatically(self) -> None:
        plan = configured_plan({"metadata": {}})
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-failed-run")
        with TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "run.log"
            with (
                patch("mininet_ai.cli._compile_or_exit", return_value=plan),
                patch(
                    "mininet_ai.cli.create_substrate_runtime",
                    return_value=runtime,
                ),
                patch("mininet_ai.cli._SignalLatch", AutoStopLatch),
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "run",
                        "experiment.yaml",
                        "--verbose",
                        "--intent",
                        "switch-router@s1=inspect forwarding",
                        *self.run_databases(temporary),
                    ],
                )

            log = log_path.read_text(encoding="utf-8")

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertNotIsInstance(result.exception, TimeoutError)
        self.assertIn("agent.agno.deterministic-response-invalid", result.output)
        self.assertIn("agent.agno.deterministic-response-invalid", log)
        self.assertIn(" ERROR agent.invocation.failed:", log)
        self.assertLess(
            log.index("agent.agno.deterministic-response-invalid"),
            log.index("Stop requested; draining work and tearing down"),
        )
        self.assertEqual(runtime.inspect("cli-failed-run").run.state, RunState.STOPPED)

    def test_run_can_stop_after_all_initial_intents_complete(self) -> None:
        plan = configured_plan({"message": "handled"})
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-intents-run")
        with TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "run.log"
            with (
                patch("mininet_ai.cli._compile_or_exit", return_value=plan),
                patch(
                    "mininet_ai.cli.create_substrate_runtime",
                    return_value=runtime,
                ),
                patch("mininet_ai.cli._SignalLatch", AutoStopLatch),
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "run",
                        "experiment.yaml",
                        "--stop-after-intents",
                        "--intent",
                        "switch-router@s1=first intent",
                        "--intent",
                        "switch-router@s1=second intent",
                        *self.run_databases(temporary),
                    ],
                )

            log = log_path.read_text(encoding="utf-8")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "All 2 initial intents finished; requesting automatic stop",
            log,
        )
        self.assertLess(
            log.index("All 2 initial intents finished"),
            log.index("Stop requested; draining work and tearing down"),
        )
        self.assertIn("2 invocations", result.output)
        self.assertEqual(runtime.inspect("cli-intents-run").run.state, RunState.STOPPED)

    def test_run_tears_down_if_reporting_the_started_run_fails(self) -> None:
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-broken-output")
        with (
            TemporaryDirectory() as temporary,
            patch("mininet_ai.cli._compile_or_exit", return_value=self.plan),
            patch(
                "mininet_ai.cli.create_substrate_runtime",
                return_value=runtime,
            ),
            patch("mininet_ai.cli.console.print", side_effect=BrokenPipeError),
        ):
            result = self.runner.invoke(
                app,
                ["run", "experiment.yaml", *self.run_databases(temporary)],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(
            runtime.inspect("cli-broken-output").run.state,
            RunState.STOPPED,
        )

    @staticmethod
    def run_databases(directory: str) -> list[str]:
        root = Path(directory)
        return [
            "--ledger-db",
            str(root / "ledger.sqlite3"),
            "--agno-db",
            str(root / "agno.sqlite3"),
            "--shared-state-db",
            str(root / "state.sqlite3"),
            "--log-file",
            str(root / "run.log"),
            "--control-dir",
            str(root / "control"),
        ]

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

    def test_invoke_queues_in_owner_and_owner_executes_authorized_action(self) -> None:
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
        registries = ProviderRegistries()
        register_builtin_providers(registries, runtime)
        sink = MemoryAuditSink()
        owner = ExperimentRuntime(plan, runtime, registries, audit=AuditRecorder(sink))
        run = owner.start()
        with TemporaryDirectory() as temporary:
            control_dir = Path(temporary) / "control"
            with (
                patch("mininet_ai.cli._compile_or_exit", side_effect=AssertionError("must not compile YAML")) as compile,
                IntentServer(owner, control_dir),
                patch("mininet_ai.cli.create_substrate_runtime") as create,
                patch("mininet_ai.cli.discover_plugins") as discover,
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "invoke",
                        run.id,
                        "switch-router@s1",
                        "--intent",
                        "repair forwarding",
                        "--control-dir",
                        str(control_dir),
                        "--audit-log",
                        str(Path(temporary) / "unused-audit.jsonl"),
                        "--agno-db",
                        str(Path(temporary) / "unused-agno.sqlite3"),
                        "--shared-state-db",
                        str(Path(temporary) / "unused-state.sqlite3"),
                        "--discover-plugins",
                        "--format",
                        "json",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["type"], "intent.manual")
            self.assertEqual(payload["payload"]["intent"], "repair forwarding")
            create.assert_not_called()
            compile.assert_not_called()
            discover.assert_not_called()
            self.assertIn("Deprecated invoke configuration options", result.stderr)
            self.assertFalse((Path(temporary) / "unused-audit.jsonl").exists())
            self.assertFalse((Path(temporary) / "unused-agno.sqlite3").exists())
            self.assertFalse((Path(temporary) / "unused-state.sqlite3").exists())
            report = owner.stop()
            self.assertEqual(report.continuous.completed, 1)
            invocation = report.continuous.invocations[0]
            self.assertEqual(invocation.result.action_results[0].status.value, "succeeded")
            self.assertIn("capability.execution.completed", [event.type.value for event in sink.events])

    def test_invoke_without_owner_returns_nonzero_without_creating_runtime(self) -> None:
        plan = configured_plan({"metadata": {}})
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-failed-agent")
        run = runtime.deploy(plan)
        with (
            TemporaryDirectory() as temporary,
            patch("mininet_ai.cli._compile_or_exit", side_effect=AssertionError("must not compile YAML")) as compile,
            patch("mininet_ai.cli.create_substrate_runtime") as create,
        ):
            result = self.runner.invoke(
                app,
                [
                    "invoke",
                    run.id,
                    "switch-router@s1",
                    "--intent",
                    "inspect",
                    "--control-dir",
                    str(Path(temporary) / "control"),
                    "--format",
                    "json",
                ],
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("runtime.control.unavailable", result.output)
        self.assertIn("could not contact run", result.output)
        self.assertNotIn("could not submit intent", result.output)
        create.assert_not_called()
        compile.assert_not_called()

    def test_invoke_rejects_unknown_agent_in_owner(self) -> None:
        plan = configured_plan({"message": "done"})
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-plugins")
        registries = ProviderRegistries()
        register_builtin_providers(registries, runtime)
        owner = ExperimentRuntime(plan, runtime, registries)
        run = owner.start()
        with TemporaryDirectory() as temporary:
            control_dir = Path(temporary) / "control"
            with (
                IntentServer(owner, control_dir),
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "invoke",
                        run.id,
                        "missing-agent",
                        "--intent",
                        "inspect",
                        "--control-dir",
                        str(control_dir),
                    ],
                )

        report = owner.stop()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("experiment.intent.invalid-agent", result.output)
        self.assertEqual(report.continuous.completed, 0)

    def test_agents_discovers_live_instances_in_text_and_json(self) -> None:
        plan = configured_plan({"message": "done"})
        runtime = FakeSubstrateRuntime(run_id_factory=lambda: "cli-agents")
        registries = ProviderRegistries()
        register_builtin_providers(registries, runtime)
        owner = ExperimentRuntime(plan, runtime, registries)
        run = owner.start()
        self.addCleanup(owner.stop)
        with TemporaryDirectory() as temporary:
            control = Path(temporary) / "control"
            with (
                IntentServer(owner, control),
                patch("mininet_ai.cli._compile_or_exit", side_effect=AssertionError("must not compile YAML")),
                patch("mininet_ai.cli.create_substrate_runtime") as create,
            ):
                args = ["agents", run.id, "--control-dir", str(control)]
                text_result = self.runner.invoke(app, args)
                json_result = self.runner.invoke(app, [*args, "--format", "json"])
                self.assertEqual(text_result.exit_code, 0, text_result.output)
                self.assertIn("switch-router@s1", text_result.stdout)
                self.assertIn("Manual intents", text_result.stdout)
                self.assertEqual(json_result.exit_code, 0, json_result.output)
                payload = json.loads(json_result.stdout)
                self.assertEqual(payload["runId"], run.id)
                self.assertEqual(payload["planDigest"], plan.digest)
                self.assertEqual(payload["agents"], list(owner.agent_ids))
                self.assertEqual(payload["manualAgents"], list(owner.manual_agent_ids))
                create.assert_not_called()
        self.assertEqual(owner.stop().continuous.completed, 0)

    def test_agents_without_owner_returns_nonzero(self) -> None:
        with TemporaryDirectory() as temporary:
            result = self.runner.invoke(app, [
                "agents", "missing-run", "--control-dir", str(Path(temporary) / "control"),
            ])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("runtime.control.unavailable", result.output)
        self.assertIn("could not contact run", result.output)
        self.assertNotIn("could not submit intent", result.output)


if __name__ == "__main__":
    unittest.main()
