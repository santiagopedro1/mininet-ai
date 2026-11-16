import importlib
import json
from copy import deepcopy
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from pydantic import ValidationError

from mininet_ai.agents import register_builtin_providers
from mininet_ai.compiler import (
    compile_experiment,
    load_deployment_plan,
    save_deployment_plan,
)
from mininet_ai.experiment import ExperimentRuntime
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.specification.models import DecisionScenario
from mininet_ai.substrates import FakeSubstrateRuntime

runner = importlib.import_module("examples.noms27.single-switch-recovery.runner")
EXPECTED_CHOICE = runner.EXPECTED_CHOICE
SOURCE = runner.SOURCE
score_choice = runner.score_choice


def scenario():
    return yaml.safe_load(SOURCE.read_text())["decisionScenarios"][0]


def test_single_switch_example_round_trips(tmp_path):
    plan = compile_experiment(SOURCE)
    restored = load_deployment_plan(save_deployment_plan(plan, tmp_path / "plan.json"))
    assert restored == plan
    assert len(plan.agents) == 1
    assert plan.agents[0].attachment.targets == ("s1",)
    assert len(scenario()["choices"]) == 7
    assert (
        sum(
            score_choice(choice["id"])["selectionCorrect"]
            for choice in scenario()["choices"]
        )
        == 1
    )


def test_observation_compaction_drops_ignored_fields_without_mutating_input():
    flows = {
        "s1": {
            "switches": [
                {
                    "name": "s1",
                    "flows": [
                        {
                            "cookie": "0x5101",
                            "table": 0,
                            "priority": 100,
                            "match": {"ip": True},
                            "actions": "output:2",
                            "packets": 4,
                            "bytes": 392,
                            "durationSeconds": 3.1,
                            "idleAgeSeconds": 1,
                            "hardAgeSeconds": 2,
                        }
                    ],
                }
            ]
        }
    }

    compacted = runner.compact_observation(
        SimpleNamespace(name="openflow.flows"), flows
    )

    assert compacted == {
        "s1": {
            "switches": [
                {
                    "name": "s1",
                    "flows": [
                        {
                            "cookie": "0x5101",
                            "table": 0,
                            "priority": 100,
                            "match": {"ip": True},
                            "actions": "output:2",
                        }
                    ],
                }
            ]
        }
    }
    assert flows["s1"]["switches"][0]["flows"][0]["packets"] == 4

    ports = {
        "s1": {
            "ports": [
                {
                    "name": "s1-eth1",
                    "number": 1,
                    "state": "down",
                    "observedAt": "2026-11-04T00:00:00+00:00",
                }
            ]
        }
    }

    compacted_ports = runner.compact_observation(
        SimpleNamespace(name="ovs.port-state"), ports
    )

    assert compacted_ports["s1"]["ports"] == [
        {
            "name": "s1-eth1",
            "state": "down",
            "observedAt": "2026-11-04T00:00:00+00:00",
        }
    ]


def test_monitor_choice_descriptions_use_the_tested_applicability_conditions():
    criteria = {choice["id"]: choice["description"] for choice in scenario()["choices"]}
    assert criteria == {
        "restore_reverse_flow": "Required flow cookie 0x5102 (h2 to h1: in_port 2, nw_src 10.0.0.2, nw_dst 10.0.0.1, output 1) is missing or differs while all ports are up.",
        "enable_access_port": "Required switch port s1-eth1 is down.",
        "enable_peer_port": "Required switch port s1-eth2 is down.",
        "restore_forward_flow": "Required flow cookie 0x5101 (h1 to h2: in_port 1, nw_src 10.0.0.1, nw_dst 10.0.0.2, output 2) is missing or differs while all ports are up.",
        "repair_neighbor_entry": "A required host neighbor mapping is missing or incorrect while all ports are up and required flows are present.",
        "no_action": "All required ports are up and every required flow is present and correct.",
        "escalate": "An observation is unavailable, no supplied local policy matches, or the repair is outside local authority.",
    }


