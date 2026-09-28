from __future__ import annotations

import stat
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from mininet_ai.audit import AuditEvent, AuditEventType, AuditSink
from mininet_ai.compiler import compile_experiment
from mininet_ai.runtime import (
    LedgerAuditSink,
    LedgerEntry,
    LedgerError,
    LedgerEventSink,
    LedgerRecordCategory,
    PluginManifest,
    RunLedger,
    RunManifest,
    RuntimeEvent,
    RuntimeEventType,
    SQLiteRunLedger,
)


ROOT = Path(__file__).parents[2]
EXPERIMENT = ROOT / "examples" / "phase1" / "experiment.yaml"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def run_manifest() -> RunManifest:
    return RunManifest.from_plan(
        "run-1",
        compile_experiment(EXPERIMENT),
        created_at=NOW,
        plugins=(
            PluginManifest(
                group="mininet_ai.capabilities",
                name="example.action",
                version="1.2.3",
                sourceDigest="sha256:" + "a" * 64,
            ),
        ),
        configuration={"randomSeed": 7, "modelEndpoint": "local"},
    )


class SQLiteRunLedgerTests(unittest.TestCase):
    def test_run_manifest_survives_close_and_reopen_in_owner_only_file(self) -> None:
        manifest = run_manifest()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "runs" / "run-1.sqlite3"
            ledger = SQLiteRunLedger(path)

            self.assertIsInstance(ledger, RunLedger)
            ledger.create_run(manifest)
            self.assertEqual(ledger.get_run("run-1"), manifest)
            ledger.close()

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            reopened = SQLiteRunLedger(path)
            try:
                self.assertEqual(reopened.get_run("run-1"), manifest)
            finally:
                reopened.close()

    def test_runtime_and_audit_sinks_preserve_existing_event_contracts(self) -> None:
        runtime_event = RuntimeEvent(
            eventId="event-1",
            runId="run-1",
            type=RuntimeEventType.RUNTIME_FAILURE,
            source="supervisor",
            subject="switch-router@s1",
            occurredAt=NOW,
            observedAt=NOW,
            sequence=4,
            correlationId="invoke-1",
            payload={"code": "runtime.worker.failed"},
        )
        audit_event = AuditEvent(
            recordedAt=NOW,
            type=AuditEventType.CAPABILITY_COMPLETED,
            runId="run-1",
            invocationId="invoke-1",
            agentId="switch-router@s1",
            data={"result": {"status": "succeeded"}},
        )

        with TemporaryDirectory() as temporary:
            ledger = SQLiteRunLedger(Path(temporary) / "ledger.sqlite3")
            try:
                ledger.create_run(run_manifest())
                event_sink = LedgerEventSink(ledger)
                audit_sink = LedgerAuditSink(ledger)

                self.assertIsInstance(audit_sink, AuditSink)
                event_sink.write(runtime_event)
                audit_sink.write(audit_event)

                records = ledger.records("run-1")
            finally:
                ledger.close()

        self.assertEqual(
            tuple(record.category for record in records),
            (
                LedgerRecordCategory.RUNTIME_EVENT,
                LedgerRecordCategory.ACTION,
            ),
        )
        self.assertEqual(records[0].data["eventId"], "event-1")
        self.assertEqual(
            records[1].data["contractVersion"],
            "mininet-ai/audit/v1alpha1",
        )

    def test_agno_usage_survives_the_audit_to_ledger_adapter(self) -> None:
        audit_event = AuditEvent(
            recordedAt=NOW,
            type=AuditEventType.AGENT_COMPLETED,
            runId="run-1",
            invocationId="invoke-1",
            agentId="switch-router@s1",
            data={
                "response": {"message": "done"},
                "runtime": {
                    "name": "agno",
                    "agnoRunId": "invoke-1",
                    "model": "gpt-5-mini",
                    "modelProvider": "OpenAI",
                    "metrics": {
                        "inputTokens": 21,
                        "outputTokens": 8,
                        "totalTokens": 29,
                        "cacheReadTokens": 4,
                        "reasoningTokens": 2,
                        "cost": 0.003,
                    },
                },
            },
        )

        with TemporaryDirectory() as temporary:
            ledger = SQLiteRunLedger(Path(temporary) / "ledger.sqlite3")
            try:
                ledger.create_run(run_manifest())
                LedgerAuditSink(ledger).write(audit_event)
                record = ledger.records("run-1")[0]
            finally:
                ledger.close()

        self.assertEqual(record.category, LedgerRecordCategory.INVOCATION)
        event_data = record.data["data"]
        assert isinstance(event_data, dict)
        runtime = event_data["runtime"]
        assert isinstance(runtime, dict)
        metrics = runtime["metrics"]
        assert isinstance(metrics, dict)
        self.assertEqual(metrics["totalTokens"], 29)
        self.assertEqual(metrics["cacheReadTokens"], 4)
        self.assertEqual(metrics["cost"], 0.003)

    def test_shared_state_audit_records_have_a_state_category(self) -> None:
        event = AuditEvent(
            recordedAt=NOW,
            type=AuditEventType.SHARED_STATE_UPDATED,
            runId="run-1",
            invocationId="invoke-1",
            agentId="switch-router@s1",
            data={
                "changes": [
                    {
                        "scope": "run",
                        "operation": "set",
                        "key": "preferred-path",
                        "value": "west",
                        "version": 1,
                    }
                ]
            },
        )

        record = LedgerEntry.from_audit_event(event)

        self.assertEqual(record.category, LedgerRecordCategory.STATE)
        self.assertEqual(record.type, "shared-state.updated")

    def test_unsafe_paths_and_invalid_run_operations_are_typed(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.sqlite3"
            path.write_bytes(b"")
            path.chmod(0o644)
            with self.assertRaises(LedgerError) as unsafe:
                SQLiteRunLedger(path)
            self.assertEqual(unsafe.exception.code, "ledger.path.unsafe")

            path.chmod(0o600)
            ledger = SQLiteRunLedger(path)
            ledger.create_run(run_manifest())
            with self.assertRaises(LedgerError) as duplicate:
                ledger.create_run(run_manifest())
            self.assertEqual(duplicate.exception.code, "ledger.run.duplicate")

            with self.assertRaises(LedgerError) as unknown:
                ledger.records("missing-run")
            self.assertEqual(unknown.exception.code, "ledger.run.unknown")

            ledger.close()
            ledger.close()
            with self.assertRaises(LedgerError) as closed:
                ledger.get_run("run-1")
            self.assertEqual(closed.exception.code, "ledger.closed")

    def test_concurrent_connections_assign_unique_per_run_sequences(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.sqlite3"
            first = SQLiteRunLedger(path)
            first.create_run(run_manifest())
            second = SQLiteRunLedger(path)

            def append(index: int) -> int:
                ledger = first if index % 2 == 0 else second
                return ledger.append(
                    LedgerEntry(
                        runId="run-1",
                        category=LedgerRecordCategory.METRIC,
                        type="test.concurrent",
                        recordedAt=NOW,
                        correlationId=f"record-{index}",
                        data={"index": index},
                    )
                ).sequence

            try:
                with ThreadPoolExecutor(max_workers=8) as executor:
                    sequences = tuple(executor.map(append, range(20)))
                records = first.records("run-1")
            finally:
                first.close()
                second.close()

        self.assertEqual(sorted(sequences), list(range(1, 21)))
        self.assertEqual(
            tuple(record.sequence for record in records),
            tuple(range(1, 21)),
        )

    def test_newer_ledger_schema_is_not_silently_rewritten(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA user_version = 99")
            connection.close()
            path.chmod(0o600)

            with self.assertRaises(LedgerError) as unsupported:
                SQLiteRunLedger(path)

            self.assertEqual(
                unsupported.exception.code,
                "ledger.version.unsupported",
            )
            check = sqlite3.connect(path)
            try:
                version = check.execute("PRAGMA user_version").fetchone()[0]
            finally:
                check.close()
            self.assertEqual(version, 99)

    def test_records_are_append_only_ordered_and_continue_after_reopen(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.sqlite3"
            ledger = SQLiteRunLedger(path)
            ledger.create_run(run_manifest())
            first = ledger.append(
                LedgerEntry(
                    runId="run-1",
                    category=LedgerRecordCategory.RUN_LIFECYCLE,
                    type="run.started",
                    recordedAt=NOW,
                    data={"state": "running"},
                )
            )
            second = ledger.append(
                LedgerEntry(
                    runId="run-1",
                    category=LedgerRecordCategory.METRIC,
                    type="latency.detected",
                    recordedAt=NOW,
                    correlationId="event-1",
                    data={"milliseconds": 2.5},
                )
            )
            ledger.close()

            self.assertEqual((first.sequence, second.sequence), (1, 2))
            reopened = SQLiteRunLedger(path)
            try:
                third = reopened.append(
                    LedgerEntry(
                        runId="run-1",
                        category=LedgerRecordCategory.RUN_LIFECYCLE,
                        type="run.stopped",
                        recordedAt=NOW,
                    )
                )

                self.assertEqual(third.sequence, 3)
                self.assertEqual(
                    reopened.records("run-1", after_sequence=1),
                    (second, third),
                )
                self.assertEqual(
                    reopened.records("run-1", after_sequence=0, limit=1),
                    (first,),
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
