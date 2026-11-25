import importlib
import json
from datetime import UTC, datetime
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
from mininet_ai.compiler import (
    compile_experiment,
    load_deployment_plan,
    save_deployment_plan,
)
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.specification.models import Experiment
from mininet_ai.substrates import FakeSubstrateRuntime

runner = importlib.import_module("examples.noms27.single-switch-escalation.runner")


def test_example_plan_and_authority(tmp_path):
    plan = compile_experiment(runner.SOURCE)
    assert (
        load_deployment_plan(save_deployment_plan(plan, tmp_path / "plan.json")) == plan
    )
    agents = {agent.id: agent for agent in plan.agents}
    assert set(agents) == {runner.LOCAL_AGENT, runner.GLOBAL_AGENT}
    assert agents[runner.LOCAL_AGENT].capabilities == ("openflow.flow.install",)
    assert agents[runner.GLOBAL_AGENT].capabilities == ("ovs.port.enable",)
    assert agents[runner.GLOBAL_AGENT].attachment.layer.value == "control"
    assert agents[runner.GLOBAL_AGENT].attachment.targets == ("s1",)
    document = yaml.safe_load(runner.SOURCE.read_text())
    choices = document["decisionScenarios"][0]["choices"]
    assert {choice["id"] for choice in choices} == {
        "restore_forward_flow",
        "restore_reverse_flow",
        "no_action",
        "escalate",
    }
    assert (
        next(choice for choice in choices if choice["id"] == "escalate")["proposals"]
        == []
    )


def test_cli_model_defaults_and_identity_requirement():
    args = runner.parse_args(["--evidence", "unused", "--skip-model-verification"])
    assert args.endpoint == "http://10.10.10.152:9931"
    document = yaml.safe_load(runner.SOURCE.read_text())
    assert args.endpoint == document["blueprints"][0]["model"]["parameters"]["endpoint"]
    assert args.model_name == "h2oai/h2o-lightning-4b"
    assert args.llm_model == "qwen3.5:latest"
    assert args.llm_host == "http://10.10.10.152:11434"
    with pytest.raises(SystemExit):
        runner.parse_args(["--evidence", "unused"])


def test_global_prompt_distinguishes_recipient_authority_from_local_restrictions():
    document = yaml.safe_load(runner.SOURCE.read_text())
    blueprint = next(
        item
        for item in document["blueprints"]
        if item["metadata"]["name"] == "global-recovery"
    )
    instructions = blueprint["reasoning"]["instructions"]
    assert "escalation recipient, NOT the local agent" in instructions
    assert "You are authorized to enable the declared switch ports" in instructions
    assert "does NOT restrict your authority" in instructions
    assert "Do not withhold a justified port-enable proposal" in instructions


def test_small_warmup_keeps_model_loaded(tmp_path):
    transport = Mock()
    transport.post_json.return_value = {"done": True, "message": {"content": "OK"}}
    runner.warm_global("http://ollama:11434/", "qwen3.5:latest", tmp_path, transport)
    transport.post_json.assert_called_once()
    url, payload = transport.post_json.call_args.args
    assert url == "http://ollama:11434/api/chat"
    assert payload["model"] == "qwen3.5:latest"
    assert payload["keep_alive"] == -1
    assert payload["options"]["num_predict"] == 2
    assert payload["stream"] is False
    assert (tmp_path / "llm-warmup-response.json").exists()


@pytest.mark.parametrize(
    "response", [{"error": "missing model"}, {"done": False}, None]
)
def test_failed_warmup_is_recorded(tmp_path, response):
    transport = Mock()
    transport.post_json.return_value = response
    with pytest.raises(RuntimeError):
        runner.warm_global("http://ollama", "model", tmp_path, transport)
    assert (tmp_path / "llm-warmup-error.json").exists()


