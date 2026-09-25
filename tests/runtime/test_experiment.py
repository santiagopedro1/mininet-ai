from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from mininet_ai.agents import register_builtin_providers
from mininet_ai.audit import AuditRecorder
from mininet_ai.experiment import (
    ExperimentRuntime,
    ExperimentRuntimeState,
)
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime import (
    LedgerAuditSink,
    LedgerError,
    LedgerRecordCategory,
    RunManifest,
    SQLiteRunLedger,
)
from mininet_ai.substrates import FakeSubstrateRuntime, RunState
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


if __name__ == "__main__":
    unittest.main()
