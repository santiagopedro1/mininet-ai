import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from mininet_ai.compiler import compile_experiment

runner = importlib.import_module("examples.noms27.unified-system-1-2-recovery.runner")


def scenario(path):
    return yaml.safe_load(path.read_text())["decisionScenarios"][0]


def test_experiment_plan_shared_contract_and_authority():
    plan = compile_experiment(runner.SOURCE)
    agents = {agent.id: agent for agent in plan.agents}
    assert set(agents) == {runner.LOCAL_AGENT, runner.GLOBAL_AGENT}
    assert agents[runner.LOCAL_AGENT].attachment.layer.value == "data"
    assert agents[runner.GLOBAL_AGENT].attachment.layer.value == "control"
    assert set(agents[runner.LOCAL_AGENT].capabilities) == {
        "openflow.flow.install",
        "ovs.port.enable",
    }
    assert set(agents[runner.GLOBAL_AGENT].capabilities) == {
        "openflow.flow.install",
        "ovs.port.enable",
    }

    local = scenario(runner.SOURCE)
    choices = {choice["id"]: choice for choice in local["choices"]}
    assert set(choices) == {
        "restore_reverse_flow",
        "enable_access_port",
        "enable_peer_port",
        "restore_forward_flow",
        "no_action",
        "escalate",
    }
    assert choices["escalate"]["proposals"] == []
    assert choices["no_action"]["proposals"] == []

    other = scenario(
        Path(runner.SOURCE).parent.parent / "single-switch-recovery" / "experiment.yaml"
    )
    other_choices = {choice["id"]: choice for choice in other["choices"]}
    for choice_id in (
        "restore_reverse_flow",
        "restore_forward_flow",
        "no_action",
        "escalate",
    ):
        assert (
            choices[choice_id]["description"] == other_choices[choice_id]["description"]
        )


def test_system_two_prompt_distinguishes_observed_from_expected():
    document = yaml.safe_load(runner.SOURCE.read_text())
    instructions = document["blueprints"][1]["reasoning"]["instructions"]
    assert "switch.diagnosis" in instructions
    assert "switch.expectations" in instructions
    assert "NOT an observation" in instructions
    assert "absent from the observed flow entries is missing" in instructions
    assert "Never treat an entry in expectations as present" in instructions


@pytest.mark.parametrize(
    "category,target,expected",
    [
        ("link-failure", "s1-eth1", "enable_access_port"),
        ("link-failure", "s1-eth2", "enable_peer_port"),
        ("missing-flow", "forward", "restore_forward_flow"),
        ("missing-flow", "reverse", "restore_reverse_flow"),
    ],
)
def test_forced_fault_selection(category, target, expected):
    args = runner.parse_args(
        [
            "--evidence",
            "unused",
            "--skip-model-verification",
            "--fault",
            category,
            "--fault-target",
            target,
            "--seed",
            "7",
        ]
    )

    fault = runner.select_fault(args)

    assert fault["category"] == category
    assert fault["target"] == target
    assert fault["expectedChoice"] == expected
    assert fault["seed"] == 7
    assert fault["source"] == "forced-target"


def test_random_fault_selection_is_seeded_and_varies():
    args = runner.parse_args(
        [
            "--evidence",
            "unused",
            "--skip-model-verification",
            "--fault",
            "random",
            "--seed",
            "7",
        ]
    )
    picks = []
    for index in range(1, 21):
        args.run_index = index
        picks.append(runner.select_fault(args))

    assert {pick["category"] for pick in picks} == {"link-failure", "missing-flow"}
    assert all(pick["seed"] == 7 and pick["source"] == "random" for pick in picks)

    args.run_index = 5
    assert runner.select_fault(args) == picks[4]


def test_cli_defaults_and_validation():
    args = runner.parse_args(["--evidence", "unused", "--skip-model-verification"])
    assert args.fault == "random"
    assert args.fault_target is None
    assert args.seed is None
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
    with pytest.raises(SystemExit):
        runner.parse_args(
            [
                "--evidence",
                "unused",
                "--skip-model-verification",
                "--fault",
                "link-failure",
                "--fault-target",
                "forward",
            ]
        )


def test_missing_flow_injection_deletes_the_expected_cookie(tmp_path, monkeypatch):
    calls = []

    def fake_command(*parts):
        calls.append(parts)
        if parts[:3] == ("ovs-ofctl", "-O", "OpenFlow13") and "dump-flows" in parts:
            return ""
        return ""

    monkeypatch.setattr(runner, "command", fake_command)
    harness = runner.UnifiedHarness(tmp_path)

    harness._inject_flow("forward")

    assert (
        "ovs-ofctl",
        "-O",
        "OpenFlow13",
        "del-flows",
        "s1",
        "cookie=0x5101/-1",
    ) in calls
    fault = json.loads((tmp_path / "fault.json").read_text())
    assert fault["fault"] == "missing-flow"
    assert fault["target"] == "forward"
    assert fault["cookie"] == "0x5101"
    assert fault["expectedChoice"] == "restore_forward_flow"