def test_handoff_filters_evaluator_answers():
    intent = runner.handoff_intent(
        {"switch": {"diagnosis": "observed"}},
        [
            {
                "selectedChoice": "escalate",
                "status": "succeeded",
                "observedAt": "now",
                "expectedChoice": "escalate",
                "correct": True,
                "faultPort": "secret",
            }
        ],
    )
    assert "expectedChoice" not in intent
    assert "correct" not in intent
    assert "faultPort" not in intent
    assert "secret" not in intent
    assert json.loads(intent)["localDecisionHistory"][0]["selectedChoice"] == "escalate"


@pytest.mark.parametrize(
    "target,capability,port,accepted",
    [
        ("s1", "ovs.port.enable", "s1-eth1", True),
        ("s1", "ovs.port.enable", "s1-eth2", True),
        ("h1", "ovs.port.enable", "s1-eth1", False),
        ("s1", "ovs.port.enable", "h1-eth0", False),
        ("s1", "host.process.start", "s1-eth1", False),
    ],
)
def test_global_proposals_use_real_capability_authorization(
    target, capability, port, accepted
):
    document = yaml.safe_load(runner.SOURCE.read_text())
    blueprint = document["blueprints"][1]
    blueprint["implementation"] = {"type": "declarative"}
    blueprint["model"] = {
        "provider": "mock",
        "name": "deterministic",
        "parameters": {
            "response": {
                "proposals": [
                    {
                        "id": "repair",
                        "capability": capability,
                        "target": target,
                        "arguments": {"port": port},
                    }
                ]
            }
        },
    }
    plan = compile_experiment(Experiment.model_validate(document))

    class Substrate(FakeSubstrateRuntime):
        name = "mininet-ovs"

    substrate = Substrate()
    run = substrate.deploy(plan)
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    runtime = OneShotAgentRuntime(plan, substrate, registries)
    try:
        result = runtime.invoke(
            run.id, runner.GLOBAL_AGENT, "Inspect diagnostics and repair."
        )
        assert len(result.action_results) == 1
        assert (result.action_results[0].status.value == "succeeded") == accepted
    finally:
        substrate.teardown(run.id)


def test_confidence_gate_replaces_low_confidence_choice_without_mutating_response():
    gate = runner.ConfidenceEscalationGate(
        threshold=0.9, choices={"restore_forward_flow", "escalate", "no_action"}
    )
    gate.enabled = True
    response = {
        "answers": {
            "next_action": {
                "type": "choice",
                "choice": "restore_forward_flow",
                "confidence": 0.4,
                "probabilities": {"restore_forward_flow": 0.4, "escalate": 0.35},
            }
        }
    }

    effective = gate.apply(response)

    assert effective["answers"]["next_action"]["choice"] == "escalate"
    assert effective["gate"]["originalChoice"] == "restore_forward_flow"
    assert effective["gate"]["originalConfidence"] == 0.4
    assert effective["gate"]["originalProbability"] == 0.4
    assert response["answers"]["next_action"]["choice"] == "restore_forward_flow"
    assert gate.last == effective["gate"]


@pytest.mark.parametrize("confidence", [0.9, 0.95, None])
def test_confidence_gate_leaves_threshold_or_missing_confidence_unchanged(confidence):
    gate = runner.ConfidenceEscalationGate(
        threshold=0.9, choices={"restore_forward_flow", "escalate"}
    )
    gate.enabled = True
    answer = {"type": "choice", "choice": "restore_forward_flow"}
    if confidence is not None:
        answer["confidence"] = confidence
    response = {"answers": {"next_action": answer}}

    assert gate.apply(response) is response
    assert gate.last is None


def test_confidence_gate_is_disabled_until_enabled_and_needs_escalate():
    response = {
        "answers": {
            "next_action": {
                "type": "choice",
                "choice": "restore_forward_flow",
                "confidence": 0.1,
            }
        }
    }

    disabled = runner.ConfidenceEscalationGate(
        threshold=0.9, choices={"restore_forward_flow", "escalate"}
    )
    assert disabled.apply(response) is response

    no_escalate = runner.ConfidenceEscalationGate(
        threshold=0.9, choices={"restore_forward_flow", "no_action"}
    )
    no_escalate.enabled = True
    assert no_escalate.apply(response) is response


