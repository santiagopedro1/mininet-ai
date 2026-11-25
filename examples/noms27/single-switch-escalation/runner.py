"""Demonstrate explicit System One escalation to an experiment-wide LLM agent."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

import yaml

from mininet_ai.agents import register_builtin_providers
from mininet_ai.compiler import compile_experiment, save_deployment_plan
from mininet_ai.experiment import ExperimentRuntime
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime.ledger import SQLiteRunLedger
from mininet_ai.substrates.mininet_ovs.observations import MininetOVSObservations
from mininet_ai.substrates.mininet_ovs.runtime import MininetOVSRuntime
from mininet_ai.transports.http import HttpxJsonTransport

_recovery = importlib.import_module("examples.noms27.single-switch-recovery.runner")
MonitorTrial = _recovery.MonitorTrial
RecordingTransport = _recovery.RecordingTransport
RecoveryHarness = _recovery.RecoveryHarness
ConfidenceEscalationGate = _recovery.ConfidenceEscalationGate
command = _recovery.command
compact_observation = _recovery.compact_observation
model_parameters = _recovery.model_parameters
require_idle = _recovery.require_idle
write_json = _recovery.write_json

SOURCE = Path(__file__).with_name("experiment.yaml")
LOCAL_AGENT = "recovery@s1"
GLOBAL_AGENT = "global-recovery@s1"


def warm_global(host: str, model: str, evidence: Path, transport=None) -> None:
    """One small request per experiment, before topology creation; never a repair."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply OK."}],
        "stream": False,
        "think": False,
        "keep_alive": -1,
        "options": {"num_predict": 2, "temperature": 0},
    }
    write_json(evidence / "llm-warmup-request.json", payload)
    try:
        response = (transport or HttpxJsonTransport()).post_json(
            host.rstrip("/") + "/api/chat",
            payload,
            headers={"Content-Type": "application/json"},
            timeout_seconds=120,
            max_response_bytes=1_048_576,
        )
        write_json(evidence / "llm-warmup-response.json", response)
        if (
            not isinstance(response, dict)
            or response.get("error")
            or response.get("done") is not True
            or not isinstance(response.get("message"), dict)
        ):
            raise RuntimeError("Ollama warm-up did not complete successfully")
    except Exception as error:
        write_json(evidence / "llm-warmup-error.json", {"error": str(error)})
        raise


def handoff_intent(state: dict, history: list[dict]) -> str:
    """Allowlist model-facing context: never forward evaluator labels or fault.json."""
    return json.dumps(
        {
            "incident": "Local recovery requested escalation; diagnose and propose a repair.",
            "diagnostics": state,
            "localDecisionHistory": [
                {
                    key: item.get(key)
                    for key in ("selectedChoice", "status", "observedAt")
                }
                for item in history
            ],
        }
    )


def global_repair_succeeded(result) -> bool:
    return (
        result is not None
        and result.status.value == "succeeded"
        and len(result.action_results) == 1
        and result.action_results[0].status.value == "succeeded"
    )