@pytest.mark.parametrize(
    "mutation", ["duplicate", "unlisted-rule", "escalation-action", "diagnosis-action"]
)
def test_current_recovery_contract_rejects_invalid_choices(mutation):
    document = deepcopy(scenario())
    if mutation == "duplicate":
        document["choices"][1]["id"] = document["choices"][0]["id"]
    elif mutation == "unlisted-rule":
        proposal = document["choices"][0]["proposals"][0]
        proposal["arguments"] = {**proposal["arguments"], "actions": "NORMAL"}
    elif mutation == "escalation-action":
        document["choices"][-1]["proposals"] = document["choices"][0]["proposals"]
    else:
        document["inputMode"] = "current-only"
    with pytest.raises(ValidationError):
        DecisionScenario.model_validate(document)


@pytest.mark.parametrize(
    "choice,inspection",
    [(item["id"], "missing") for item in scenario()["choices"]]
    + [(EXPECTED_CHOICE, "unreadable")],
)
def test_each_choice_runs_without_fallback_or_answer_leakage(choice, inspection):
    class Transport:
        def __init__(self):
            self.requests = []

        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            self.requests.append(payload)
            return {"answers": {"next_action": {"type": "choice", "choice": choice}}}

    class Substrate(FakeSubstrateRuntime):
        name = "mininet-ovs"

        def __init__(self):
            super().__init__()
            self.mutations = []

        def execute(self, run_id, request):
            self.mutations.append(request)
            return super().execute(run_id, request)

        def inspect_scenario_actions(self, run_id, requests, **kwargs):
            return tuple(
                inspection
                if request.name == "ovs.port.enable"
                and request.parameters["port"] == "s1-eth1"
                else "present"
                for request in requests
            )

    substrate = Substrate()
    transport = Transport()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    records = []
    done = Event()

    def retain(record):
        runtime.pause_agent(record.agent_id)
        records.append(record)
        done.set()

    runtime = ExperimentRuntime(
        compile_experiment(SOURCE),
        substrate,
        registries,
        decision_transport=transport,
        invocation_listener=retain,
    )
    try:
        runtime.start()
        assert done.wait(5)
    finally:
        runtime.stop()
    assert len(records) == 1
    if choice == EXPECTED_CHOICE and inspection == "missing":
        assert records[0].result.status.value == "succeeded"
        assert len(substrate.mutations) == 1
        assert substrate.mutations[0].name == "ovs.port.enable"
        assert substrate.mutations[0].parameters == {"port": "s1-eth1"}
    else:
        assert (
            choice != EXPECTED_CHOICE or records[0].result.status.value != "succeeded"
        )
        assert substrate.mutations == []
    wire = json.dumps(transport.requests)
    assert "dummy" not in wire and "stub" not in wire
    assert "expectedChoice" not in wire
    assert len(transport.requests) == 2  # Startup probe and one measured decision.
    assert set(transport.requests[-1]["questions"]["next_action"]["criteria"]) == {
        item["id"] for item in scenario()["choices"]
    }


def test_escalation_is_wrong_and_never_calls_an_llm():
    result = score_choice("escalate")
    assert not result["selectionCorrect"]
    assert result["escalationRequested"]
    assert not result["escalationExecuted"]


def test_packaged_schema_accepts_current_recovery():
    from jsonschema import Draft202012Validator

    root = Path(__file__).parents[2] / "mininet_ai/compiler/schemas"
    schema = json.loads((root / "v1alpha5-decision-scenario.json").read_text())
    Draft202012Validator(schema).validate(scenario())


