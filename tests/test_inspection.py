"""Inspection acceptance at the owning CLI and saved-source boundaries."""

import hashlib
import json
import os
import signal
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mininet_ai.artifacts import run_directory
from mininet_ai.cli import app
from mininet_ai.errors import MininetAIError
from mininet_ai.runtime.ledger import LedgerEntry, LedgerRecordCategory, SQLiteRunLedger
from mininet_ai.saved_runs import RunEvidence
from mininet_ai.substrates import FakeSubstrateRuntime
from tests.agents.test_runtime import configured_plan
from tests.storage_fixtures import storage_args
from tests.test_cli_runtime import fake_plan


def inspect_run(tmp_path, *, plan=None, extra_args=(), wait=None):
    runtime = FakeSubstrateRuntime(run_id_factory=lambda: "inspection-run")
    with (
        TemporaryDirectory(prefix="ctrl-", dir="/tmp/opencode") as control,
        patch("mininet_ai.cli._compile_or_exit", return_value=plan or fake_plan()),
        patch("mininet_ai.cli.reserve_run", return_value=("inspection-run", runtime)),
        patch("mininet_ai.cli._SignalLatch.wait", new=wait or (lambda latch: None)),
    ):
        result = CliRunner().invoke(
            app,
            ["run", "experiment.yaml", "--results-dir", str(tmp_path / "runs"),
             "--control-dir", control, "--format", "json", *extra_args],
        )
    return result, run_directory(tmp_path / "runs", "inspection-run")


def test_cli_publishes_private_equivalent_views_without_claiming_success(tmp_path):
    result, directory = inspect_run(tmp_path)
    assert result.exit_code == 0, result.output
    json.loads(result.stdout)
    summary = json.loads((directory / "summary.json").read_text())
    human = (directory / "summary.md").read_text()
    ledger = json.loads((directory / "evidence/ledger.json").read_text())
    assert summary["run_id"] == "inspection-run"
    assert summary["results_directory"] == str(directory)
    assert summary["started_at"] == ledger["manifest"]["createdAt"]
    assert summary["outcomes"]["experiment_success"]["value"] is None
    assert summary["outcomes"]["cleanup"]["status"] == "verified"
    assert str(directory) in result.stderr
    assert result.stderr.count("inspection-run") >= 2
    assert "summary.json" in human and "evidence/ledger.json" in human
    assert "experiment_success" in human
    for name in ("summary.json", "summary.md", "evidence/ledger.json"):
        assert (directory / name).stat().st_mode & 0o777 == 0o600
    assert (directory / "evidence").stat().st_mode & 0o777 == 0o700


def test_partial_observed_action_failures_do_not_invent_totals(tmp_path):
    close = SQLiteRunLedger.close

    def close_with_failures(ledger):
        for index in range(3):
            ledger.append(LedgerEntry(
                runId="inspection-run", category=LedgerRecordCategory.ACTION,
                type="capability.execution.failed", recordedAt=datetime.now(UTC),
                correlationId=f"invocation-{index}",
                data={"invocationId": f"invocation-{index}", "data": {
                    "code": "action.failed", "message": "controlled action failure"}},
            ))
        close(ledger)

    with patch.object(SQLiteRunLedger, "close", close_with_failures):
        result, directory = inspect_run(tmp_path)
    assert result.exit_code == 0, result.output
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["counts"]["actions"]["failed"]["value"] == 3
    assert summary["counts"]["actions"]["failed"]["coverage"] == "partial"
    assert summary["counts"]["actions"]["total"]["value"] is None
    assert summary["counts"]["actions"]["total"]["coverage"] == "unknown"
    assert len([p for p in summary["problems"] if p.get("code") == "action.failed"]) == 3


def test_complete_recorded_response_survives_without_raw_capture(tmp_path):
    response = {"message": "full structured answer " * 100,
                "proposals": [{"id": "p1", "capability": "openflow.flow.install",
                               "target": "s1", "arguments": {"match": "ip", "actions": "normal"}}]}
    result, directory = inspect_run(
        tmp_path, plan=configured_plan(response),
        extra_args=("--intent", "switch-router@s1=inspect forwarding"),
    )
    assert result.exit_code == 0, result.output
    ledger = json.loads((directory / "evidence/ledger.json").read_text())
    recorded = [r["data"]["data"]["response"] for r in ledger["records"]
                if r["type"] == "agent.invocation.completed"]
    assert len(recorded) == 1
    assert recorded[0]["message"] == response["message"]
    assert recorded[0]["proposals"] == [{**response["proposals"][0], "reason": None, "timeoutSeconds": 30.0}]
    assert ledger["manifest"]["contractVersion"] == "mininet-ai/run-ledger/v1alpha1"
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["counts"]["actions"]["succeeded"]["value"] == 1
    assert summary["counts"]["invocations"]["succeeded"]["value"] == 1


