import errno
import json
import multiprocessing
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from mininet_ai.artifacts import default_artifact_root
from mininet_ai.runtime import SQLiteRunLedger, SQLiteSharedStateStore


def test_storage_defaults_follow_effective_user_not_project(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("os.geteuid", lambda: 0)
    assert default_artifact_root() == Path("/var/lib/mininet-ai")
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    assert default_artifact_root() == tmp_path / "state/mininet-ai"
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    assert default_artifact_root() == tmp_path / "home/.local/state/mininet-ai"


def _hold_source(path, ready, release):
    from mininet_ai.saved_runs import SourceLocks

    with SourceLocks([Path(path)], writer=True):
        ready.set()
        release.wait(5)


def test_export_lock_refuses_writer_and_deduplicates_hardlinks(tmp_path):
    from mininet_ai.saved_runs import SourceLocks

    source = tmp_path / "evidence"
    source.write_text("private")
    source.chmod(0o600)
    alias = tmp_path / "alias"
    os.link(source, alias)
    context = multiprocessing.get_context("fork")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_source, args=(source, ready, release))
    process.start()
    try:
        assert ready.wait(5)
        with (
            pytest.raises(OSError, match="writer|busy"),
            SourceLocks([alias], writer=False),
        ):
            pass
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 0
    with SourceLocks([source, alias], writer=False):
        assert source.read_text() == "private"


@pytest.mark.parametrize("store_type", [SQLiteRunLedger, SQLiteSharedStateStore])
def test_standalone_store_participates_until_closed(tmp_path, store_type):
    from mininet_ai.saved_runs import SourceLocks

    path = tmp_path / "ledger.sqlite3"
    ledger = store_type(path)
    try:
        with pytest.raises(OSError, match="busy"), SourceLocks([path], writer=False):
            pass
    finally:
        ledger.close()
    with SourceLocks([path], writer=False):
        pass