def test_recording_transport_retains_raw_and_gated_responses(tmp_path, monkeypatch):
    transport = Mock()
    transport.post_json.return_value = {
        "answers": {
            "next_action": {
                "type": "choice",
                "choice": "restore_forward_flow",
                "confidence": 0.2,
                "probabilities": {"restore_forward_flow": 0.2, "escalate": 0.1},
            }
        }
    }
    monkeypatch.setattr(runner._recovery, "HttpxJsonTransport", lambda: transport)
    gate = runner.ConfidenceEscalationGate(
        threshold=0.9, choices={"restore_forward_flow", "escalate"}
    )
    gate.enabled = True
    recorder = runner.RecordingTransport(tmp_path, gate=gate)

    effective = recorder.post_json(
        "unused", {"state": '{"switch":{}}'}, request_id="invoke-1"
    )

    assert effective["answers"]["next_action"]["choice"] == "escalate"
    assert (
        recorder.response["answers"]["next_action"]["choice"] == "restore_forward_flow"
    )
    raw = json.loads((tmp_path / "native/invoke-1/response.json").read_text())
    gated = json.loads(
        (tmp_path / "native/invoke-1/effective-response.json").read_text()
    )
    record = json.loads((tmp_path / "native/invoke-1/gate.json").read_text())
    assert raw["answers"]["next_action"]["choice"] == "restore_forward_flow"
    assert gated["answers"]["next_action"]["choice"] == "escalate"
    assert record["originalChoice"] == "restore_forward_flow"


def test_confidence_threshold_is_optional_and_bounded():
    args = runner.parse_args(["--evidence", "unused", "--skip-model-verification"])
    assert args.confidence_threshold is None
    args = runner.parse_args(
        [
            "--evidence",
            "unused",
            "--skip-model-verification",
            "--confidence-threshold",
            "0.9",
        ]
    )
    assert args.confidence_threshold == 0.9
    with pytest.raises(SystemExit):
        runner.parse_args(
            [
                "--evidence",
                "unused",
                "--skip-model-verification",
                "--confidence-threshold",
                "1.1",
            ]
        )