@pytest.mark.parametrize("failed_view", ["summary.md", "summary.json", "ledger.json"])
def test_inspection_write_failure_preserves_exit_and_sources(tmp_path, failed_view):
    link = os.link
    finish = RunEvidence.finish
    before = {}

    def capture_finish(evidence, **kwargs):
        finish(evidence, **kwargs)
        for path in evidence.metadata.sources.values():
            path = Path(path)
            if path.is_file():
                before[path] = hashlib.sha256(path.read_bytes()).hexdigest()

    def fail_link(source, destination, **kwargs):
        if destination == failed_view:
            raise OSError("controlled inspection failure")
        return link(source, destination, **kwargs)

    with patch.object(RunEvidence, "finish", capture_finish), patch("os.link", fail_link):
        result, directory = inspect_run(tmp_path)
    assert result.exit_code == 0, result.output
    assert "controlled inspection failure" in result.stderr
    assert failed_view in result.stderr
    assert not (directory / ("evidence" if failed_view == "ledger.json" else "") / failed_view).exists()
    assert before
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())


def test_partial_start_failure_keeps_original_error_and_unknown_start(tmp_path):
    with patch.object(FakeSubstrateRuntime, "deploy", side_effect=MininetAIError("original deployment failure")):
        result, directory = inspect_run(tmp_path)
    assert result.exit_code == 1, result.output
    assert "original deployment failure" in result.output
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["started_at"] is None
    assert summary["outcomes"]["operation"] == "failed"
    assert summary["outcomes"]["cleanup"]["status"] == "unknown"
    assert summary["outcomes"]["evidence_finalized"] is False
    assert any("original deployment failure" in p["reason"] for p in summary["problems"])
    assert summary["counts"]["actions"]["failed"]["value"] is None
    assert not (directory / "evidence/ledger.json").exists()


def test_original_failure_survives_secondary_inspection_failure(tmp_path):
    with (
        patch.object(FakeSubstrateRuntime, "deploy", side_effect=MininetAIError("original deployment failure")),
        patch("os.link", side_effect=OSError("secondary publication failure")),
    ):
        result, directory = inspect_run(tmp_path)
    assert result.exit_code == 1, result.output
    assert "original deployment failure" in result.output
    assert "secondary publication failure" in result.stderr
    assert not (directory / "summary.json").exists()


def test_missing_terminal_record_leaves_disputed_conclusion_unknown(tmp_path):
    close = SQLiteRunLedger.close

    def remove_terminal(ledger):
        close(ledger)
        with sqlite3.connect(ledger.path) as connection:
            connection.execute("DELETE FROM records WHERE sequence=(SELECT max(sequence) FROM records)")

    with patch.object(SQLiteRunLedger, "close", remove_terminal):
        result, directory = inspect_run(tmp_path)
    assert result.exit_code == 0, result.output
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["conclusions"]["execution"] is None
    assert summary["coverage"]["ledger"] == "partial"
    assert any("terminal" in problem["reason"] for problem in summary["problems"])
    assert summary["outcomes"]["cleanup"]["status"] == "verified"


@pytest.mark.parametrize("damage", ["malformed", "busy", "replaced"])
def test_unsafe_or_unreadable_ledger_is_unavailable_not_repaired(tmp_path, damage):
    finish = RunEvidence.finish
    held = []

    def damage_after_finish(evidence, **kwargs):
        finish(evidence, **kwargs)
        source = Path(evidence.metadata.sources["ledger"])
        if damage == "malformed":
            source.write_bytes(b"truncated database")
        elif damage == "busy":
            held.append(SQLiteRunLedger(source))
        else:
            source.unlink()
            source.write_bytes(b"replacement must not be read")
            source.chmod(0o600)

    try:
        with patch.object(RunEvidence, "finish", damage_after_finish):
            result, directory = inspect_run(tmp_path)
    finally:
        for writer in held:
            writer.close()
    assert result.exit_code == 0, result.output
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["inspection"]["evidence/ledger.json"] == "unavailable"
    assert summary["counts"]["actions"]["total"]["value"] is None
    assert not (directory / "evidence/ledger.json").exists()
    assert summary["problems"]
    if damage == "malformed":
        assert Path(summary["sources"]["ledger"]).read_bytes() == b"truncated database"