def install_trial_fakes(
    monkeypatch,
    args,
    *,
    post_fault=(("enable_access_port", None),),
    warm_choice="no_action",
    global_status="succeeded",
    repaired=True,
    cleanup=True,
    local_status="succeeded",
):
    """Exercise the runner lifecycle without root, inference, or Mininet."""
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
            self.fault = None

        def probe_round(self):
            return {"success": True}

        def inject(self, category, target):
            self.injected = True
            self.fault = (category, target)
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

    monkeypatch.setattr(runner, "UnifiedHarness", harness_factory)

    class Runtime:
        def __init__(self, *positional, invocation_listener, **kwargs):
            self.retain = invocation_listener
            self.paused = set()
            self.intents = []

        def emit(self, choice, response=None):
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
                                            "state": "down"
                                            if harnesses[0].injected
                                            else "up",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            }
            decision = {
                "selectedChoice": choice,
                "request": {"state": json.dumps(state)},
            }
            if response is not None:
                decision["response"] = response
            result = SimpleNamespace(
                started_at=now,
                invocation_id="test-id",
                status=SimpleNamespace(
                    value=local_status if harnesses[0].injected else "succeeded"
                ),
                response=SimpleNamespace(
                    metadata={
                        "executionRecord": {"runtime": {"boundedDecision": decision}}
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
            for choice, response in post_fault:
                self.emit(choice, response)

        def submit_intent(self, agent, intent, source):
            assert runner.LOCAL_AGENT in self.paused
            self.intents.append((agent, intent))
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


GATE_RESPONSE = {
    "answers": {
        "next_action": {
            "type": "choice",
            "choice": "escalate",
            "confidence": 0.4,
            "probabilities": {"enable_access_port": 0.4, "escalate": 0.3},
        }
    },
    "gate": {
        "metric": "confidence",
        "threshold": 0.9,
        "originalChoice": "enable_access_port",
        "originalConfidence": 0.4,
        "originalProbability": 0.4,
        "effectiveChoice": "escalate",
    },
}


@pytest.mark.parametrize(
    "category,target,post_fault,stop_reason,passed,safe,escalated,gate_applied,cause",
    [
        (
            "link-failure",
            "s1-eth1",
            (("enable_access_port", None),),
            "recovered",
            True,
            True,
            False,
            False,
            None,
        ),
        (
            "missing-flow",
            "forward",
            (("restore_forward_flow", None),),
            "recovered",
            True,
            True,
            False,
            False,
            None,
        ),
        (
            "link-failure",
            "s1-eth1",
            (("escalate", None),),
            "recovered",
            True,
            True,
            True,
            False,
            "model-explicit",
        ),
        (
            "link-failure",
            "s1-eth1",
            (("escalate", GATE_RESPONSE),),
            "recovered",
            True,
            True,
            True,
            True,
            "confidence-gate",
        ),
        (
            "link-failure",
            "s1-eth1",
            (("enable_peer_port", None),),
            "local-decision-failed",
            False,
            True,
            False,
            False,
            None,
        ),
    ],
)
def test_runner_lifecycle(
    tmp_path,
    monkeypatch,
    category,
    target,
    post_fault,
    stop_reason,
    passed,
    safe,
    escalated,
    gate_applied,
    cause,
):
    args = runner.parse_args(
        [
            "--evidence",
            str(tmp_path / "run"),
            "--skip-model-verification",
            "--fault",
            category,
            "--fault-target",
            target,
            "--seed",
            "7",
        ]
    )
    _, runtimes = install_trial_fakes(monkeypatch, args, post_fault=post_fault)

    status = runner.run_trial(args)

    summary = json.loads((args.evidence / "summary.json").read_text())
    assert status == (0 if passed else 1)
    assert summary["stopReason"] == stop_reason
    assert summary["passed"] is passed
    assert summary["safeToContinue"] is safe
    assert summary["escalationExecuted"] is escalated
    assert summary["confidenceGateApplied"] is gate_applied
    assert summary["escalationCause"] == cause
    assert len(runtimes[0].intents) == int(escalated)
    assert summary["fault"]["category"] == category
    assert summary["fault"]["target"] == target


def test_global_failure_stops_the_batch(tmp_path, monkeypatch):
    args = runner.parse_args(
        [
            "--evidence",
            str(tmp_path / "run"),
            "--skip-model-verification",
            "--fault",
            "link-failure",
            "--fault-target",
            "s1-eth1",
        ]
    )
    install_trial_fakes(
        monkeypatch,
        args,
        post_fault=(("escalate", None),),
        global_status="failed",
    )

    assert runner.run_trial(args) == 1
    summary = json.loads((args.evidence / "summary.json").read_text())
    assert summary["stopReason"] == "global-repair-failed"
    assert summary["safeToContinue"] is False


def test_local_repair_verification_failure_is_recorded(tmp_path, monkeypatch):
    args = runner.parse_args(
        [
            "--evidence",
            str(tmp_path / "run"),
            "--skip-model-verification",
            "--fault",
            "missing-flow",
            "--fault-target",
            "reverse",
        ]
    )
    install_trial_fakes(
        monkeypatch,
        args,
        post_fault=(("restore_reverse_flow", None),),
        repaired=False,
    )

    assert runner.run_trial(args) == 1
    summary = json.loads((args.evidence / "summary.json").read_text())
    assert summary["stopReason"] == "local-recovery-verification-failed"
    assert summary["safeToContinue"] is True
