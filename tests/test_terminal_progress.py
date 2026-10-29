"""Terminal acceptance through the CLI, using deterministic local invocations."""

import json
import logging
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mininet_ai.agents.agno.models import DeterministicAgnoModel
from mininet_ai.cli import app
from mininet_ai.dependency_logging import RunDiagnostics, invocation_diagnostics
from mininet_ai.substrates import FakeSubstrateRuntime
from tests import test_cli_runtime as cli_tests
from tests.agents.test_runtime import configured_plan


def run_with_diagnostics(
    tmp_path, emit, *, verbose=False, output_format="json", native_diagnostics=False
):
    runtime = FakeSubstrateRuntime(run_id_factory=lambda: "terminal-run")
    original = DeterministicAgnoModel.invoke

    def invoke(model, *args, **kwargs):
        emit()
        return original(model, *args, **kwargs)

    with (
        TemporaryDirectory(prefix="tp-", dir="/tmp/opencode") as control,
        patch(
            "mininet_ai.cli._compile_or_exit",
            return_value=configured_plan(
                {"message": "Assessment only; no verified recovery"}
            ),
        ),
        patch("mininet_ai.cli.reserve_run", return_value=("terminal-run", runtime)),
        patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
        patch.object(DeterministicAgnoModel, "invoke", invoke),
        patch(
            "mininet_ai.cli.RunDiagnostics",
            side_effect=lambda run_id, sink, **kwargs: RunDiagnostics(
                run_id, sink, mininet=native_diagnostics
            ),
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                "experiment.yaml",
                "--format",
                output_format,
                "--intent",
                "switch-router@s1=inspect",
                *(["--verbose"] if verbose else []),
                *cli_tests.RuntimeCLITests.run_databases(str(tmp_path))[:-1],
                control,
            ],
        )
    assert result.exit_code == 0, result.output
    return result, (tmp_path / "run.log").read_text()


def test_warning_storm_groups_across_invocations_on_clock_and_flushes(tmp_path):
    from agno.utils.log import log_error, log_warning

    now = 0.0

    def emit():
        nonlocal now
        for instant, invocation in [
            (0, "one"),
            (29, "two"),
            (30, "three"),
            (31, "four"),
        ]:
            now = instant
            with invocation_diagnostics("terminal-run", "router", invocation):
                log_warning("PRIVATE provider payload")
                if instant == 29:
                    log_error("PRIVATE error payload")
                    log_error("PRIVATE error payload")
                    with invocation_diagnostics("terminal-run", "other-router", "two"):
                        log_warning("PRIVATE changed agent")

    with patch("mininet_ai.cli.monotonic", side_effect=lambda: now, create=True):
        result, log = run_with_diagnostics(tmp_path, emit)

    warnings = [
        line for line in result.stderr.splitlines() if "WARN [Agent:router]" in line
    ]
    assert len(warnings) == 3  # first, 30-second total, shutdown remainder
    assert "invocation=one" in warnings[0]
    assert "repeated 2" in warnings[1] and "3 occurrences total" in warnings[1]
    assert "repeated 1" in warnings[2] and "4 occurrences total" in warnings[2]
    assert result.stderr.count("WARN [Agent:other-router]") == 1
    assert result.stderr.count("ERR  [Agent:router]") == 2
    assert result.stderr.index("ERR  [Agent:router]") < result.stderr.index(
        "repeated 2"
    )
    assert result.stderr.index("draining work") < result.stderr.index("repeated 1")
    assert log.count("Agno dependency warning") == 5
    assert log.count("Agno dependency error") == 2
    assert "repeated" not in log
    assert "PRIVATE" not in result.output + log
    assert json.loads(result.stdout)["state"] == "stopped"
    assert "\x1b" not in result.output + log
    assert all(re.match(r"\d{2}:\d{2}:\d{2} (WARN|ERR ) \[", line) for line in warnings)


@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_output_modes_keep_diagnostics_and_results_independent(
    tmp_path, verbose, output_format
):
    from agno.utils.log import log_warning

    def emit():
        for invocation in ("first", "second", "third"):
            with invocation_diagnostics("terminal-run", "router", invocation):
                log_warning("PRIVATE payload")

    result, log = run_with_diagnostics(
        tmp_path, emit, verbose=verbose, output_format=output_format
    )
    assert ("Assessment only" in result.stderr) is verbose
    assert ("agent.invocation.started" in result.stderr) is verbose
    assert ("Prepared experiment" in result.stderr) is verbose
    assert "Assessment only" in log
    assert "agent.invocation.started" in log
    assert log.count("Agno dependency warning") == 3
    assert result.stderr.count("WARN [Agent:router]") == (3 if verbose else 2)
    assert ("repeated 2" in result.stderr) is not verbose
    assert "draining work" in result.stderr
    assert "WARN" not in result.stdout
    assert "INFO" not in result.stdout
    assert "\x1b" not in result.stdout + result.stderr + log
    if output_format == "json":
        report = json.loads(result.stdout)
        assert report["operation"] == "succeeded"
        assert report["continuous"]["completed"] == 1
        assert report["continuous"]["failed"] == 0
    else:
        assert "Assessment only" not in result.stdout
        assert "operation: succeeded" in result.stdout


def test_changed_dependency_warnings_remain_immediate_and_retained(tmp_path):
    from agno.utils.log import log_warning
    from mininet import log, net

    def emit():
        with invocation_diagnostics("terminal-run", "router", "one"):
            for function in ("start", "start", "stop", "stop"):
                log.lg.handle(
                    logging.LogRecord(
                        "mininet",
                        logging.WARNING,
                        net.__file__,
                        1,
                        "PRIVATE native body",
                        (),
                        None,
                        function,
                    )
                )
            log_warning("PRIVATE provider body")

    result, saved = run_with_diagnostics(tmp_path, emit, native_diagnostics=True)
    lines = result.stderr.splitlines()
    stop = next(i for i, line in enumerate(lines) if "draining work" in line)
    before_stop = "\n".join(lines[:stop])
    assert before_stop.count("WARN [Mininet]") == 2
    assert "net.start" in before_stop and "net.stop" in before_stop
    assert before_stop.count("WARN [Agent:router]") == 1
    assert result.stderr.count("repeated 1") == 2
    assert saved.count("Mininet warning diagnostic") == 4
    assert "PRIVATE" not in result.output + saved