def test_observed_signal_does_not_claim_remote_termination(tmp_path):
    def signal_wait(latch):
        latch._request_stop(signal.SIGINT, None)

    result, directory = inspect_run(tmp_path, wait=signal_wait)
    assert result.exit_code == 0, result.output
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["outcomes"]["interruption_observed"] is True
    assert summary["outcomes"]["remote_provider_termination"] is None


def test_failed_invocation_is_not_hidden_by_successful_reasoning_or_cleanup(tmp_path):
    response = {"proposals": [{"id": "bad", "capability": "not.allowed", "target": "s1"}]}
    result, directory = inspect_run(tmp_path, plan=configured_plan(response),
                                    extra_args=("--intent", "switch-router@s1=inspect",))
    assert result.exit_code == 1, result.output
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["outcomes"]["cleanup"]["status"] == "verified"
    assert summary["counts"]["invocations"]["failed"]["value"] == 1
    assert summary["counts"]["invocations"]["succeeded"]["value"] == 0
    assert summary["counts"]["invocations"]["reasoning_completed"]["value"] == 1
    assert summary["observations"]["invocations"][0]["status"] != "succeeded"


def test_external_effective_sources_and_unavailable_diagnostics_are_honest(tmp_path):
    finish = RunEvidence.finish
    log = tmp_path / "external-diagnostics.log"

    def remove_log(evidence, **kwargs):
        finish(evidence, **kwargs)
        log.unlink()

    with patch.object(RunEvidence, "finish", remove_log):
        result, directory = inspect_run(tmp_path, extra_args=storage_args(tmp_path, log_file=log))
    assert result.exit_code == 0, result.output
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["sources"]["log"] == str(log)
    assert summary["source_availability"]["log"]["available"] is False
    assert summary["source_availability"]["log"]["reason"]
    assert summary["started_at"] is not None
    assert not log.exists()


def test_preexisting_inspection_file_is_not_overwritten(tmp_path):
    finish = RunEvidence.finish

    def existing_view(evidence, **kwargs):
        finish(evidence, **kwargs)
        (evidence.directory / "summary.md").write_text("existing private view")
        (evidence.directory / "summary.md").chmod(0o600)

    with patch.object(RunEvidence, "finish", existing_view):
        result, directory = inspect_run(tmp_path)
    assert result.exit_code == 0, result.output
    assert (directory / "summary.md").read_text() == "existing private view"
    assert "Inspection unavailable:" in result.stderr
    assert (directory / "summary.json").is_file()


@pytest.mark.parametrize("retry", [False, True])
def test_conflicting_terminal_records_are_not_confused_with_recorded_retry(tmp_path, retry):
    close = SQLiteRunLedger.close

    def terminal_records(ledger):
        types = ["run.failed"] + (["run.stop-retrying", "run.stopped"] if retry else [])
        for kind in types:
            ledger.append(LedgerEntry(runId="inspection-run",
                                     category=LedgerRecordCategory.RUN_LIFECYCLE,
                                     type=kind, recordedAt=datetime.now(UTC)))
        close(ledger)

    with patch.object(SQLiteRunLedger, "close", terminal_records):
        result, directory = inspect_run(tmp_path)
    assert result.exit_code == 0, result.output
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["conclusions"]["execution"] == ("succeeded" if retry else None)


def test_malformed_audit_payload_does_not_hide_independent_metadata(tmp_path):
    close = SQLiteRunLedger.close

    def malformed_record(ledger):
        ledger.append(LedgerEntry(runId="inspection-run", category=LedgerRecordCategory.ACTION,
                                 type="capability.execution.completed", recordedAt=datetime.now(UTC),
                                 data={"data": []}))
        close(ledger)

    with patch.object(SQLiteRunLedger, "close", malformed_record):
        result, directory = inspect_run(tmp_path)
    assert result.exit_code == 0, result.output
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["outcomes"]["cleanup"]["status"] == "verified"
    assert any("Malformed" in p["reason"] for p in summary["problems"])