def install_trial_fakes(
    monkeypatch,
    args,
    *,
    choices=("escalate",),
    warm_choice="no_action",
    global_status="succeeded",
    repaired=True,
    cleanup=True,
    local_status="succeeded",
    global_respond=True,
):
    """Exercise the real runner lifecycle without root, inference, or Mininet."""
    monkeypatch.setattr(runner, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(runner, "command", lambda *parts: "[]" if "-j" in parts else "")
    monkeypatch.setattr(runner, "require_idle", Mock())
    monkeypatch.setattr(runner, "SQLiteRunLedger", Mock())

    class ImmediateEvent(Event):
        def wait(self, timeout=None):
            return self.is_set()

    monkeypatch.setattr(runner, "Event", ImmediateEvent)

    class Harness:
        def __init__(self, evidence):
            self.observation_lock = Lock()
            self.fault_at = None
            self.injected = False

        def probe_round(self):
            return {"success": True}

        def inject(self, port):
            self.injected = True
            self.fault_at = datetime.now(UTC)

        def verify_recovery(self):
            return {"recovered": repaired, "rounds": [{"success": repaired}] * 3}

        def verify_cleanup(self):
            return {"verified": cleanup}

    harnesses = []

    def harness_factory(evidence):
        harness = Harness(evidence)
        harnesses.append(harness)
        return harness

    monkeypatch.setattr(runner, "RecoveryHarness", harness_factory)

    class Runtime:
        def __init__(self, *positional, invocation_listener, **kwargs):
            self.retain = invocation_listener
            self.paused = set()
            self.intents = []

        def emit(self, choice):
            now = datetime.now(UTC)
            state = {
                "switch": {
                    "diagnosis": {
                        "portState": {
                            "value": {
                                "s1": {
                                    "ports": [
                                        {
                                            "observedAt": now.isoformat(),
                                            "name": "s1-eth1",
                                            "adminUp": not harnesses[0].injected,
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            }
            result = SimpleNamespace(
                started_at=now,
                invocation_id="test-id",
                status=SimpleNamespace(
                    value=local_status if harnesses[0].injected else "succeeded"
                ),
                response=SimpleNamespace(
                    metadata={
                        "executionRecord": {
                            "runtime": {
                                "boundedDecision": {
                                    "selectedChoice": choice,
                                    "request": {"state": json.dumps(state)},
                                }
                            }
                        }
                    }
                ),
            )
            self.retain(
                SimpleNamespace(
                    agent_id=runner.LOCAL_AGENT,
                    result=result,
                    model_dump=lambda **kwargs: {},
                )
            )

        def start(self):
            for _ in range(args.warmup_requests):
                self.emit(warm_choice)

        def pause_agent(self, agent):
            self.paused.add(agent)

        def resume_agent(self, agent):
            self.paused.discard(agent)
            for choice in choices:
                self.emit(choice)

        def submit_intent(self, agent, intent, source):
            assert runner.LOCAL_AGENT in self.paused
            self.intents.append((agent, intent))
            if not global_respond:
                return SimpleNamespace(model_dump=lambda **kwargs: {"source": source})
            result = SimpleNamespace(
                status=SimpleNamespace(value=global_status),
                action_results=[
                    SimpleNamespace(status=SimpleNamespace(value=global_status))
                ],
                model_dump=lambda **kwargs: {"status": global_status},
            )
            self.retain(
                SimpleNamespace(
                    agent_id=agent, result=result, model_dump=lambda **kwargs: {}
                )
            )
            return SimpleNamespace(model_dump=lambda **kwargs: {"source": source})

        def stop(self, **kwargs):
            assert kwargs["drain"] is False
            return SimpleNamespace(
                operation=SimpleNamespace(value="succeeded"),
                issues=[],
                model_dump=lambda **kwargs: {},
            )

    runtimes = []

    def runtime_factory(*positional, **kwargs):
        runtime = Runtime(*positional, **kwargs)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(runner, "ExperimentRuntime", runtime_factory)
    return harnesses, runtimes


@pytest.mark.parametrize(
    "choices,global_status,repaired,expected",
    [
        (("escalate",), "succeeded", True, "recovered"),
        (("restore_forward_flow",), "succeeded", True, "no-valid-escalation"),
        (
            ("no_action", "no_action", "no_action"),
            "succeeded",
            True,
            "no-valid-escalation",
        ),
        (("escalate",), "failed", True, "global-repair-failed"),
        (("escalate",), "succeeded", False, "recovery-verification-failed"),
    ],
)
def test_runner_handoff_and_outcomes(
    tmp_path, monkeypatch, choices, global_status, repaired, expected
):
    root = tmp_path / "trial"
    args = runner.parse_args(["--evidence", str(root), "--skip-model-verification"])
    harnesses, runtimes = install_trial_fakes(
        monkeypatch,
        args,
        choices=choices,
        global_status=global_status,
        repaired=repaired,
    )
    status = runner.run_trial(args)
    summary = json.loads((root / "summary.json").read_text())
    assert summary["stopReason"] == expected
    assert (status == 0) == (expected == "recovered")
    assert harnesses[0].injected
    assert len(runtimes[0].intents) == int(choices[-1] == "escalate")
    if global_status == "failed":
        assert summary["safeToContinue"] is False


def test_bad_healthy_decision_prevents_fault(tmp_path, monkeypatch):
    args = runner.parse_args(
        ["--evidence", str(tmp_path / "trial"), "--skip-model-verification"]
    )
    harnesses, runtimes = install_trial_fakes(monkeypatch, args, warm_choice="escalate")
    assert runner.run_trial(args) == 1
    assert not harnesses[0].injected
    assert runtimes[0].intents == []


@pytest.mark.parametrize(
    "choices,local_status,global_respond,expected",
    [
        ((), "succeeded", True, "local-trial-timeout"),
        (("escalate",), "failed", True, "no-valid-escalation"),
        (("escalate",), "succeeded", False, "global-timeout"),
    ],
)
def test_timeout_and_failed_selection_never_fall_back(
    tmp_path, monkeypatch, choices, local_status, global_respond, expected
):
    root = tmp_path / "trial"
    args = runner.parse_args(["--evidence", str(root), "--skip-model-verification"])
    _, runtimes = install_trial_fakes(
        monkeypatch,
        args,
        choices=choices,
        local_status=local_status,
        global_respond=global_respond,
    )
    assert runner.run_trial(args) == 1
    summary = json.loads((root / "summary.json").read_text())
    assert summary["stopReason"] == expected
    assert len(runtimes[0].intents) == int(expected == "global-timeout")
    if expected == "global-timeout":
        assert summary["safeToContinue"] is False


@pytest.mark.parametrize("actions", [[], ["succeeded", "succeeded"], ["rejected"]])
def test_global_completion_requires_one_successful_action(actions):
    result = SimpleNamespace(
        status=SimpleNamespace(value="succeeded"),
        action_results=[
            SimpleNamespace(status=SimpleNamespace(value=status)) for status in actions
        ],
    )
    assert not runner.global_repair_succeeded(result)


def test_cleanup_failure_prevents_success(tmp_path, monkeypatch):
    args = runner.parse_args(
        ["--evidence", str(tmp_path / "trial"), "--skip-model-verification"]
    )
    install_trial_fakes(monkeypatch, args, cleanup=False)
    assert runner.run_trial(args) == 1


def test_batch_warms_once_before_trials(tmp_path, monkeypatch):
    args = runner.parse_args(
        [
            "--evidence",
            str(tmp_path / "batch"),
            "--runs",
            "2",
            "--skip-model-verification",
        ]
    )
    monkeypatch.setattr(runner, "os", SimpleNamespace(geteuid=lambda: 0))
    calls = []
    monkeypatch.setattr(runner, "warm_global", lambda *args: calls.append("warm"))

    def trial(args):
        calls.append("trial")
        args.evidence.mkdir()
        runner.write_json(
            args.evidence / "summary.json",
            {"passed": True, "safeToContinue": True, "stopReason": "recovered"},
        )
        return 0

    monkeypatch.setattr(runner, "run_trial", trial)
    assert runner.run(args) == 0
    assert calls == ["warm", "trial", "trial"]


def test_batch_warm_failure_never_deploys(tmp_path, monkeypatch):
    args = runner.parse_args(
        ["--evidence", str(tmp_path / "batch"), "--skip-model-verification"]
    )
    monkeypatch.setattr(runner, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(
        runner, "warm_global", Mock(side_effect=RuntimeError("unavailable"))
    )
    trial = Mock()
    monkeypatch.setattr(runner, "run_trial", trial)
    assert runner.run(args) == 1
    trial.assert_not_called()


def test_batch_reports_startup_error_in_terminal(tmp_path, monkeypatch, capsys):
    from mininet_ai.runtime.ledger import LedgerError

    args = runner.parse_args(
        ["--evidence", str(tmp_path / "batch"), "--skip-model-verification"]
    )
    monkeypatch.setattr(runner, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(runner, "warm_global", Mock())
    message = "run ledger is not an owner-only regular file"
    monkeypatch.setattr(
        runner,
        "run_trial",
        Mock(side_effect=LedgerError(message, code="ledger.path.unsafe")),
    )
    assert runner.run(args) == 1
    output = capsys.readouterr().err
    assert "LedgerError" in output
    assert message in output
    assert str(args.evidence / "failure.json") in output
