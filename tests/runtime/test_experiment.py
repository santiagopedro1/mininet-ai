from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mininet_ai.agents import register_builtin_providers
from mininet_ai.audit import AuditRecorder
from mininet_ai.compiler.models import CoordinationEdge, CoordinationPlan
from mininet_ai.experiment import (
    ExperimentRuntime,
    ExperimentRuntimeState,
)
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime import (
    ContinuousRuntimeError,
    ContinuousRuntimeReport,
    LedgerAuditSink,
    LedgerError,
    LedgerRecordCategory,
    RunManifest,
    SQLiteRunLedger,
    TelemetryPipelineReport,
)
from mininet_ai.substrates import FakeSubstrateRuntime, RunState
from mininet_ai.specification.models import CoordinationMode
from tests.agents.test_runtime import configured_plan


NOW = datetime(2026, 1, 2, tzinfo=UTC)


class ExperimentRuntimeTests(unittest.TestCase):
    def test_owner_runs_intent_and_persists_complete_ordered_history(self) -> None:
        plan = configured_plan({"message": "handled continuously"})
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "owner-run")
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)

        with TemporaryDirectory() as temporary:
            ledger = SQLiteRunLedger(Path(temporary) / "runs.sqlite3")
            owner = ExperimentRuntime(
                plan,
                substrate,
                registries,
                audit=AuditRecorder(LedgerAuditSink(ledger)),
                ledger=ledger,
                event_id_factory=lambda: "manual-event-1",
            )

            run = owner.start()
            event = owner.submit_intent(
                "switch-router@s1",
                "inspect forwarding",
            )
            report = owner.stop()
            repeated = owner.stop()
            records = ledger.records(run.id)
            manifest = ledger.get_run(run.id)
            ledger.close()

        self.assertEqual(run.id, "owner-run")
        self.assertEqual(event.event_id, "manual-event-1")
        self.assertEqual(report.state, ExperimentRuntimeState.STOPPED)
        self.assertEqual(report.continuous.completed, 1)
        self.assertEqual(report.continuous.failed, 0)
        invocation = report.continuous.invocations[0]
        self.assertIsNotNone(invocation.coordination)
        assert invocation.coordination is not None
        self.assertEqual(invocation.coordination.correlation_id, event.event_id)
        self.assertEqual(
            invocation.coordination.requested_agent_id,
            "switch-router@s1",
        )
        self.assertEqual(
            invocation.coordination.entry_agent_id,
            "switch-router@s1",
        )
        self.assertEqual(
            invocation.coordination.messages[0].triggering_event_id,
            event.event_id,
        )
        self.assertEqual(
            invocation.result.invocation_id,
            invocation.coordination.invocations[0].result.invocation_id,
        )
        self.assertEqual(report, repeated)
        assert report.teardown is not None
        self.assertEqual(report.teardown.run.state, RunState.STOPPED)
        self.assertEqual(owner.state, ExperimentRuntimeState.STOPPED)
        self.assertEqual(manifest.plan_digest, plan.digest)
        self.assertEqual(
            tuple(record.sequence for record in records),
            tuple(range(1, len(records) + 1)),
        )
        self.assertEqual(records[0].type, "run.starting")
        self.assertIn("intent.manual", [record.type for record in records])
        self.assertIn(
            LedgerRecordCategory.INVOCATION,
            [record.category for record in records],
        )
        self.assertEqual(records[-1].type, "run.stopped")

    def test_owner_routes_manual_intent_through_central_coordinator(self) -> None:
        plan = configured_plan({"message": "coordinated"})
        coordinator = "global-router"
        plan = plan.model_copy(
            update={
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.CENTRALIZED,
                    edges=tuple(
                        CoordinationEdge(
                            source=coordinator,
                            target=agent.id,
                            relationship="coordinates",
                        )
                        for agent in plan.agents
                        if agent.id != coordinator
                    ),
                )
            }
        )
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "central-run")
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            event_id_factory=lambda: "central-event-1",
        )

        owner.start()
        owner.submit_intent("switch-router@s1", "inspect forwarding")
        report = owner.stop()

        record = report.continuous.invocations[0]
        self.assertIsNotNone(record.coordination)
        assert record.coordination is not None
        self.assertEqual(record.coordination.entry_agent_id, coordinator)
        self.assertEqual(record.result.agent_id, coordinator)
        self.assertEqual(
            record.coordination.messages[0].target_agent_id,
            coordinator,
        )

    def test_manifest_failure_rolls_back_the_deployed_substrate(self) -> None:
        plan = configured_plan({"message": "unused"})
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "duplicate-run")
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)

        with TemporaryDirectory() as temporary:
            ledger = SQLiteRunLedger(Path(temporary) / "runs.sqlite3")
            ledger.create_run(
                RunManifest.from_plan(
                    "duplicate-run",
                    plan,
                    created_at=NOW,
                )
            )
            owner = ExperimentRuntime(
                plan,
                substrate,
                registries,
                ledger=ledger,
            )

            with self.assertRaises(LedgerError):
                owner.start()

            ledger.close()

        self.assertEqual(owner.state, ExperimentRuntimeState.FAILED)
        self.assertEqual(
            substrate.inspect("duplicate-run").run.state,
            RunState.STOPPED,
        )

    def test_stop_does_not_teardown_until_runtime_workers_have_stopped(self) -> None:
        class RetryableContinuous:
            def __init__(self) -> None:
                self.stop_calls = 0

            def start(self) -> None:
                pass

            def stop(self, **options) -> ContinuousRuntimeReport:
                del options
                self.stop_calls += 1
                if self.stop_calls == 1:
                    raise ContinuousRuntimeError(
                        "worker still active",
                        code="runtime.stop.timeout",
                    )
                return ContinuousRuntimeReport()

            def report(self) -> ContinuousRuntimeReport:
                return ContinuousRuntimeReport()

        class StoppableTelemetry:
            def start(self) -> None:
                pass

            def stop(self, **options) -> TelemetryPipelineReport:
                del options
                return TelemetryPipelineReport()

            def report(self) -> TelemetryPipelineReport:
                return TelemetryPipelineReport()

        plan = configured_plan({"message": "unused"})
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "retry-run")
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        continuous = RetryableContinuous()
        telemetry = StoppableTelemetry()
        owner = ExperimentRuntime(plan, substrate, registries)

        with (
            patch(
                "mininet_ai.experiment.ContinuousAgentRuntime",
                return_value=continuous,
            ),
            patch(
                "mininet_ai.experiment.TelemetryPipeline",
                return_value=telemetry,
            ),
        ):
            owner.start()
            failed = owner.stop(timeout_seconds=0.01)

            self.assertEqual(failed.state, ExperimentRuntimeState.FAILED)
            self.assertIsNone(failed.teardown)
            self.assertEqual(
                substrate.inspect("retry-run").run.state,
                RunState.RUNNING,
            )

            stopped = owner.stop(timeout_seconds=1)

        self.assertEqual(stopped.state, ExperimentRuntimeState.STOPPED)
        assert stopped.teardown is not None
        self.assertEqual(stopped.run.state, RunState.STOPPED)
        self.assertEqual(continuous.stop_calls, 2)


if __name__ == "__main__":
    unittest.main()
