"""Keep bounded CLI behavior alongside results/storage and inspection features."""

import json
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from typer.testing import CliRunner

from mininet_ai import cli
from mininet_ai.experiment import ExperimentRuntime
from mininet_ai.substrates import FakeSubstrateRuntime


def test_saved_bounded_plan_keeps_results_without_constructing_agno(
    tmp_path, monkeypatch
):
    source = Path(__file__).parents[1] / "examples/diamond-recovery/experiment.yaml"
    saved = tmp_path / "plan.json"
    runner = CliRunner()
    planned = runner.invoke(cli.app, ["plan", str(source), "--output", str(saved)])
    assert planned.exit_code == 0, planned.output
    digest = json.loads(saved.read_text())["digest"]
    posts = []

    class NativeTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            assert url.endswith("/v1/systemone")
            posts.append(payload)
            return {
                "answers": {"next_action": {"type": "choice", "choice": "healthy"}},
                "usage": {"output_tokens": 0},
            }

    def forbidden_agno(*args, **kwargs):
        raise AssertionError("bounded CLI must not construct Agno")

    def forbidden_compile(*args, **kwargs):
        raise AssertionError("saved-plan execution must not reread source")

    substrate = FakeSubstrateRuntime(run_id_factory=lambda: "run-bounded-cli")
    monkeypatch.setattr(cli, "AgnoAgentFactory", forbidden_agno)
    monkeypatch.setattr(cli, "compile_experiment", forbidden_compile)
    monkeypatch.setattr(cli, "reserve_run", lambda name: ("run-bounded-cli", substrate))
    monkeypatch.setattr(cli._SignalLatch, "wait", lambda self: None)
    monkeypatch.setattr(
        cli,
        "ExperimentRuntime",
        partial(ExperimentRuntime, decision_transport=NativeTransport()),
    )
    with TemporaryDirectory(prefix="bc-", dir="/tmp/opencode") as control:
        result = runner.invoke(
            cli.app,
            [
                "run",
                str(saved),
                "--format",
                "json",
                "--results-dir",
                str(tmp_path / "runs"),
                "--control-dir",
                control,
            ],
        )
    assert result.exit_code == 0, result.output
    assert posts, "native interface readiness must be exercised"
    report = json.loads(result.stdout)
    assert report["run"]["plan_digest"] == digest
    assert report["operation"] == "succeeded"
    assert report["cleanup"]["status"] == "verified"
    assert "Recorded run ID: run-bounded-cli" in result.stderr
    assert "Recorded run ID:" not in result.stdout
    directory = tmp_path / "runs/run-bounded-cli"
    assert (directory / "summary.md").is_file()
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["outcomes"]["operation"] == "succeeded"
    assert (directory / "evidence/ledger.json").is_file()
    assert json.loads(saved.read_text())["digest"] == digest