def run_trial(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        raise PermissionError("live escalation requires root; no inference was sent")
    links = {link["ifname"] for link in json.loads(command("ip", "-j", "link"))}
    if (
        links & {"s1", "s1-eth1", "s1-eth2", "h1-eth0", "h2-eth0"}
        or "s1" in command("ovs-vsctl", "list-br").splitlines()
    ):
        raise RuntimeError("scenario resource names are already in use")
    root = args.evidence.resolve()
    root.mkdir(parents=True, exist_ok=False)
    require_idle(args.endpoint, root / "remote-before.json")
    document = yaml.safe_load(SOURCE.read_text())
    scenario = document["decisionScenarios"][0]
    choice_ids = {choice["id"] for choice in scenario["choices"]}
    if args.confidence_threshold is not None and "escalate" not in choice_ids:
        raise ValueError("confidence threshold requires an escalate choice")
    gate = ConfidenceEscalationGate(
        threshold=args.confidence_threshold, choices=choice_ids
    )
    local, global_blueprint = document["blueprints"]
    local["model"]["name"] = args.model_name
    local["model"]["parameters"] = model_parameters(args)
    global_blueprint["model"]["name"] = args.llm_model
    global_blueprint["model"]["parameters"]["host"] = args.llm_host
    source = root / "experiment.yaml"
    source.write_text(yaml.safe_dump(document, sort_keys=False))
    plan = compile_experiment(source)
    save_deployment_plan(plan, root / "plan.json")
    write_json(
        root / "evaluation-contract.json",
        {
            "expectedChoiceBeforeFault": "no_action",
            "expectedChoiceAfterFault": "escalate",
            "faultPort": args.fault_port,
            "handoff": "runner-mediated; only a successful explicit escalation triggers it",
            "globalInvocationsAllowed": 1,
            "globalTimeoutSeconds": 120,
            "localDecisionDeadlineSeconds": 5,
            "maxPostFaultChecks": args.max_checks,
            "warmupRequests": args.warmup_requests,
            "confidenceThreshold": args.confidence_threshold,
            "confidenceGate": "post-fault local decisions only; a choice below the threshold is replaced by escalate before action execution, with the raw response and gate record retained",
            "success": "healthy warm-up, explicit escalation, one successful authorized global repair, three successful bidirectional probe rounds, verified cleanup",
        },
    )
    harness = RecoveryHarness(root)

    class Observations(MininetOVSObservations):
        def __init__(self, plan, network):
            super().__init__(plan, network)
            harness.attach(network)

        def collect(self, query):
            with harness.observation_lock:
                return compact_observation(query, super().collect(query))

    substrate = MininetOVSRuntime(observation_factory=Observations)
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    trial = MonitorTrial(args.warmup_requests, args.max_checks, "escalate")
    warmup_finished, local_finished, global_finished = Event(), Event(), Event()
    evaluations: list[dict[str, Any]] = []
    state: dict[str, Any] = {}
    global_result = None

    def retain(record):
        nonlocal state, global_result
        result = record.result
        with (root / "invocations.jsonl").open("a") as stream:
            stream.write(
                json.dumps(record.model_dump(mode="json", by_alias=True)) + "\n"
            )
        if record.agent_id == GLOBAL_AGENT:
            global_result = result
            write_json(
                root / "global-result.json",
                result.model_dump(mode="json", by_alias=True),
            )
            global_finished.set()
            return
        decision = (
            result.response.metadata.get("executionRecord", {})
            .get("runtime", {})
            .get("boundedDecision", {})
            if result.response is not None
            else {}
        )
        observed_at = result.started_at
        request_state = decision.get("request", {}).get("state")
        if request_state:
            state = json.loads(request_state)
            ports = state["switch"]["diagnosis"].get("portState", {}).get("value", {})
            if isinstance(ports, dict) and isinstance(ports.get("s1"), dict):
                timestamps = [
                    datetime.fromisoformat(port["observedAt"])
                    for port in ports["s1"].get("ports", [])
                    if port.get("observedAt")
                ]
                if timestamps:
                    observed_at = min(timestamps)
        evaluation = trial.observe(
            decision.get("selectedChoice"), result.status.value, observed_at
        )
        response = decision.get("response") if isinstance(decision, dict) else None
        native = response if isinstance(response, dict) else {}
        answers = native.get("answers")
        answer = (
            answers.get("next_action")
            if isinstance(answers, dict)
            and isinstance(answers.get("next_action"), dict)
            else {}
        )
        probabilities = answer.get("probabilities")
        confidence = answer.get("confidence")
        choice_probability = (
            probabilities.get(answer.get("choice"))
            if isinstance(probabilities, dict)
            else None
        )
        gate_record = native.get("gate")
        gate_record = gate_record if isinstance(gate_record, dict) else None
        if gate_record:
            confidence = gate_record.get("originalConfidence", confidence)
            choice_probability = gate_record.get(
                "originalProbability", choice_probability
            )
        evaluation.update(
            {
                "invocationId": result.invocation_id,
                "observedAt": observed_at.isoformat(),
                "implementation": "runner-mediated-escalation"
                if evaluation["escalationRequested"]
                else evaluation["implementation"],
                "modelChoice": (
                    gate_record.get("originalChoice")
                    if gate_record
                    else answer.get("choice")
                )
                or decision.get("selectedChoice"),
                "confidence": confidence,
                "choiceProbability": choice_probability,
            }
        )
        if gate_record:
            evaluation["confidenceGate"] = gate_record
        evaluations.append(evaluation)
        with (root / "evaluations.jsonl").open("a") as stream:
            stream.write(json.dumps(evaluation) + "\n")
        if trial.done.is_set():
            runtime.pause_agent(LOCAL_AGENT)
            local_finished.set()
        elif trial.ready.is_set() and trial.fault_at is None:
            runtime.pause_agent(LOCAL_AGENT)
            warmup_finished.set()

    with closing(SQLiteRunLedger(root / "ledger.sqlite3")) as ledger:
        runtime = ExperimentRuntime(
            plan,
            substrate,
            registries,
            ledger=ledger,
            invocation_listener=retain,
            decision_transport=RecordingTransport(root, gate=gate),
        )
        recovery: dict[str, Any] = {"recovered": False}
        stop_reason = "not-started"
        started = remote_idle = escalation_executed = False
        report = None
        try:
            runtime.start()
            started = True
            if not warmup_finished.wait(args.ready_timeout):
                stop_reason = "warmup-timeout"
            elif not trial.warmup_succeeded:
                stop_reason = "warmup-failed"
            else:
                readiness = harness.probe_round()
                write_json(root / "ready.json", readiness)
                if not readiness["success"]:
                    stop_reason = "readiness-failed"
                else:
                    with trial.lock, harness.observation_lock:
                        harness.inject(args.fault_port)
                        trial.fault_at = harness.fault_at
                        gate.enabled = True
                    runtime.resume_agent(LOCAL_AGENT)
                    if not local_finished.wait(args.trial_timeout):
                        stop_reason = "local-trial-timeout"
                    elif not trial.checks[-1]["correct"]:
                        stop_reason = "no-valid-escalation"
                    else:
                        # The callback has paused local admission before signalling.
                        intent = handoff_intent(state, evaluations)
                        write_json(root / "handoff.json", json.loads(intent))
                        event = runtime.submit_intent(
                            GLOBAL_AGENT, intent, source="runner-escalation"
                        )
                        escalation_executed = True
                        write_json(
                            root / "handoff-event.json",
                            event.model_dump(mode="json", by_alias=True),
                        )
                        if not global_finished.wait(120):
                            stop_reason = "global-timeout"
                        elif not global_repair_succeeded(global_result):
                            stop_reason = "global-repair-failed"
                        else:
                            recovery = harness.verify_recovery()
                            write_json(root / "recovery.json", recovery)
                            stop_reason = (
                                "recovered"
                                if recovery["recovered"]
                                else "recovery-verification-failed"
                            )
        except KeyboardInterrupt, EOFError:
            stop_reason = "operator-stop"
        except Exception as error:
            stop_reason = "run-error"
            write_json(
                root / "failure.json",
                {"type": type(error).__name__, "error": str(error)},
            )
        finally:
            if started:
                report = runtime.stop(drain=False, timeout_seconds=30)
                write_json(
                    root / "runtime-report.json",
                    report.model_dump(mode="json", by_alias=True),
                )
            else:
                write_json(
                    root / "startup-cleanup.json",
                    runtime.cleanup.model_dump(mode="json", by_alias=True),
                )
            cleanup = harness.verify_cleanup()
            write_json(root / "cleanup.json", cleanup)
            try:
                require_idle(args.endpoint, root / "remote-after.json")
                remote_idle = True
            except Exception as error:
                write_json(root / "remote-after-error.json", {"error": str(error)})
        healthy_stop = (
            report is not None
            and report.operation.value == "succeeded"
            and all(
                issue.code == "experiment.cleanup.unverified" for issue in report.issues
            )
        )
        final_check = trial.checks[-1] if trial.checks else {}
        summary = {
            "passed": stop_reason == "recovered"
            and trial.warmup_succeeded
            and healthy_stop
            and cleanup["verified"]
            and remote_idle,
            "stopReason": stop_reason,
            "escalationRequested": bool(
                trial.checks and trial.checks[-1]["escalationRequested"]
            ),
            "escalationExecuted": escalation_executed,
            "globalRepairSucceeded": global_repair_succeeded(global_result),
            "confidenceThreshold": args.confidence_threshold,
            "confidenceGateApplied": bool(final_check.get("confidenceGate")),
            "modelChoice": final_check.get("modelChoice")
            or final_check.get("selectedChoice"),
            "confidence": final_check.get("confidence"),
            "choiceProbability": final_check.get("choiceProbability"),
            "warmupSucceeded": trial.warmup_succeeded,
            "warmupEvaluations": trial.warmup_checks,
            "postFaultChecks": trial.checks,
            "safeToContinue": healthy_stop
            and cleanup["verified"]
            and remote_idle
            and stop_reason not in {"global-timeout", "global-repair-failed"}
            and (not escalation_executed or global_repair_succeeded(global_result)),
            "remoteIdleAfterStop": remote_idle,
            "cleanup": cleanup,
            **recovery,
        }
        write_json(root / "summary.json", summary)
        return 0 if summary["passed"] else 1


def run(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        raise PermissionError("live escalation requires root; no inference was sent")
    root = args.evidence.resolve()
    root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    try:
        warm_global(args.llm_host, args.llm_model, root)
        for index in range(1, args.runs + 1):
            print(f"Starting escalation trial {index}/{args.runs}.", flush=True)
            trial_args = argparse.Namespace(
                **{**vars(args), "evidence": root / f"run-{index:03d}"}
            )
            exit_code = run_trial(trial_args)
            summary = json.loads((trial_args.evidence / "summary.json").read_text())
            results.append(
                {
                    **summary,
                    "run": index,
                    "passed": exit_code == 0 and summary["passed"],
                }
            )
            print(json.dumps(results[-1]), flush=True)
            write_json(root / "summary.json", {"passed": False, "runs": results})
            if (
                not summary["safeToContinue"]
                or summary["stopReason"] == "operator-stop"
            ):
                break
    except (Exception, KeyboardInterrupt) as error:
        write_json(
            root / "failure.json", {"type": type(error).__name__, "error": str(error)}
        )
        write_json(root / "summary.json", {"passed": False, "runs": results})
        print(
            f"Experiment stopped: {type(error).__name__}: {error}\n"
            f"Failure evidence: {root / 'failure.json'}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    passed = len(results) == args.runs and all(result["passed"] for result in results)
    write_json(root / "summary.json", {"passed": passed, "runs": results})
    return 0 if passed else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://10.10.10.152:9931")
    parser.add_argument("--model-name", default="h2oai/h2o-lightning-4b")
    for name in ("repository", "revision", "artifact", "sha256"):
        parser.add_argument(f"--model-{name}")
    parser.add_argument("--skip-model-verification", action="store_true")
    parser.add_argument("--llm-host", default="http://10.10.10.152:11434")
    parser.add_argument("--llm-model", default="qwen3.5:latest")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--warmup-requests", type=int, default=3)
    parser.add_argument("--max-checks", type=int, default=3)
    parser.add_argument("--ready-timeout", type=float, default=60)
    parser.add_argument("--trial-timeout", type=float, default=30)
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=None,
        help=(
            "Post-fault local System One confidence below this value is replaced "
            "by escalate before action execution (0..1; disabled by default)"
        ),
    )
    parser.add_argument(
        "--fault-port", choices=["s1-eth1", "s1-eth2"], default="s1-eth1"
    )
    args = parser.parse_args(argv)
    if (
        args.confidence_threshold is not None
        and not 0 <= args.confidence_threshold <= 1
    ):
        parser.error("--confidence-threshold must be between 0 and 1")
    if args.confidence_threshold is not None:
        choices = {
            choice["id"]
            for choice in yaml.safe_load(SOURCE.read_text())["decisionScenarios"][0][
                "choices"
            ]
        }
        if "escalate" not in choices:
            parser.error("--confidence-threshold requires an escalate choice")
    if (
        min(
            args.runs,
            args.warmup_requests,
            args.max_checks,
            args.ready_timeout,
            args.trial_timeout,
        )
        <= 0
    ):
        parser.error("timeouts and counts must be positive")
    try:
        model_parameters(args)
    except ValueError as error:
        parser.error(str(error))
    return args


def main() -> int:
    return run(parse_args())