def test_live_runner_starts_healthy_and_never_injects_from_observation_collection(
    tmp_path, monkeypatch
):
    """Exercise the runner's actual factory, without root, networking or inference."""
    events = []
    harness = Mock()
    harness.observation_lock = Lock()
    harness.attach.side_effect = lambda network: events.append("baseline")
    harness.inject.side_effect = lambda: events.append("fault-and-disruption-check")
    monkeypatch.setattr(runner, "os", Mock(geteuid=lambda: 0))
    monkeypatch.setattr(
        runner, "command", lambda *args: "[]" if args[0] == "ip" else ""
    )
    monkeypatch.setattr(runner, "require_idle", lambda *args: None)
    monkeypatch.setattr(runner, "RecoveryHarness", lambda evidence: harness)
    monkeypatch.setattr(runner.MininetOVSObservations, "__init__", lambda *args: None)
    monkeypatch.setattr(
        runner.MininetOVSObservations, "collect", lambda *args: {"raw": "diagnostics"}
    )

    class FactoryChecked(Exception):
        pass

    def inspect_factory(*, observation_factory):
        observations = observation_factory(compile_experiment(SOURCE), object())
        assert events == ["baseline"], (
            "deployment must remain healthy until an operator triggers the fault"
        )
        events.clear()
        assert observations.collect(object()) == {"raw": "diagnostics"}
        assert observations.collect(object()) == {"raw": "diagnostics"}
        assert events == [], (
            "diagnostic collection must not inject faults or run disruption probes"
        )
        raise FactoryChecked

    monkeypatch.setattr(runner, "MininetOVSRuntime", inspect_factory)
    args = runner.parse_args(
        ["--evidence", str(tmp_path / "evidence"), "--skip-model-verification"]
    )
    with pytest.raises(FactoryChecked):
        runner.run_trial(args)


def test_monitor_warms_up_then_counts_three_no_actions():
    from datetime import UTC, datetime, timedelta

    trial = runner.MonitorTrial(2, 3, EXPECTED_CHOICE)
    now = datetime.now(UTC)
    assert trial.observe("no_action", "succeeded", now)["correct"]
    assert not trial.ready.is_set()
    trial.observe("no_action", "succeeded", now)
    assert trial.ready.is_set()
    assert trial.warmup_succeeded
    trial.fault_at = now + timedelta(seconds=1)
    trial.observe("no_action", "succeeded", now)  # An in-flight healthy snapshot.
    assert trial.checks == []
    for index in range(3):
        evaluation = trial.observe("no_action", "succeeded", now + timedelta(seconds=2))
        assert not evaluation["correct"]
        assert trial.done.is_set() == (index == 2)
    assert len(trial.checks) == 3


def test_warmup_is_fixed_length_not_retry_until_success():
    from datetime import UTC, datetime

    trial = runner.MonitorTrial(3, 3, EXPECTED_CHOICE)
    now = datetime.now(UTC)
    for choice in ("no_action", "escalate", "no_action"):
        trial.observe(choice, "succeeded", now)
    assert trial.ready.is_set()
    assert not trial.warmup_succeeded
    assert len(trial.warmup_checks) == 3
    assert trial.checks == []


@pytest.mark.parametrize("flag", ["--runs", "--warmup-requests", "--max-checks"])
def test_batch_counts_must_be_positive(flag):
    with pytest.raises(SystemExit):
        runner.parse_args(
            ["--evidence", "unused", "--skip-model-verification", flag, "0"]
        )


def test_batch_keeps_failed_decisions_and_continues_after_verified_cleanup(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "os", Mock(geteuid=lambda: 0))
    destinations = []

    def trial(args):
        destinations.append(args.evidence)
        args.evidence.mkdir()
        passed = len(destinations) == 2
        runner.write_json(
            args.evidence / "summary.json",
            {
                "passed": passed,
                "safeToContinue": True,
                "stopReason": "check-limit-or-policy-selected",
            },
        )
        return 0 if passed else 1

    monkeypatch.setattr(runner, "run_trial", trial)
    root = tmp_path / "batch"
    args = runner.parse_args(
        [
            "--evidence",
            str(root),
            "--skip-model-verification",
            "--runs",
            "3",
            "--warmup-requests",
            "2",
        ]
    )
    assert runner.run(args) == 1
    summary = json.loads((root / "summary.json").read_text())
    assert summary["completedRuns"] == 3
    assert summary["passedRuns"] == 1
    assert summary["failedRuns"] == 2
    assert summary["warmupRequestsPerRun"] == 2
    assert not summary["stoppedEarly"]
    assert destinations == [root / f"run-{index:03d}" for index in (1, 2, 3)]


