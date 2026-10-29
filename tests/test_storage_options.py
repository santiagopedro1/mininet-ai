"""Storage selection acceptance at the owning CLI boundary."""

from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mininet_ai.cli import app


@pytest.fixture
def control_directory():
    # Keep Unix socket names below the platform limit regardless of pytest root.
    with TemporaryDirectory(prefix="mn-storage-") as directory:
        yield directory


@pytest.mark.parametrize(
    "contents",
    [
        "unknown: path",
        "agno_db: a\nagno_db: b",
        "[]",
        "null",
        "agno_db: null",
        "agno_db: 1",
        "agno_db: []",
        "agno_db: ''",
        "agno_db: '   '",
        "agno_db: [",
        "agno_db: true",
        "agno_db: {path: value}",
        "agno_db: !custom path",
        "agno_db: a\n---\nledger_db: b",
        "? [agno_db]\n: path",
        'agno_db: "\\0"',
    ],
)
def test_invalid_storage_config_is_rejected_before_compilation(tmp_path, contents):
    config = tmp_path / "storage.yaml"
    config.write_text(contents)
    with patch("mininet_ai.cli._compile_or_exit") as compile_plan:
        result = CliRunner().invoke(
            app, ["run", "missing.yaml", "--storage-config", str(config)]
        )
    assert result.exit_code == 2
    assert "Invalid storage configuration" in result.output
    compile_plan.assert_not_called()


@pytest.mark.parametrize(
    "flag",
    ["--artifact-root", "--log-file", "--ledger-db", "--shared-state-db", "--agno-db"],
)
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("equals", [False, True])
def test_removed_run_selectors_rejected_even_with_replacements(flag, mixed, equals):
    result = CliRunner().invoke(
        app,
        [
            "run",
            "missing.yaml",
            *(
                ["--results-dir", "results", "--storage-config", "storage.yaml"]
                if mixed
                else []
            ),
            *([f"{flag}=old"] if equals else [flag, "old"]),
        ],
    )
    assert result.exit_code == 2
    assert "removed" in result.output.lower()
    assert (
        "--results-dir" if flag == "--artifact-root" else "--storage-config"
    ) in result.output
    if flag != "--artifact-root":
        assert flag[2:].replace("-", "_") in result.output


@pytest.mark.parametrize("flag", ["--agno-db", "--shared-state-db"])
def test_removed_invoke_selectors_explain_owner_selection(flag):
    result = CliRunner().invoke(
        app, ["invoke", "run-id", "agent", "--intent", "hello", flag, "old"]
    )
    assert result.exit_code == 2
    assert "owning run" in result.output
    assert "--storage-config" in result.output


def test_help_groups_live_results_and_advanced_storage():
    result = CliRunner().invoke(app, ["run", "--help"], terminal_width=120)
    assert result.exit_code == 0
    assert "Results and output" in result.output
    assert "Advanced storage" in result.output
    for text in (
        "--results-dir",
        "--storage-config",
        "--format",
        "--verbose",
        "memory/",
        "sensitive",
        "VM shared filesystem",
        "not an exact",
        "log_file",
        "ledger_db",
        "shared_state_db",
        "agno_db",
    ):
        assert text in result.output
    for flag in (
        "--artifact-root",
        "--log-file",
        "--ledger-db",
        "--shared-state-db",
        "--agno-db",
    ):
        assert flag not in result.output