def run_fake(tmp_path, extra_args=(), expected_returncode=0):
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    specification = tmp_path / "experiment.yaml"
    specification.write_text(
        "apiVersion: mininet-ai/v1alpha3\nkind: Experiment\n"
        "metadata: {name: saved-run}\nsubstrate:\n  driver: fake\n"
        "  topology:\n    resources: [{name: s1, kind: switch}]\n"
    )
    root = tmp_path / "runs"
    control_context = TemporaryDirectory(prefix="mn-")
    control = Path(control_context.name)
    process = subprocess.Popen(
        [
            str(Path(sys.executable).parent / "mininet-ai"),
            "run",
            str(specification),
            "--artifact-root",
            str(root),
            "--control-dir",
            str(control),
            *extra_args,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not list(control.glob("*/control.sock")) and process.poll() is None:
            if time.monotonic() > deadline:
                raise AssertionError("fake run did not start")
            time.sleep(0.02)
        if process.poll() is None and expected_returncode == 0:
            process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == expected_returncode, stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        control_context.cleanup()
    return root, next(root.glob("run-*"))


def test_cli_records_sources_and_finalizes_after_stop(tmp_path):
    _, directory = run_fake(tmp_path)
    metadata = json.loads((directory / "run.json").read_text())
    assert metadata["schema_version"] == 1
    assert metadata["finalized"] is True
    assert metadata["outcome"] == "succeeded"
    assert metadata["sources"]["ledger"] == str(directory / "dbs/ledger.sqlite3")
    assert (directory / "run.json").stat().st_mode & 0o777 == 0o600


def test_agno_factory_locks_even_when_aliasing_an_included_source(tmp_path):
    from mininet_ai.agents import AgnoAgentFactory
    from mininet_ai.saved_runs import SourceLocks

    path = tmp_path / "sessions.sqlite3"
    factory = AgnoAgentFactory(database_path=path)
    try:
        with pytest.raises(OSError, match="busy"), SourceLocks([path], writer=False):
            pass
    finally:
        factory.close()
    with SourceLocks([path], writer=False):
        pass


def test_managed_export_produces_host_readable_evidence_not_databases(tmp_path):
    from typer.testing import CliRunner

    from mininet_ai.cli import app

    root, directory = run_fake(tmp_path)
    destination = tmp_path / "results"
    result = CliRunner().invoke(
        app,
        [
            "export",
            directory.name,
            "--artifact-root",
            str(root),
            "--destination",
            str(destination),
            "--acknowledge-sensitive-data",
        ],
    )
    assert result.exit_code == 0, result.output
    manifest = json.loads((destination / "manifest.json").read_text())
    ledger = json.loads((destination / "ledger.json").read_text())
    assert manifest["provenance"] == "managed"
    assert manifest["evidence_complete"] is True
    assert ledger["manifest"]["runId"] == directory.name
    assert [r["sequence"] for r in ledger["records"]] == list(
        range(1, len(ledger["records"]) + 1)
    )
    assert (destination / "EXPORT_COMPLETE").is_file()
    assert not list(destination.rglob("*.sqlite3"))
    assert (destination / "run.log").stat().st_mode & 0o777 == 0o644


def test_offline_export_records_operator_provenance_and_missing_evidence(tmp_path):
    from typer.testing import CliRunner

    from mininet_ai.cli import app

    source = tmp_path / "snapshot.log"
    source.write_text("failed startup diagnostics\n")
    source.chmod(0o600)
    destination = tmp_path / "results"
    args = [
        "export-offline",
        "legacy-run",
        "--log-file",
        str(source),
        "--destination",
        str(destination),
        "--acknowledge-sensitive-data",
    ]
    runner = CliRunner()
    refusal = runner.invoke(app, args)
    assert refusal.exit_code != 0
    assert not destination.exists()
    result = runner.invoke(app, [*args, "--acknowledge-offline-consistency"])
    assert result.exit_code == 0, result.output
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["provenance"] == "operator-supplied-offline"
    assert manifest["outcome"] == "unknown"
    assert manifest["missing_sources"] == ["artifacts", "ledger", "shared_state"]
    assert manifest["evidence_complete"] is False
    assert "incomplete" in result.output
    assert (destination / "EXPORT_COMPLETE").exists()
    assert source.read_text() == "failed startup diagnostics\n"


def test_export_refuses_a_locked_artifact_file(tmp_path):
    from typer.testing import CliRunner

    from mininet_ai.cli import app
    from mininet_ai.saved_runs import SourceLocks

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    source = artifacts / "measurement.txt"
    source.write_text("changing evidence")
    source.chmod(0o600)
    destination = tmp_path / "results"
    with SourceLocks([source], writer=True):
        result = CliRunner().invoke(
            app,
            [
                "export-offline",
                "test",
                "--artifacts-dir",
                str(artifacts),
                "--destination",
                str(destination),
                "--acknowledge-sensitive-data",
                "--acknowledge-offline-consistency",
            ],
        )
    assert result.exit_code != 0
    assert "busy" in result.output
    assert not (destination / "EXPORT_COMPLETE").exists()


def test_artifact_export_does_not_copy_a_database_disguised_as_a_log(tmp_path):
    from typer.testing import CliRunner

    from mininet_ai.cli import app

    path = tmp_path / "db.sqlite3"
    SQLiteRunLedger(path).close()
    destination = tmp_path / "results"
    result = CliRunner().invoke(
        app,
        [
            "export-offline",
            "test",
            "--log-file",
            str(path),
            "--destination",
            str(destination),
            "--acknowledge-sensitive-data",
            "--acknowledge-offline-consistency",
        ],
    )
    assert result.exit_code != 0
    assert "SQLite" in result.output
    assert not (destination / "EXPORT_COMPLETE").exists()


def test_managed_export_filters_an_explicit_log_shared_across_runs(tmp_path):
    from typer.testing import CliRunner

    from mininet_ai.cli import app

    log = tmp_path / "shared.log"
    _, first = run_fake(tmp_path / "first", ["--log-file", str(log)])
    root, second = run_fake(tmp_path / "second", ["--log-file", str(log)])
    destination = tmp_path / "results"
    result = CliRunner().invoke(
        app,
        [
            "export",
            second.name,
            "--artifact-root",
            str(root),
            "--destination",
            str(destination),
            "--acknowledge-sensitive-data",
        ],
    )
    assert result.exit_code == 0, result.output
    exported = (destination / "run.log").read_text()
    assert first.name not in exported
    assert second.name in exported


def test_shared_export_handles_unsupported_directory_fsync(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from mininet_ai.cli import app

    source = tmp_path / "run.log"
    source.write_text("diagnostics")
    source.chmod(0o600)
    original = os.fsync

    def shared_mount_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "directory fsync unsupported")
        original(fd)

    monkeypatch.setattr(os, "fsync", shared_mount_fsync)
    destination = tmp_path / "result"
    result = CliRunner().invoke(
        app,
        [
            "export-offline",
            "run-test",
            "--log-file",
            str(source),
            "--destination",
            str(destination),
            "--acknowledge-sensitive-data",
            "--acknowledge-offline-consistency",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (destination / "EXPORT_COMPLETE").exists()


def test_offline_bundle_filters_other_runs_and_checks_inventory(tmp_path):
    import hashlib
    from datetime import UTC, datetime

    from typer.testing import CliRunner

    from mininet_ai.cli import app
    from mininet_ai.compiler import compile_experiment
    from mininet_ai.runtime import RunManifest
    from mininet_ai.runtime.state import SharedStateAccess, SharedStateUpdate
    from tests.specification_fixtures import COMPILER_MULTILAYER_SPECIFICATION

    ledger_path, state_path = tmp_path / "ledger.sqlite3", tmp_path / "state.sqlite3"
    ledger = SQLiteRunLedger(ledger_path)
    state = SQLiteSharedStateStore(state_path)
    try:
        for run_id in ("run-chosen", "run-unrelated"):
            ledger.create_run(
                RunManifest.from_plan(
                    run_id,
                    compile_experiment(COMPILER_MULTILAYER_SPECIFICATION),
                    created_at=datetime.now(UTC),
                )
            )
            state.apply(
                SharedStateAccess(
                    run_id=run_id,
                    deployment="lab",
                    agent_id="observer",
                    limits={"run": 1},
                ),
                (
                    SharedStateUpdate(
                        scope="run", operation="set", key="result", value=run_id
                    ),
                ),
            )
    finally:
        ledger.close()
        state.close()
    destination = tmp_path / "result"
    result = CliRunner().invoke(
        app,
        [
            "export-offline",
            "run-chosen",
            "--ledger-db",
            str(ledger_path),
            "--shared-state-db",
            str(state_path),
            "--destination",
            str(destination),
            "--acknowledge-sensitive-data",
            "--acknowledge-offline-consistency",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        json.loads((destination / "ledger.json").read_text())["manifest"]["runId"]
        == "run-chosen"
    )
    entries = json.loads((destination / "shared-state.json").read_text())["entries"]
    assert [(entry["namespace"], entry["value"]) for entry in entries] == [
        ("run:run-chosen", "run-chosen")
    ]
    manifest = json.loads((destination / "manifest.json").read_text())
    for path, checksum in manifest["sha256"].items():
        assert hashlib.sha256((destination / path).read_bytes()).hexdigest() == checksum


@pytest.mark.parametrize(
    "case", ["public", "symlink", "missing", "corrupt", "destination-exists", "overlap"]
)
def test_export_safety_failures_preserve_sources_and_never_mark_complete(
    tmp_path, case
):
    from typer.testing import CliRunner

    from mininet_ai.cli import app

    source = tmp_path / "evidence"
    source.write_text("private diagnostics")
    source.chmod(0o600)
    selected = source
    destination = tmp_path / "result"
    option = "--log-file"
    if case == "public":
        source.chmod(0o644)
    elif case == "symlink":
        selected = tmp_path / "alias"
        selected.symlink_to(source)
    elif case == "missing":
        selected = tmp_path / "absent"
    elif case == "corrupt":
        option = "--ledger-db"
    elif case == "destination-exists":
        destination.mkdir()
        (destination / "keep").write_text("existing result")
    elif case == "overlap":
        selected = tmp_path / "artifacts"
        selected.mkdir(mode=0o700)
        destination = selected / "result"
        option = "--artifacts-dir"
    result = CliRunner().invoke(
        app,
        [
            "export-offline",
            "test",
            option,
            str(selected),
            "--destination",
            str(destination),
            "--acknowledge-sensitive-data",
            "--acknowledge-offline-consistency",
        ],
    )
    assert result.exit_code != 0
    assert not (destination / "EXPORT_COMPLETE").exists()
    assert source.read_text() == "private diagnostics"
    if case == "destination-exists":
        assert (destination / "keep").read_text() == "existing result"


def test_failed_operation_can_finalize_without_claiming_experiment_success(tmp_path):
    _, directory = run_fake(
        tmp_path, ["--intent", "missing=inspect"], expected_returncode=1
    )
    metadata = json.loads((directory / "run.json").read_text())
    assert metadata["finalized"] is True
    assert metadata["outcome"] == "failed"


def test_late_coordination_failure_never_certifies_a_bundle(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from mininet_ai.cli import app
    from mininet_ai.saved_runs import SourceLocks

    source = tmp_path / "run.log"
    source.write_text("diagnostics")
    source.chmod(0o600)
    original_exit = SourceLocks.__exit__

    def fail_on_release(self, exc_type, exc, tb):
        original_exit(self, exc_type, exc, tb)
        if exc_type is None:
            raise OSError("coordination failed during release")

    monkeypatch.setattr(SourceLocks, "__exit__", fail_on_release)
    destination = tmp_path / "result"
    result = CliRunner().invoke(
        app,
        [
            "export-offline",
            "run-test",
            "--log-file",
            str(source),
            "--destination",
            str(destination),
            "--acknowledge-sensitive-data",
            "--acknowledge-offline-consistency",
        ],
    )
    assert result.exit_code != 0
    assert "coordination failed" in result.output
    assert not (destination / "EXPORT_COMPLETE").exists()


def test_export_marker_is_host_readable_under_private_umask(tmp_path):
    from typer.testing import CliRunner

    from mininet_ai.cli import app

    source = tmp_path / "run.log"
    source.write_text("diagnostics")
    source.chmod(0o600)
    destination = tmp_path / "result"
    old = os.umask(0o077)
    try:
        result = CliRunner().invoke(
            app,
            [
                "export-offline",
                "run-test",
                "--log-file",
                str(source),
                "--destination",
                str(destination),
                "--acknowledge-sensitive-data",
                "--acknowledge-offline-consistency",
            ],
        )
    finally:
        os.umask(old)
    assert result.exit_code == 0, result.output
    assert (destination / "EXPORT_COMPLETE").stat().st_mode & 0o777 == 0o644


def test_verified_start_failure_reports_stopped_writers(tmp_path):
    from mininet_ai.agents import AgnoAgentFactory
    from mininet_ai.errors import MininetAIError
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.saved_runs import SourceLocks
    from mininet_ai.substrates import FakeSubstrateRuntime
    from tests.test_cli_runtime import fake_plan

    class FailedDeployment(FakeSubstrateRuntime):
        deployment_cleanup_verified = True

        def deploy(self, plan):
            raise MininetAIError("deployment failed before any workers started")

    database = tmp_path / "agno.sqlite3"
    owner = ExperimentRuntime(
        fake_plan(),
        FailedDeployment(),
        ProviderRegistries(),
        agent_factory=AgnoAgentFactory(database_path=database),
    )
    with pytest.raises(MininetAIError, match="deployment failed"):
        owner.start()
    assert owner.writers_stopped is True
    with SourceLocks([database], writer=False):
        pass


def test_cleaned_start_failure_exports_explicitly_incomplete_evidence(
    tmp_path, monkeypatch
):
    from typer.testing import CliRunner

    from mininet_ai.cli import app
    from mininet_ai.errors import MininetAIError
    from mininet_ai.substrates import FakeSubstrateRuntime

    class FailedDeployment(FakeSubstrateRuntime):
        deployment_cleanup_verified = True

        def deploy(self, plan):
            raise MininetAIError("deployment failed before any workers started")

    specification = tmp_path / "experiment.yaml"
    specification.write_text(
        "apiVersion: mininet-ai/v1alpha3\nkind: Experiment\nmetadata: {name: failed-start}\n"
        "substrate: {driver: fake, topology: {resources: [{name: s1, kind: switch}]}}\n"
    )
    monkeypatch.setattr(
        "mininet_ai.cli.reserve_run", lambda name: ("run-failed", FailedDeployment())
    )
    runner = CliRunner()
    root = tmp_path / "runs"
    failure = runner.invoke(
        app, ["run", str(specification), "--artifact-root", str(root)]
    )
    assert failure.exit_code != 0
    metadata = json.loads((root / "run-failed/run.json").read_text())
    assert metadata["finalized"] is True
    assert metadata["outcome"] == "failed"
    destination = tmp_path / "result"
    exported = runner.invoke(
        app,
        [
            "export",
            "run-failed",
            "--artifact-root",
            str(root),
            "--destination",
            str(destination),
            "--acknowledge-sensitive-data",
        ],
    )
    assert exported.exit_code == 0, exported.output
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["outcome"] == "failed"
    assert manifest["missing_sources"] == ["ledger_run"]
    assert not manifest["evidence_complete"]
    assert (destination / "EXPORT_COMPLETE").exists()


def test_mininet_failed_rollback_never_certifies_stopped_writers():
    import unittest

    from mininet_ai.errors import MininetAIError
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from tests.substrates.test_mininet_ovs_runtime import (
        UnhealthyRecordingNetwork,
        mininet_plan,
        recording_runtime,
    )

    class BrokenRollback(UnhealthyRecordingNetwork):
        def stop(self):
            raise RuntimeError("rollback did not stop processes")

    cleanup = unittest.TestCase()
    try:
        substrate = recording_runtime(cleanup, BrokenRollback)
        owner = ExperimentRuntime(mininet_plan(), substrate, ProviderRegistries())
        with pytest.raises(MininetAIError, match="rollback also failed"):
            owner.start()
        assert owner.writers_stopped is False
    finally:
        cleanup.doCleanups()


def test_agno_checks_database_before_runtime_can_start(tmp_path):
    from mininet_ai.agents import AgnoAgentFactory
    from mininet_ai.sdk import AgentProviderError

    path = tmp_path / "agno.sqlite3"
    path.write_bytes(b"not a SQLite database")
    path.chmod(0o600)
    with pytest.raises(AgentProviderError, match="database"):
        AgnoAgentFactory(database_path=path)
    assert path.read_bytes() == b"not a SQLite database"