@pytest.mark.parametrize("failure", ["unsafe-cleanup", "exception", "interrupt"])
def test_batch_stops_instead_of_redeploying_when_unsafe_or_interrupted(
    tmp_path, monkeypatch, failure
):
    monkeypatch.setattr(runner, "os", Mock(geteuid=lambda: 0))
    calls = []

    def trial(args):
        calls.append(args.evidence)
        if failure == "exception":
            raise RuntimeError("deployment failed")
        if failure == "interrupt":
            raise KeyboardInterrupt
        args.evidence.mkdir()
        runner.write_json(
            args.evidence / "summary.json", {"passed": False, "safeToContinue": False}
        )
        return 1

    monkeypatch.setattr(runner, "run_trial", trial)
    root = tmp_path / "batch"
    args = runner.parse_args(
        ["--evidence", str(root), "--skip-model-verification", "--runs", "5"]
    )
    assert runner.run(args) == 1
    summary = json.loads((root / "summary.json").read_text())
    assert len(calls) == 1
    assert summary["stoppedEarly"]
    assert summary["status"] == "stopped"
    assert summary["attemptedRuns"] == 1
    assert (
        summary["stopReason"]
        == {
            "unsafe-cleanup": "unsafe-to-continue",
            "exception": "run-error",
            "interrupt": "operator-stop",
        }[failure]
    )
    assert summary["errorRuns"] == int(failure == "exception")
    assert summary["interruptedRuns"] == int(failure == "interrupt")


def test_batch_never_overwrites_existing_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "os", Mock(geteuid=lambda: 0))
    trial = Mock()
    monkeypatch.setattr(runner, "run_trial", trial)
    original = tmp_path / "keep.txt"
    original.write_text("existing evidence")
    args = runner.parse_args(["--evidence", str(tmp_path), "--skip-model-verification"])
    with pytest.raises(FileExistsError):
        runner.run(args)
    trial.assert_not_called()
    assert original.read_text() == "existing evidence"


@pytest.mark.parametrize("choice", [EXPECTED_CHOICE, "enable_peer_port", "escalate"])
def test_monitor_stops_on_first_post_fault_policy_selection(choice):
    from datetime import UTC, datetime

    trial = runner.MonitorTrial(2, 3, EXPECTED_CHOICE)
    trial.fault_at = datetime.now(UTC)
    evaluation = trial.observe(choice, "succeeded", trial.fault_at)
    assert evaluation["correct"] == (choice == EXPECTED_CHOICE)
    assert trial.done.is_set()
    assert len(trial.checks) == 1


def test_verification_bypass_is_explicit_and_needs_no_fake_identity():
    from mininet_ai.specification.models import ModelConfiguration

    args = runner.parse_args(["--evidence", "unused", "--skip-model-verification"])
    parameters = runner.model_parameters(args)
    assert parameters == {
        "endpoint": "http://10.10.10.152:9931",
        "verifyIdentity": False,
    }
    model = ModelConfiguration.model_validate(
        {
            "provider": "llamacpp-systemone",
            "name": "unverified-name",
            "parameters": parameters,
        }
    )
    assert "expectedIdentity" not in model.parameters
    with pytest.raises(SystemExit):
        runner.parse_args(["--evidence", "unused"])