def test_storage_config_is_not_discovered(tmp_path, monkeypatch):
    from tests.test_cli_runtime import configured_plan

    (tmp_path / "storage.yaml").write_text("invalid: discovered")
    monkeypatch.chdir(tmp_path)
    with patch(
        "mininet_ai.cli._compile_or_exit",
        return_value=configured_plan({"message": "handled"}),
    ):
        result = CliRunner().invoke(app, ["run", "experiment.yaml", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert list(tmp_path.iterdir()) == [tmp_path / "storage.yaml"]


@pytest.mark.parametrize(
    "keys",
    [
        (),
        ("log_file",),
        ("ledger_db",),
        ("shared_state_db",),
        ("agno_db",),
        ("log_file", "ledger_db", "shared_state_db", "agno_db"),
    ],
)
def test_config_paths_override_only_selected_sources_from_another_cwd(
    tmp_path, monkeypatch, keys, control_directory
):
    import json

    from mininet_ai.substrates import FakeSubstrateRuntime
    from tests.test_cli_runtime import configured_plan

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    config = config_dir / "storage.yaml"
    # Mix an absolute selection with config-relative entries.
    paths = {
        key: str(tmp_path / "external-agno.sqlite3")
        if key == "agno_db"
        else f"external/{key}"
        for key in keys
    }
    config.write_text(json.dumps(paths))
    monkeypatch.chdir(elsewhere)
    runtime = FakeSubstrateRuntime(run_id_factory=lambda: "selected-run")
    with (
        patch(
            "mininet_ai.cli._compile_or_exit",
            return_value=configured_plan({"message": "handled"}),
        ),
        patch("mininet_ai.cli.reserve_run", return_value=("selected-run", runtime)),
        patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                "experiment.yaml",
                "--results-dir",
                str(tmp_path / "results"),
                "--storage-config",
                str(config),
                "--control-dir",
                control_directory,
            ],
        )
    assert result.exit_code == 0, result.output
    from mininet_ai.artifacts import run_directory

    directory = run_directory(tmp_path / "results", "selected-run")
    sources = json.loads((directory / "run.json").read_text())["sources"]
    defaults = {
        "log_file": directory / "logs/run.log",
        "ledger_db": directory / "dbs/ledger.sqlite3",
        "shared_state_db": directory / "dbs/shared-state.sqlite3",
        "agno_db": directory / "dbs/agno.sqlite3",
    }
    roles = {
        "log_file": "log",
        "ledger_db": "ledger",
        "shared_state_db": "shared_state",
        "agno_db": "agno",
    }
    for key, default in defaults.items():
        expected = (config_dir / paths[key]) if key in keys else default
        assert sources[roles[key]] == str(expected)
        assert expected.is_file()
        assert expected.stat().st_mode & 0o777 == 0o600
    assert not (elsewhere / "external").exists()


@pytest.mark.parametrize("kind", ["missing", "directory", "invalid-encoding"])
def test_unreadable_config_never_falls_back(tmp_path, kind):
    config = tmp_path / "storage.yaml"
    if kind == "directory":
        config.mkdir()
    elif kind == "invalid-encoding":
        config.write_bytes(b"\xff")
    with patch("mininet_ai.cli._compile_or_exit") as compile_plan:
        result = CliRunner().invoke(
            app, ["run", "missing.yaml", "--storage-config", str(config)]
        )
    assert result.exit_code == 2
    assert "Invalid storage configuration" in result.output
    compile_plan.assert_not_called()


@pytest.mark.parametrize(
    "missing", ["log_file", "ledger_db", "shared_state_db", "agno_db"]
)
def test_unreserved_adapter_requires_every_explicit_path(tmp_path, missing):
    from mininet_ai.substrates import FakeSubstrateRuntime
    from tests.storage_fixtures import storage_args
    from tests.test_cli_runtime import configured_plan

    paths = {
        key: tmp_path / key
        for key in ("log_file", "ledger_db", "shared_state_db", "agno_db")
        if key != missing
    }
    with (
        patch(
            "mininet_ai.cli._compile_or_exit",
            return_value=configured_plan({"message": "handled"}),
        ),
        patch(
            "mininet_ai.cli.reserve_run", return_value=(None, FakeSubstrateRuntime())
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                "experiment.yaml",
                "--results-dir",
                str(tmp_path / "results"),
                *storage_args(tmp_path, **paths),
            ],
        )
    assert result.exit_code == 2
    assert "--storage-config" in result.output
    assert "all four paths" in result.output
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize("selection", ["default", "legacy", "external", "new-root"])
def test_agent_memory_routing_and_explicit_legacy_reuse_stay_separate_from_runs(
    tmp_path, control_directory, selection
):
    import json

    from agno.db.schemas.memory import UserMemory

    from mininet_ai.agents.agno.storage import create_agno_database
    from mininet_ai.artifacts import run_directory
    from mininet_ai.specification.models import (
        LearnedMemoryConfiguration,
        MemoryConfiguration,
    )
    from mininet_ai.substrates import FakeSubstrateRuntime
    from tests.storage_fixtures import storage_args
    from tests.test_cli_runtime import configured_plan

    root = tmp_path / "results"
    root.mkdir(mode=0o700)
    existing = (
        root / "agno.sqlite3"
        if selection in {"legacy", "new-root"}
        else tmp_path / "old-memory.sqlite3"
    )
    database = create_agno_database(existing)
    database.upsert_user_memory(
        UserMemory(
            memory="Preserve this learned routing preference", user_id="router@s1"
        )
    )
    selected_root = tmp_path / "new-root" if selection == "new-root" else root
    extra = (
        storage_args(tmp_path, agno_db=existing)
        if selection in {"legacy", "external"}
        else []
    )
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
    for run_id in ("run-first", "run-second"):
        runtime = FakeSubstrateRuntime(run_id_factory=lambda run_id=run_id: run_id)
        with (
            patch("mininet_ai.cli._compile_or_exit", return_value=plan),
            patch("mininet_ai.cli.reserve_run", return_value=(run_id, runtime)),
            patch("mininet_ai.cli._SignalLatch.wait", return_value=None),
        ):
            result = CliRunner().invoke(
                app,
                [
                    "run",
                    "experiment.yaml",
                    "--results-dir",
                    str(selected_root),
                    "--control-dir",
                    control_directory,
                    *extra,
                ],
            )
        assert result.exit_code == 0, result.output
        directory = run_directory(selected_root, run_id)
        metadata = json.loads((directory / "run.json").read_text())
        expected = existing if extra else selected_root / "memory/agno.sqlite3"
        assert metadata["sources"]["agno"] == str(expected)
        assert metadata["sources"]["shared_state"] == str(
            directory / "dbs/shared-state.sqlite3"
        )
        assert not (directory / "dbs/agno.sqlite3").exists()
    assert len(database.get_user_memories(user_id="router@s1")) == 1
    if not extra:
        assert (
            create_agno_database(expected).get_user_memories(user_id="router@s1") == []
        )
    else:
        assert not (root / "memory").exists()
