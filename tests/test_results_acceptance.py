"""Cross-feature acceptance through the owning CLI and its saved results."""

import hashlib
import json

import pytest
from typer.testing import CliRunner

from mininet_ai.cli import app
from tests.agents.test_runtime import configured_plan
from tests.storage_fixtures import storage_args
from tests.test_inspection import inspect_run


def test_run_help_explains_inspection_and_independent_output_controls():
    result = CliRunner().invoke(app, ["run", "--help"], terminal_width=160)
    assert result.exit_code == 0
    for fact in (
        "summary.md",
        "summary.json",
        "evidence/ledger.json",
        "stderr",
        "stdout",
    ):
        assert fact in result.stdout
    assert "independent" in result.stdout


@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize("verbose", [False, True])
def test_output_modes_preserve_external_evidence_and_inspection_journey(
    tmp_path, output_format, verbose
):
    response = {
        "message": "Reasoning is not verified recovery",
        "proposals": [
            {
                "id": "p1",
                "capability": "openflow.flow.install",
                "target": "s1",
                "arguments": {"match": "ip", "actions": "normal"},
            }
        ],
    }
    external = tmp_path / "external"
    result, directory = inspect_run(
        tmp_path,
        plan=configured_plan(response),
        extra_args=(
            "--format",
            output_format,
            *(["--verbose"] if verbose else []),
            "--intent",
            "switch-router@s1=inspect forwarding",
            *storage_args(
                tmp_path,
                log_file=external / "diagnostics.log",
                ledger_db=external / "ledger.sqlite3",
            ),
        ),
    )
    assert result.exit_code == 0, result.output
    if output_format == "json":
        assert isinstance(json.loads(result.stdout), dict)
    assert "Recorded run ID:" not in result.stdout
    assert str(directory) in result.stderr
    assert "\x1b" not in result.stderr
    assert (response["message"] in result.stderr) is verbose
    summary = json.loads((directory / "summary.json").read_text())
    human = (directory / "summary.md").read_text()
    # The embedded machine facts are the same projection, not a second summary.
    assert json.loads(human.split("```json\n", 1)[1].split("```", 1)[0]) == summary
    assert summary["outcomes"]["operation"] == "succeeded"
    assert summary["outcomes"]["cleanup"]["status"] == "verified"
    assert summary["outcomes"]["experiment_success"]["value"] is None
    assert summary["counts"]["invocations"]["succeeded"]["value"] == 1
    assert summary["counts"]["actions"]["succeeded"]["value"] == 1
    assert summary["counts"]["actions"]["total"]["value"] is None
    assert summary["sources"]["log"] == str(external / "diagnostics.log")
    assert summary["sources"]["ledger"] == str(external / "ledger.sqlite3")
    assert response["message"] in (external / "diagnostics.log").read_text()
    ledger = json.loads((directory / "evidence/ledger.json").read_text())
    responses = [
        record["data"]["data"]["response"]
        for record in ledger["records"]
        if record["type"] == "agent.invocation.completed"
    ]
    assert len(responses) == 1
    assert responses[0]["message"] == response["message"]
    assert (
        responses[0]["proposals"][0]["arguments"]
        == response["proposals"][0]["arguments"]
    )


def test_export_of_old_saved_run_does_not_backfill_views_or_mutate_sources(tmp_path):
    result, directory = inspect_run(tmp_path)
    assert result.exit_code == 0, result.output
    for name in ("summary.md", "summary.json", "evidence/ledger.json"):
        (directory / name).unlink()
    sources = [path for path in directory.rglob("*") if path.is_file()]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    destination = tmp_path / "bundle"
    exported = CliRunner().invoke(
        app,
        [
            "export",
            "inspection-run",
            "--artifact-root",
            str(tmp_path / "runs"),
            "--destination",
            str(destination),
            "--acknowledge-sensitive-data",
        ],
    )
    assert exported.exit_code == 0, exported.output
    assert (destination / "EXPORT_COMPLETE").is_file()
    for name in ("summary.md", "summary.json", "evidence/ledger.json"):
        assert not (directory / name).exists()
        assert not (destination / name).exists()
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path, digest in before.items()
    )
