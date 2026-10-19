import json

import pytest

from mininet_ai.experiments.latency.runner import run_experiment
from mininet_ai.experiments.latency.trials import TrialConfig
from mininet_ai.experiments.latency.validation import validate_artifact
from tests.experiments import example
from tests.experiments.test_trial_coordinator import FakeRuntime, Session


def test_runner_persists_valid_recovery_and_refuses_existing_directory(tmp_path):
    destination = tmp_path / "run"
    summary = run_experiment(
        example.build_scenario(seed=0),
        destination,
        config=TrialConfig(measured=1, warmup=1),
        runtime=FakeRuntime(),
        session_factory=lambda scenario, trial_id: Session(),
    )
    result = validate_artifact(destination)
    assert (result.state, result.integrity, result.issues) == ("completed", "valid", ())
    assert summary["counts"]["measured"]["outcomes"]["recovered"] == 1
    recovery = next(
        r
        for r in summary["distributions"]
        if r["metric"] == "recovery" and not any(r["group"].values())
    )
    assert recovery["count"] == 1
    assert recovery["mean_seconds"] == 3
    assert len((destination / "agents.jsonl").read_text().splitlines()) == 20
    manifest = json.loads((destination / "run.json").read_text())
    assert manifest["provenance"]["resolved_model_identity"] is None
    before = (destination / "run.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_experiment(example.build_scenario(seed=0), destination)
    assert (destination / "run.json").read_bytes() == before


@pytest.mark.parametrize("case", ["timeout", "warmup", "provider", "cleanup"])
def test_runner_retains_unsuccessful_attempts_and_separates_abort(case, tmp_path):
    from mininet_ai.outcomes import CleanupOutcome
    from mininet_ai.runtime.instrumentation import current_binding

    class FaultSession(Session):
        def probe(self):
            if case == "warmup" or (case == "timeout" and self.injected):
                return (False, False)
            return super().probe()

        def respond(self, gate, observation_id):
            if case == "provider":
                binding = current_binding()
                assert binding is not None
                from dataclasses import replace

                context = replace(
                    binding.context,
                    agent_id="switch-agent-s1",
                    invocation_id="request-invocation",
                )
                request = binding.recorder.requests.start(context, "ollama")
                binding.recorder.requests.interrupted(
                    request, "local timeout", cancelled=True
                )

        def cleanup(self):
            return CleanupOutcome() if case == "cleanup" else super().cleanup()

    summary = run_experiment(
        example.build_scenario(seed=0),
        tmp_path / "run",
        config=TrialConfig(
            measured=2,
            warmup=1 if case == "warmup" else 0,
            response_seconds=4,
            cleanup_seconds=1,
        ),
        runtime=FakeRuntime(),
        session_factory=lambda *_: FaultSession(),
    )
    validation = validate_artifact(tmp_path / "run")
    assert validation.integrity == "valid", validation.issues
    assert summary["state"] == ("completed" if case == "timeout" else "aborted")
    counts = summary["counts"]["measured"]
    assert counts["started"] == (
        0 if case == "warmup" else 2 if case == "timeout" else 1
    )
    if case in {"provider", "cleanup"}:
        assert counts["outcomes"]["recovered"] == 1
    if case == "provider":
        events = [r for r in validation.records if "type" in r]
        assert any(r["type"] == "provider_request_started" for r in events)
        assert any(r["type"] == "provider_cancel_requested" for r in events)


def test_cli_requires_explicit_seed_and_rejects_nonfinite_budgets(monkeypatch, capsys):
    import sys

    for args, diagnostic in [
        (["--plan"], "--seed"),
        (["--seed", "0", "--setup-budget", "nan", "--plan"], "finite"),
    ]:
        monkeypatch.setattr(sys, "argv", ["run.py", *args])
        with pytest.raises(SystemExit) as error:
            example.main()
        assert error.value.code == 2
        assert diagnostic in capsys.readouterr().err


def test_runner_uses_real_agent_capability_and_provider_adapter_seams(tmp_path):
    from mininet_ai.experiments.latency.session import SubstrateTrialSession
    from mininet_ai.substrates import FakeSubstrateRuntime

    class Substrate(FakeSubstrateRuntime):
        name = "mininet-ovs"

    def factory(scenario, trial_id):
        class ObservedSession(SubstrateTrialSession):
            injected = False
            rounds = 0

            def ready(self):
                return True

            def inject(self):
                super().inject()
                self.injected = True

            def endpoints_down(self):
                return (self.injected, self.injected)

            def probe(self):
                if self.injected:
                    self.rounds += 1
                    return (self.rounds > 1, self.rounds > 1)
                return (True, True)

        return ObservedSession(scenario, trial_id, Substrate())

    from mininet_ai.compiler import compile_experiment
    from mininet_ai.specification.models import Experiment

    scenario = example.build_scenario(seed=0)
    snapshot = scenario.plan.snapshot
    snapshot["blueprints"][0]["implementation"]["entrypoint"] = (
        "tests.experiments.factories:create_seed_zero_recovery_agent"
    )
    from dataclasses import replace

    scenario = replace(
        scenario, plan=compile_experiment(Experiment.model_validate(snapshot))
    )
    summary = run_experiment(
        scenario,
        tmp_path / "run",
        config=TrialConfig(measured=1, warmup=0),
        runtime=FakeRuntime(),
        session_factory=factory,
    )
    validation = validate_artifact(tmp_path / "run")
    assert validation.integrity == "valid", validation.issues
    assert summary["counts"]["measured"]["outcomes"]["recovered"] == 1
    total = lambda metric: next(
        r
        for r in summary["distributions"]
        if r["metric"] == metric and not any(r["group"].values())
    )
    assert total("reasoning")["count"] == 10
    assert total("execution")["count"] == 40


def test_storage_failure_still_cleans_up_and_never_fabricates_completion(tmp_path):
    directory = tmp_path / "run"

    class DiskFailure(Session):
        def setup(self):
            # Simulate a lost append destination at the public storage boundary.
            (directory / "events.jsonl").unlink()
            (directory / "events.jsonl").mkdir()

    session = DiskFailure()
    with pytest.raises((OSError, RuntimeError)):
        run_experiment(
            example.build_scenario(seed=0),
            directory,
            config=TrialConfig(measured=2, warmup=0),
            runtime=FakeRuntime(),
            session_factory=lambda *_: session,
        )
    assert session.removed
    assert not (directory / "summary.json").exists()
    assert (directory / "trials.jsonl").read_text() == ""
    assert validate_artifact(directory).state == "incomplete"


def test_interruption_retains_started_evidence_without_final_projections(tmp_path):
    class Interrupted(Session):
        def setup(self):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_experiment(
            example.build_scenario(seed=0),
            tmp_path / "run",
            config=TrialConfig(measured=1, warmup=0),
            runtime=FakeRuntime(),
            session_factory=lambda *_: Interrupted(),
        )
    validation = validate_artifact(tmp_path / "run")
    assert validation.state == "incomplete"
    assert not any(
        r.get("type") in {"run_finished", "run_aborted"} for r in validation.records
    )
    assert (tmp_path / "run/trials.jsonl").read_text() == ""


def test_slow_probe_slots_and_late_work_are_retained_but_not_recovery(tmp_path):
    summary = run_experiment(
        example.build_scenario(seed=0),
        tmp_path / "run",
        config=TrialConfig(measured=1, warmup=0, response_seconds=4),
        runtime=FakeRuntime(probe=1_500_000_000),
        session_factory=lambda *_: Session(),
    )
    validation = validate_artifact(tmp_path / "run")
    assert summary["counts"]["measured"]["outcomes"]["timeout"] == 1
    assert any(r.get("type") == "slot_missed" for r in validation.records)
    assert any(
        r.get("type") == "probe_round" and r["data"]["late"] for r in validation.records
    )


def test_manifest_matches_seed_zero_golden_vector(tmp_path):
    run_experiment(
        example.build_scenario(seed=0),
        tmp_path / "run",
        config=TrialConfig(measured=3, warmup=0),
        runtime=FakeRuntime(),
        session_factory=lambda *_: Session(),
    )
    manifest = json.loads((tmp_path / "run/run.json").read_text())
    golden = json.loads(
        __import__("pathlib")
        .Path("docs/fixtures/link-failure-latency-seed-v1-0.json")
        .read_text()
    )
    replay = manifest["scenario"]
    for actual, expected in (
        ("graph", "edges"),
        ("ports_by_switch", "ports_by_switch"),
        ("host_attachments", "host_attachments"),
        ("healthy_paths", "healthy_paths"),
        ("eligible_failure_links", "eligible_failure_links"),
    ):
        assert replay[actual] == golden[expected]
    assert replay["failure_order"]["measured"] == golden["measured_failure_order"][:3]


def test_summary_retains_failed_reasoning_and_operation_outcome_breakdown():
    from mininet_ai.experiments.latency.summary import derive_summary
    from tests.experiments.test_latency_artifacts import with_reasoning

    artifact = with_reasoning()
    records = sorted(
        [
            r
            for name, rows in artifact.items()
            if name != "summary.json"
            for r in (rows if isinstance(rows, list) else [rows])
        ],
        key=lambda r: r["collector_seq"],
    )
    summary = derive_summary(records)
    row = next(
        r
        for r in summary["distributions"]
        if r["metric"] == "reasoning" and not any(r["group"].values())
    )
    assert (
        row["count"],
        row["mean_seconds"],
        row["median_seconds"],
        row["unavailable"],
    ) == (1, 1e-9, 1e-9, 9)
    failed = next(
        r
        for r in summary["distributions"]
        if r["metric"] == "reasoning"
        and r["group"]["operation_outcome"] == "provider_error"
    )
    assert failed["count"] == 1


def test_live_acceptance_without_opt_in_is_explicitly_unverified(tmp_path):
    import os
    import subprocess
    import sys

    environment = dict(os.environ)
    environment.pop("MININET_AI_LATENCY_LIVE", None)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/accept-latency-live.py",
            "--output",
            str(tmp_path / "live"),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 77
    assert "UNVERIFIED" in result.stderr
    assert not (tmp_path / "live").exists()


def test_recorder_loss_invalidates_required_evidence_and_aborts(tmp_path):
    from mininet_ai.runtime.instrumentation import current_binding

    class LostEvidence(Session):
        def setup(self):
            binding = current_binding()
            assert binding is not None
            binding.recorder.report_loss(binding.context, "injected recorder loss")

    with pytest.raises(ValueError, match="artifact validation failed"):
        run_experiment(
            example.build_scenario(seed=0),
            tmp_path / "run",
            config=TrialConfig(measured=2, warmup=0, cleanup_seconds=1),
            runtime=FakeRuntime(),
            session_factory=lambda *_: LostEvidence(),
        )
    result = validate_artifact(tmp_path / "run")
    assert result.state == "aborted"
    assert result.integrity == "invalid"
    assert any(i.code == "recorder_loss" for i in result.issues)
    summary = json.loads((tmp_path / "run/summary.json").read_text())
    assert summary["counts"]["measured"]["started"] == 1
    assert summary["counts"]["measured"]["outcomes"]["invalid"] == 1


@pytest.mark.parametrize("damage", ["corruption", "truncation", "incomplete"])
def test_actual_run_artifacts_expose_corrupted_truncated_and_incomplete_evidence(
    tmp_path, damage
):
    directory = tmp_path / "run"
    run_experiment(
        example.build_scenario(seed=0),
        directory,
        config=TrialConfig(measured=1, warmup=0),
        runtime=FakeRuntime(),
        session_factory=lambda *_: Session(),
    )
    if damage == "corruption":
        summary = json.loads((directory / "summary.json").read_text())
        summary["counts"]["measured"]["finalized"] = 0
        (directory / "summary.json").write_text(json.dumps(summary))
    elif damage == "truncation":
        with (directory / "events.jsonl").open("a") as stream:
            stream.write('{"record_id":')
    else:
        (directory / "summary.json").unlink()
    result = validate_artifact(directory)
    assert result.integrity == ("invalid" if damage == "corruption" else "valid")
    assert result.state == ("completed" if damage == "corruption" else "incomplete")