def test_runner_defaults_match_the_q8_server_configuration():
    args = runner.parse_args(["--evidence", "unused", "--skip-model-verification"])
    model = yaml.safe_load(SOURCE.read_text())["blueprints"][0]["model"]
    assert args.endpoint == model["parameters"]["endpoint"] == "http://10.10.10.152:9931"
    assert args.model_name == model["name"] == "h2oai/h2o-lightning-4b"
    assert model["parameters"]["expectedIdentity"]["artifact"] == "h2o-lightning-4b-Q8_0.gguf"


def test_recording_transport_retains_multiple_monitor_checks(tmp_path, monkeypatch):
    transport = Mock()
    transport.post_json.return_value = {
        "answers": {"next_action": {"type": "choice", "choice": "no_action"}}
    }
    monkeypatch.setattr(runner, "HttpxJsonTransport", lambda: transport)
    recorder = runner.RecordingTransport(tmp_path)
    for index in range(2):
        recorder.post_json(
            "unused", {"state": '{"switch":{}}'}, request_id=f"invoke-{index}"
        )
        assert (tmp_path / "native" / f"invoke-{index}" / "request.json").exists()
        assert (tmp_path / "native" / f"invoke-{index}" / "response.json").exists()
    assert recorder.measured_requests == 2


@pytest.mark.parametrize(
    "post_fault_choice,expected_pass,expected_checks,runs",
    [
        (EXPECTED_CHOICE, True, 1, 1),
        ("no_action", False, 3, 1),
        ("escalate", False, 1, 1),
        (EXPECTED_CHOICE, True, 1, 2),
    ],
)
def test_monitor_runner_completes_full_lifecycle_with_scripted_model(
    tmp_path, monkeypatch, post_fault_choice, expected_pass, expected_checks, runs
):
    """Real scheduling, inference mapping and authorization; simulated network/model."""
    from datetime import UTC, datetime
    from time import monotonic

    from jsonschema import Draft202012Validator

    state = {"down": False, "injections": 0, "repairs": 0}
    original_harness = runner.RecoveryHarness

    def attach(harness, network):
        harness.network = network
        state["down"] = False

    def inject(harness, port):
        assert port == "s1-eth1"
        state["injections"] += 1
        state["down"] = True
        harness.fault_at = datetime.now(UTC)
        harness.fault_tick = monotonic()

    monkeypatch.setattr(original_harness, "attach", attach)
    monkeypatch.setattr(original_harness, "inject", inject)
    monkeypatch.setattr(
        original_harness,
        "probe_round",
        lambda self: {"success": not state["down"], "tick": monotonic()},
    )
    monkeypatch.setattr(
        original_harness,
        "verify_recovery",
        lambda self: {"recovered": not state["down"], "endToEndRecoverySeconds": 0.1},
    )
    monkeypatch.setattr(
        original_harness, "verify_cleanup", lambda self: {"verified": True}
    )
    monkeypatch.setattr(runner, "os", Mock(geteuid=lambda: 0))
    monkeypatch.setattr(
        runner, "command", lambda *args: "[]" if args[0] == "ip" else ""
    )
    monkeypatch.setattr(runner, "require_idle", lambda *args: None)

    def no_manual_input(prompt):
        raise AssertionError("automatic batches must not ask for input")

    monkeypatch.setattr("builtins.input", no_manual_input)
    monkeypatch.setattr(runner.MininetOVSObservations, "__init__", lambda *args: None)

    class Substrate(FakeSubstrateRuntime):
        name = "mininet-ovs"

        def __init__(self, *, observation_factory):
            super().__init__()
            self.observation_factory = observation_factory

        def deploy(self, plan):
            self.observation_factory(plan, object())
            return super().deploy(plan)

        def observe(self, run_id, query):
            result = super().observe(run_id, query)
            if query.name == "ovs.port-state":
                ports = [
                    {
                        "name": f"s1-eth{index}",
                        "number": index,
                        "state": "down" if index == 1 and state["down"] else "up",
                        "observedAt": datetime.now(UTC).isoformat(),
                    }
                    for index in (1, 2)
                ]
                return result.model_copy(update={"values": {"s1": {"ports": ports}}})
            return result

        def inspect_scenario_actions(self, run_id, requests, **kwargs):
            return tuple(
                "missing"
                if request.name == "ovs.port.enable" and state["down"]
                else "present"
                for request in requests
            )

        def execute(self, run_id, request):
            assert request.name == "ovs.port.enable"
            assert request.target == "s1" and request.parameters == {"port": "s1-eth1"}
            result = super().execute(run_id, request)
            state["down"] = False
            state["repairs"] += 1
            return result

    class Transport:
        def get_json(self, url, **kwargs):
            return (
                {"status": "ok"}
                if url.endswith("/health")
                else {"model_alias": "not-the-declared-model"}
            )

        def post_json(self, url, payload, **kwargs):
            choice = "no_action"
            if payload["state"] != "{}":
                context = json.loads(payload["state"])
                ports = context["switch"]["diagnosis"]["portState"]["value"]["s1"][
                    "ports"
                ]
                if ports[0]["state"] == "down":
                    choice = post_fault_choice
            return {
                "answers": {
                    "next_action": {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 0.8,
                        "probabilities": {choice: 0.9},
                    }
                },
                "usage": {"input_tokens": 10, "output_tokens": 0},
            }

    monkeypatch.setattr(runner, "MininetOVSRuntime", Substrate)
    monkeypatch.setattr(runner, "HttpxJsonTransport", Transport)
    root = tmp_path / "monitor"
    args = runner.parse_args(
        [
            "--evidence",
            str(root),
            "--skip-model-verification",
            "--ready-timeout",
            "10",
            "--trial-timeout",
            "10",
            "--runs",
            str(runs),
        ]
    )
    assert runner.run(args) == (0 if expected_pass else 1)
    assert state == {
        "down": not expected_pass,
        "injections": runs,
        "repairs": int(expected_pass) * runs,
    }
    summary = json.loads((root / "summary.json").read_text())
    assert summary["passed"] == expected_pass
    assert summary["completedRuns"] == runs
    assert summary["passedRuns"] == (runs if expected_pass else 0)
    assert summary["warmupRequestsPerRun"] == 3
    for index in range(1, runs + 1):
        trial_root = root / f"run-{index:03d}"
        trial_summary = json.loads((trial_root / "summary.json").read_text())
        assert trial_summary["warmupAttemptsCompleted"] == 3
        assert trial_summary["warmupSucceeded"]
        assert trial_summary["confidence"] == 0.8
        assert trial_summary["choiceProbability"] == 0.9
        assert json.loads((trial_root / "ready.json").read_text())["success"]
        assert len(trial_summary["postFaultChecks"]) == expected_checks
    root = root / "run-001"
    summary = json.loads((root / "summary.json").read_text())
    assert summary["incorrectPostFaultChecks"] == (
        0 if expected_pass else expected_checks
    )
    evaluations = [
        json.loads(line)
        for line in (root / "evaluations.jsonl").read_text().splitlines()
    ]
    assert [item["selectedChoice"] for item in evaluations[:3]] == [
        "no_action",
        "no_action",
        "no_action",
    ]
    assert all(item["phase"] == "warmup" for item in evaluations[:3])
    schema_root = Path(__file__).parents[2] / "mininet_ai/compiler/schemas"
    schema = json.loads((schema_root / "bounded-decision-v1alpha1.json").read_text())
    for line in (root / "invocations.jsonl").read_text().splitlines():
        invocation = json.loads(line)
        evidence = invocation["result"]["response"]["metadata"]["executionRecord"][
            "runtime"
        ]["boundedDecision"]
        assert evidence["modelProvenance"]["expectedIdentity"] is None
        assert (
            evidence["modelProvenance"]["startup"]["identityVerification"] == "skipped"
        )
        Draft202012Validator(schema).validate(evidence)
