from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mininet_ai.artifacts import reserve_artifacts
from mininet_ai.cli import app
from mininet_ai.errors import MininetAIError
from tests.agents.test_runtime import configured_plan


def test_distinct_runs_and_collision(tmp_path):
    first = reserve_artifacts(tmp_path / "output", "run-first")
    second = reserve_artifacts(tmp_path / "output", "run-second")
    assert first.directory != second.directory
    assert first.log == first.directory / "logs/run.log"
    assert first.ledger == first.directory / "dbs/ledger.sqlite3"
    with pytest.raises(MininetAIError, match="reserve"):
        reserve_artifacts(tmp_path / "output", "run-first")


def test_arbitrary_ids_cannot_escape_or_collide(tmp_path):
    first = reserve_artifacts(tmp_path / "output", "../escape")
    second = reserve_artifacts(tmp_path / "output", first.directory.name)
    assert first.directory.parent == tmp_path / "output"
    assert first.directory != second.directory


def test_stable_memory_is_exception_and_legacy_untouched(tmp_path):
    root = tmp_path / "output"
    root.mkdir(mode=0o700)
    legacy = root / "agno.sqlite3"
    legacy.write_bytes(b"legacy")
    first = reserve_artifacts(root, "run-first", persistent_memory=True)
    second = reserve_artifacts(root, "run-second", persistent_memory=True)
    assert first.agno == second.agno == root / "memory/agno.sqlite3"
    assert first.shared_state != second.shared_state
    assert legacy.read_bytes() == b"legacy"


def test_agno_learned_memory_survives_new_run_store(tmp_path):
    from agno.db.schemas.memory import UserMemory

    from mininet_ai.agents.agno.storage import create_agno_database

    first = reserve_artifacts(tmp_path / "output", "run-first", persistent_memory=True)
    database = create_agno_database(first.agno)
    database.upsert_user_memory(
        UserMemory(memory="Remember routing preference", user_id="router@s1")
    )
    second = reserve_artifacts(
        tmp_path / "output", "run-second", persistent_memory=True
    )
    reopened = create_agno_database(second.agno)
    memories = reopened.get_user_memories(user_id="router@s1")
    assert len(memories) == 1
    isolated = reserve_artifacts(tmp_path / "output", "run-third")
    assert (
        create_agno_database(isolated.agno).get_user_memories(user_id="router@s1") == []
    )


def test_dry_run_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch(
        "mininet_ai.cli._compile_or_exit",
        return_value=configured_plan({"message": "handled"}),
    ):
        result = CliRunner().invoke(app, ["run", "experiment.yaml", "--dry-run"])
    assert result.exit_code == 0
    assert list(tmp_path.iterdir()) == []


def test_legacy_agent_memory_requires_explicit_selection(tmp_path):
    from mininet_ai.specification.models import (
        LearnedMemoryConfiguration,
        MemoryConfiguration,
    )

    root = tmp_path / "output"
    root.mkdir(mode=0o700)
    legacy = root / "agno.sqlite3"
    legacy.write_bytes(b"old learned memory")
    plan = configured_plan({"message": "handled"})
    plan = plan.model_copy(
        update={
            "agents": tuple(
                agent.model_copy(
                    update={
                        "memory": MemoryConfiguration(
                            learned=LearnedMemoryConfiguration(scope="agent")
                        )
                    }
                )
                for agent in plan.agents
            )
        }
    )
    with patch("mininet_ai.cli._compile_or_exit", return_value=plan):
        result = CliRunner().invoke(
            app, ["run", "experiment.yaml", "--artifact-root", str(root)]
        )
    assert result.exit_code != 0
    assert "--agno-db" in result.output
    assert legacy.read_bytes() == b"old learned memory"
    assert list(root.iterdir()) == [legacy]


def test_keyboard_interrupt_during_service_start_cleans_network(tmp_path):
    from mininet_ai.substrates import FakeSubstrateRuntime, RunState

    runtime = FakeSubstrateRuntime(run_id_factory=lambda: "run-interrupted")
    with (
        patch("mininet_ai.cli.reserve_run", return_value=("run-interrupted", runtime)),
        patch(
            "mininet_ai.cli._compile_or_exit",
            return_value=configured_plan({"message": "handled"}),
        ),
        patch(
            "mininet_ai.experiment.TelemetryPipeline.start",
            side_effect=KeyboardInterrupt,
        ),
    ):
        result = CliRunner().invoke(
            app, ["run", "experiment.yaml", "--artifact-root", str(tmp_path / "output")]
        )
    assert result.exit_code != 0
    assert runtime.inspect("run-interrupted").run.state == RunState.STOPPED
    assert "failed" in (tmp_path / "output/run-interrupted/logs/run.log").read_text()


def test_unreserved_registered_adapter_can_use_explicit_paths(tmp_path):
    from mininet_ai.substrates import FakeSubstrateRuntime, RunState

    runtime = FakeSubstrateRuntime(run_id_factory=lambda: "custom-run")
    arguments = ["run", "experiment.yaml", "--control-dir", str(tmp_path / "control")]
    for option in ("log-file", "ledger-db", "shared-state-db", "agno-db"):
        arguments.extend([f"--{option}", str(tmp_path / option)])
    with (
        patch("mininet_ai.cli.reserve_run", return_value=(None, runtime)),
        patch(
            "mininet_ai.cli._compile_or_exit",
            return_value=configured_plan({"message": "handled"}),
        ),
        patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
    ):
        result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert runtime.inspect("custom-run").run.state == RunState.STOPPED


def test_two_cli_runs_have_separate_saved_output(tmp_path):
    root = tmp_path / "output"
    with (
        patch(
            "mininet_ai.cli._compile_or_exit",
            return_value=configured_plan({"message": "handled"}),
        ),
        patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
    ):
        for _ in range(2):
            result = CliRunner().invoke(
                app,
                [
                    "run",
                    "experiment.yaml",
                    "--artifact-root",
                    str(root),
                    "--control-dir",
                    str(tmp_path / "control"),
                ],
            )
            assert result.exit_code == 0, result.output
    directories = list(root.iterdir())
    assert len(directories) == 2
    for directory in directories:
        assert (directory / "logs/run.log").is_file()
        assert (directory / "dbs/ledger.sqlite3").is_file()
        assert (directory / "dbs/shared-state.sqlite3").is_file()
        assert (directory / "dbs/agno.sqlite3").is_file()


def test_start_failure_retains_diagnostics_and_cleans_network(tmp_path):
    from mininet_ai.substrates import FakeSubstrateRuntime, RunState

    runtime = FakeSubstrateRuntime(run_id_factory=lambda: "run-failed")
    with (
        patch("mininet_ai.cli.reserve_run", return_value=("run-failed", runtime)),
        patch(
            "mininet_ai.cli._compile_or_exit",
            return_value=configured_plan({"message": "handled"}),
        ),
        patch(
            "mininet_ai.runtime.control.socket.socket.bind",
            side_effect=OSError("bind failed"),
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                "experiment.yaml",
                "--artifact-root",
                str(tmp_path / "output"),
                "--control-dir",
                str(tmp_path / "control"),
            ],
        )
    assert result.exit_code != 0
    assert "failed" in (tmp_path / "output/run-failed/logs/run.log").read_text()
    assert runtime.inspect("run-failed").run.state == RunState.STOPPED
    assert list((tmp_path / "control").iterdir()) == []
