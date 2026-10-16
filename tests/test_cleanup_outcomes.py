"""Public outcome semantics; fake evidence is never host-security evidence."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from mininet_ai.artifacts import run_directory
from mininet_ai.cli import app
from mininet_ai.errors import RuntimeOperationError
from mininet_ai.outcomes import (
    CleanupOutcome,
    CleanupStatus,
    MutationEffects,
    OperationOutcome,
)
from mininet_ai.substrates import (
    ActionResult,
    ActionStatus,
    FakeSubstrateRuntime,
    TeardownResult,
)
from tests.substrates.test_mininet_ovs_runtime import mininet_plan, recording_runtime
from tests.test_cli_runtime import fake_plan

VERIFIED = CleanupOutcome(
    status=CleanupStatus.VERIFIED,
    evidence=("test model has no active resources; not host verification",),
    evidence_gaps=(),
)
INCOMPLETE = CleanupOutcome(
    status=CleanupStatus.INCOMPLETE,
    remaining_resources=("s1",),
    unknown_resources=("worker descendants",),
    evidence=("test model still contains s1",),
)
UNKNOWN = CleanupOutcome(unknown_resources=("s1",))


@pytest.fixture
def control_dir():
    # Unix sockets have a short platform limit; pytest's VM paths are longer.
    with TemporaryDirectory(prefix="gh62-") as directory:
        yield Path(directory) / "control"


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"evidence": ("tool returned zero",)},
        {"evidence": ("test",), "evidence_gaps": (), "unknown_resources": ("s1",)},
        {"evidence": ("test",), "evidence_gaps": (), "remaining_resources": ("s1",)},
    ],
)
def test_verified_cleanup_cannot_hide_missing_evidence(fields):
    with pytest.raises(ValidationError):
        CleanupOutcome(status=CleanupStatus.VERIFIED, **fields)


def test_repeated_completion_requires_verified_receipt():
    runtime = FakeSubstrateRuntime()
    run = runtime.deploy(fake_plan())
    first = runtime.teardown(run.id)
    second = runtime.teardown(run.id)
    assert second.already_stopped and second.succeeded
    assert first.cleanup == second.cleanup
    unknown_run = first.run.model_copy(update={"cleanup": UNKNOWN})
    with pytest.raises(ValidationError, match="verified successful completion"):
        TeardownResult(
            run=unknown_run,
            cleanup=UNKNOWN,
            operation=OperationOutcome.SUCCEEDED,
            already_stopped=True,
        )


def test_old_stopped_record_defaults_to_unknown_not_completion():
    from mininet_ai.substrates import RunInfo

    runtime = FakeSubstrateRuntime()
    run = runtime.deploy(fake_plan())
    stopped = runtime.teardown(run.id)
    old_document = stopped.run.model_dump(
        mode="json", exclude={"cleanup", "operation", "effects"}
    )
    restored = RunInfo.model_validate(old_document)
    result = TeardownResult(run=restored, operation=OperationOutcome.SUCCEEDED)
    assert result.cleanup.status == CleanupStatus.UNKNOWN
    assert result.cleanup.evidence_gaps
    assert not result.succeeded and not result.already_stopped


@pytest.mark.parametrize("effects", [MutationEffects.PARTIAL, MutationEffects.UNKNOWN])
def test_action_failure_preserves_nonboolean_mutation_effects(effects):
    from datetime import UTC, datetime

    from mininet_ai.substrates import RuntimeIssue

    result = ActionResult(
        run_id="run",
        request_id="action",
        status=ActionStatus.FAILED,
        completed_at=datetime.now(UTC),
        effects=effects,
        issue=RuntimeIssue(code="runtime.action.lost-reply", message="reply lost"),
    )
    assert ActionResult.model_validate_json(result.model_dump_json()).effects == effects
    error = RuntimeOperationError(
        "reply lost",
        code="runtime.deploy.unknown",
        operation=OperationOutcome.UNKNOWN,
        effects=effects,
        cleanup=UNKNOWN,
    )
    assert error.effects == effects
    assert error.cleanup == UNKNOWN


class OutcomeRuntime(FakeSubstrateRuntime):
    def __init__(self, cleanup, operation=OperationOutcome.SUCCEEDED):
        super().__init__(run_id_factory=lambda: "outcome-run")
        self.cleanup_outcome = cleanup
        self.stop_operation = operation

    def teardown(self, run_id):
        result = super().teardown(run_id)
        run = result.run.model_copy(update={"cleanup": self.cleanup_outcome})
        return TeardownResult(
            run=run,
            cleanup=self.cleanup_outcome,
            operation=self.stop_operation,
            effects=MutationEffects.UNKNOWN,
        )


@pytest.mark.parametrize(
    "cleanup,operation,exit_code",
    [
        (VERIFIED, OperationOutcome.SUCCEEDED, 0),
        (VERIFIED, OperationOutcome.FAILED, 1),
        (UNKNOWN, OperationOutcome.SUCCEEDED, 1),
        (INCOMPLETE, OperationOutcome.SUCCEEDED, 1),
        (UNKNOWN, OperationOutcome.ACCEPTED, 1),
    ],
)
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_stop_preserves_operation_and_cleanup(
    cleanup, operation, exit_code, output_format
):
    runtime = OutcomeRuntime(cleanup, operation)
    run = runtime.deploy(fake_plan())
    with patch("mininet_ai.cli.create_substrate_runtime", return_value=runtime):
        result = CliRunner().invoke(app, ["stop", run.id, "--format", output_format])
    assert result.exit_code == exit_code, result.output
    if output_format == "json":
        output = json.loads(result.stdout)
        assert output["operation"] == operation
        assert output["cleanup"] == cleanup.model_dump(mode="json")
    else:
        assert f"stop operation: {operation}" in result.stdout
        assert f"Cleanup: {cleanup.status}" in result.stdout
        assert "Stopped" not in result.stdout


@pytest.mark.parametrize(
    "cleanup,exit_code", [(VERIFIED, 0), (UNKNOWN, 1), (INCOMPLETE, 1)]
)
def test_run_and_saved_status_keep_work_success_separate_from_cleanup(
    tmp_path, control_dir, cleanup, exit_code
):
    runtime = OutcomeRuntime(cleanup)
    root = tmp_path / "runs"
    with (
        patch("mininet_ai.cli._compile_or_exit", return_value=fake_plan()),
        patch("mininet_ai.cli.reserve_run", return_value=("outcome-run", runtime)),
        patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                "experiment.yaml",
                "--format",
                "json",
                "--artifact-root",
                str(root),
                "--control-dir",
                str(control_dir),
            ],
        )
    assert result.exit_code == exit_code, result.output
    report = json.loads(result.stdout)
    assert report["operation"] == "succeeded"
    assert report["cleanup"] == cleanup.model_dump(mode="json")
    saved = json.loads((run_directory(root, "outcome-run") / "run.json").read_text())
    assert saved["operation"] == "succeeded"
    assert saved["cleanup"] == report["cleanup"]
    assert saved["outcome"] == ("succeeded" if exit_code == 0 else "failed")
    if cleanup.verified:
        exported = CliRunner().invoke(
            app,
            [
                "export",
                "outcome-run",
                "--artifact-root",
                str(root),
                "--destination",
                str(tmp_path / "bundle"),
                "--acknowledge-sensitive-data",
            ],
        )
        assert exported.exit_code == 0, exported.output
        manifest = json.loads((tmp_path / "bundle" / "manifest.json").read_text())
        assert manifest["operation"] == "succeeded"
        assert manifest["cleanup"] == saved["cleanup"]


def test_run_failure_can_have_verified_cleanup(tmp_path):
    class FailedStart(FakeSubstrateRuntime):
        def deploy(self, plan):
            raise RuntimeOperationError(
                "deployment failed",
                code="runtime.deploy.failed",
                cleanup=VERIFIED,
                effects=MutationEffects.PARTIAL,
            )

    with (
        patch("mininet_ai.cli._compile_or_exit", return_value=fake_plan()),
        patch(
            "mininet_ai.cli.reserve_run", return_value=("outcome-run", FailedStart())
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                "experiment.yaml",
                "--format",
                "json",
                "--artifact-root",
                str(tmp_path / "runs"),
            ],
        )
    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["operation"] == "failed"
    assert output["effects"] == "partial"
    assert output["cleanup"]["status"] == "verified"


def test_operation_error_after_start_reports_final_cleanup(tmp_path, control_dir):
    runtime = FakeSubstrateRuntime(run_id_factory=lambda: "run-outcome")
    with (
        patch("mininet_ai.cli._compile_or_exit", return_value=fake_plan()),
        patch("mininet_ai.cli.reserve_run", return_value=("run-outcome", runtime)),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                "experiment.yaml",
                "--format",
                "json",
                "--artifact-root",
                str(tmp_path / "runs"),
                "--control-dir",
                str(control_dir),
                "--intent",
                "nonexistent=inspect",
            ],
        )
    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["issue"]["code"] == "experiment.intent.invalid-agent"
    assert output["operation"] == "failed"
    assert output["cleanup"]["status"] == "verified"
    saved = json.loads((tmp_path / "runs" / "run-outcome" / "run.json").read_text())
    assert saved["operation"] == "failed"
    assert saved["cleanup"]["status"] == "verified"


def test_legacy_missing_state_does_not_manufacture_completion():
    from mininet_ai.substrates.mininet_ovs.state import RunStateStore
    from tests.substrates.test_mininet_ovs_runtime import temporary_store

    harness = unittest.TestCase()
    try:
        store = temporary_store(harness)
        owner = recording_runtime(harness, state_store=store)
        run = owner.deploy(mininet_plan())
        controller = recording_runtime(
            harness, state_store=RunStateStore(store.state_directory, store.lock_path)
        )

        def stop_without_receipt(pid, number):
            owner.teardown(run.id)
            store.acquire()
            try:
                store.clear_stopped()
            finally:
                store.release()

        with (
            patch(
                "mininet_ai.substrates.mininet_ovs.runtime.os.kill",
                side_effect=stop_without_receipt,
            ),
            pytest.raises(RuntimeOperationError) as raised,
        ):
            controller.request_stop(run.id)
        assert raised.value.code == "runtime.run.unknown"
        assert not raised.value.cleanup.verified
    finally:
        harness.doCleanups()


@pytest.mark.parametrize("command", ["stop", "status"])
def test_missing_run_evidence_is_a_typed_json_error(command):
    with patch(
        "mininet_ai.cli.create_substrate_runtime", return_value=FakeSubstrateRuntime()
    ):
        result = CliRunner().invoke(app, [command, "missing", "--format", "json"])
    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["issue"]["code"] == "runtime.run.unknown"
    assert output["cleanup"]["status"] == "unknown"
    assert output["effects"] == "unknown"


def test_legacy_teardown_and_persisted_status_never_certify_tool_success():
    harness = unittest.TestCase()
    try:
        runtime = recording_runtime(harness)
        run = runtime.deploy(mininet_plan())
        first = runtime.teardown(run.id)
        second = runtime.teardown(run.id)
        assert first.operation == OperationOutcome.SUCCEEDED
        assert first.cleanup.status == CleanupStatus.UNKNOWN
        assert first.cleanup.unknown_resources
        assert not first.succeeded and not second.succeeded
        assert not second.already_stopped
        with patch("mininet_ai.cli.create_substrate_runtime", return_value=runtime):
            status = CliRunner().invoke(app, ["status", run.id, "--format", "json"])
        assert status.exit_code == 1
        assert json.loads(status.stdout)["run"]["cleanup"]["status"] == "unknown"
    finally:
        harness.doCleanups()


def test_experiment_repeated_stop_does_not_upgrade_unknown_cleanup():
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries

    owner = ExperimentRuntime(
        fake_plan(), OutcomeRuntime(UNKNOWN), ProviderRegistries()
    )
    owner.start()
    first = owner.stop()
    second = owner.stop()
    assert first.operation == second.operation == OperationOutcome.SUCCEEDED
    assert first.cleanup == second.cleanup == UNKNOWN
    assert first.state == second.state == "failed"
    assert not owner.writers_stopped


def test_legacy_boolean_failed_deploy_receipt_is_not_verification():
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries

    class BooleanReceipt(FakeSubstrateRuntime):
        deployment_cleanup_verified = True

        def deploy(self, plan):
            raise RuntimeOperationError("deploy failed", code="runtime.deploy.failed")

    owner = ExperimentRuntime(fake_plan(), BooleanReceipt(), ProviderRegistries())
    with pytest.raises(RuntimeOperationError):
        owner.start()
    assert not owner.writers_stopped
    assert owner.cleanup.status == CleanupStatus.UNKNOWN


@pytest.mark.parametrize("effects", [MutationEffects.PARTIAL, MutationEffects.UNKNOWN])
def test_teardown_error_outcome_survives_report_and_saved_metadata(
    tmp_path, control_dir, effects
):
    class LostTeardown(FakeSubstrateRuntime):
        def teardown(self, run_id):
            raise RuntimeOperationError(
                "teardown reply lost",
                code="runtime.teardown.unknown",
                run_id=run_id,
                operation=OperationOutcome.UNKNOWN,
                effects=effects,
                cleanup=UNKNOWN,
            )

    runtime = LostTeardown(run_id_factory=lambda: "run-lost-teardown")
    with (
        patch("mininet_ai.cli._compile_or_exit", return_value=fake_plan()),
        patch(
            "mininet_ai.cli.reserve_run", return_value=("run-lost-teardown", runtime)
        ),
        patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                "experiment.yaml",
                "--format",
                "json",
                "--artifact-root",
                str(tmp_path / "runs"),
                "--control-dir",
                str(control_dir),
            ],
        )
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["operation"] == "succeeded"  # work, not teardown
    error = report["teardown_error"]
    assert error["operation"] == "unknown"
    assert error["effects"] == effects
    assert error["cleanup"] == UNKNOWN.model_dump(mode="json")
    saved = json.loads(
        (tmp_path / "runs" / "run-lost-teardown" / "run.json").read_text()
    )
    assert saved["teardown_error"] == error
    assert saved["outcome"] == "failed"


@pytest.mark.parametrize(
    "operation",
    [
        OperationOutcome.FAILED,
        OperationOutcome.REJECTED,
        OperationOutcome.UNKNOWN,
        OperationOutcome.ACCEPTED,
    ],
)
@pytest.mark.parametrize("output_format", ["json", "text"])
def test_terminal_status_does_not_hide_operation_failure_with_verified_cleanup(
    operation, output_format
):
    class TerminalOutcome(FakeSubstrateRuntime):
        def inspect(self, run_id):
            snapshot = super().inspect(run_id)
            return snapshot.model_copy(
                update={"run": snapshot.run.model_copy(update={"operation": operation})}
            )

    runtime = TerminalOutcome()
    run = runtime.deploy(fake_plan())
    runtime.teardown(run.id)
    with patch("mininet_ai.cli.create_substrate_runtime", return_value=runtime):
        result = CliRunner().invoke(app, ["status", run.id, "--format", output_format])
    assert result.exit_code == 1, result.output
    if output_format == "json":
        output = json.loads(result.stdout)
        assert output["run"]["operation"] == operation
        assert output["run"]["cleanup"]["status"] == "verified"
    else:
        assert f"Operation: {operation}" in result.stdout
        assert "Cleanup: verified" in result.stdout


def test_stop_retry_keeps_earlier_work_failure_and_history():
    from mininet_ai.agents import AgnoAgentFactory
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries

    runtime = OutcomeRuntime(UNKNOWN)
    factory = AgnoAgentFactory()
    owner = ExperimentRuntime(
        fake_plan(), runtime, ProviderRegistries(), agent_factory=factory
    )
    owner.start()
    try:
        with patch.object(
            factory, "close", side_effect=[RuntimeError("agent close failed"), None]
        ):
            first = owner.stop()
            runtime.cleanup_outcome = VERIFIED
            second = owner.stop()
        assert first.operation == second.operation == OperationOutcome.FAILED
        assert second.cleanup.verified
        assert second.state == "failed"
        assert any(
            issue.message == "agent close failed" for issue in second.previous_issues
        )
        assert owner.writers_stopped
    finally:
        factory.close()


@pytest.mark.parametrize("during_start", [True, False])
def test_combined_start_or_operation_failure_and_teardown_failure_preserves_both(
    tmp_path,
    control_dir,
    during_start,
):
    class LostTeardown(FakeSubstrateRuntime):
        def teardown(self, run_id):
            raise RuntimeOperationError(
                "teardown reply lost",
                code="runtime.teardown.unknown",
                run_id=run_id,
                operation=OperationOutcome.UNKNOWN,
                effects=MutationEffects.PARTIAL,
                cleanup=UNKNOWN,
            )

    runtime = LostTeardown(run_id_factory=lambda: "run-combined-error")
    with (
        patch("mininet_ai.cli._compile_or_exit", return_value=fake_plan()),
        patch(
            "mininet_ai.cli.reserve_run", return_value=("run-combined-error", runtime)
        ),
        patch(
            "mininet_ai.experiment.TelemetryPipeline.start",
            side_effect=RuntimeError("startup failed"),
        )
        if during_start
        else patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                "experiment.yaml",
                "--format",
                "json",
                "--artifact-root",
                str(tmp_path / "runs"),
                "--control-dir",
                str(control_dir),
                *([] if during_start else ["--intent", "nonexistent=inspect"]),
            ],
        )
    assert result.exit_code == 1, result.output
    output = json.loads(result.stdout)
    assert output["operation"] == "failed"
    assert output["issue"]["code"] == (
        "experiment.start.failed" if during_start else "experiment.intent.invalid-agent"
    )
    assert output["teardown_error"]["operation"] == "unknown"
    assert output["teardown_error"]["effects"] == "partial"
    saved = json.loads(
        (tmp_path / "runs" / "run-combined-error" / "run.json").read_text()
    )
    assert saved["teardown_error"] == output["teardown_error"]
    assert saved["outcome"] == "failed"
    assert not saved["finalized"]
