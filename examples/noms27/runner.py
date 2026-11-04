"""Automatically repeat fresh single-switch recovery trials after warm-up requests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from time import monotonic
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

SOURCE = Path(__file__).with_name("experiment.yaml")
EXPECTED_CHOICE = "enable_access_port"
STUB_CHOICES = {"repair_neighbor_entry", "escalate"}


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def command(*arguments: str) -> str:
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=5, check=False
    )
    if result.returncode:
        raise RuntimeError(f"{arguments!r}: {result.stderr.strip()}")
    return result.stdout


def require_idle(endpoint: str, evidence: Path) -> None:
    slots = HttpxJsonTransport().get_json(
        endpoint.rstrip("/") + "/slots",
        headers={"Accept": "application/json"},
        timeout_seconds=2,
        max_response_bytes=1_048_576,
    )
    write_json(evidence, slots)
    if (
        not isinstance(slots, list)
        or not slots
        or any(
            not isinstance(slot, dict) or slot.get("is_processing") is not False
            for slot in slots
        )
    ):
        raise RuntimeError("model service is busy or its idle state is unknown")


def score_choice(
    selected: str | None, expected: str = EXPECTED_CHOICE
) -> dict[str, Any]:
    """Ground truth stays in the evaluator, never in the model's state."""
    return {
        "expectedChoice": expected,
        "selectedChoice": selected,
        "selectionCorrect": selected == expected,
        "implementation": "stub"
        if selected in STUB_CHOICES
        else "fixed-flow"
        if selected in {"restore_forward_flow", "restore_reverse_flow"}
        else "switch-port-enable"
        if selected in {"enable_access_port", "enable_peer_port"}
        else "no-action"
        if selected == "no_action"
        else "unavailable",
        "escalationRequested": selected == "escalate",
        "escalationExecuted": False,
        "unnecessaryPolicySelections": int(
            selected not in {None, expected, "no_action"}
        ),
    }


class RecordingTransport:
    """Retain choices even when execution subsequently refuses the action."""

    def __init__(self, evidence: Path) -> None:
        self.transport = HttpxJsonTransport()
        self.evidence = evidence
        self.response: Any = None
        self.measured_requests = 0

    def get_json(self, url, **kwargs):
        return self.transport.get_json(url, **kwargs)

    def post_json(self, url, payload, **kwargs):
        measured = payload.get("state") != "{}"
        if measured:
            self.measured_requests += 1
            request_id = kwargs.get("request_id")
            if (
                not isinstance(request_id, str)
                or not request_id.replace("-", "").isalnum()
            ):
                raise ValueError("measured requests require a safe invocation identity")
            request_root = self.evidence / "native" / request_id
            request_root.mkdir(parents=True, exist_ok=False)
            write_json(request_root / "request.json", payload)
        response = self.transport.post_json(url, payload, **kwargs)
        if measured:
            self.response = response
            write_json(request_root / "response.json", response)
        return response


class MonitorTrial:
    """Evaluator-only lifecycle; it never supplies labels to the model."""

    def __init__(self, warmup_requests: int, max_checks: int, expected: str) -> None:
        self.warmup_requests = warmup_requests
        self.max_checks = max_checks
        self.expected = expected
        self.lock = Lock()
        self.ready = Event()
        self.done = Event()
        self.fault_at: datetime | None = None
        self.warmup_checks: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []

    @property
    def warmup_succeeded(self) -> bool:
        return len(self.warmup_checks) == self.warmup_requests and all(
            check["correct"] for check in self.warmup_checks
        )

    def observe(
        self, choice: str | None, status: str, observed_at: datetime | None
    ) -> dict[str, Any]:
        with self.lock:
            after_fault = (
                self.fault_at is not None
                and observed_at is not None
                and observed_at >= self.fault_at
            )
            expected = self.expected if after_fault else "no_action"
            evaluation = {
                **score_choice(choice, expected),
                "phase": "post-fault"
                if after_fault
                else "warmup"
                if self.fault_at is None
                else "in-flight-before-fault",
                "status": status,
            }
            evaluation["correct"] = (
                evaluation["selectionCorrect"] and status == "succeeded"
            )
            if self.fault_at is None:
                if len(self.warmup_checks) < self.warmup_requests:
                    self.warmup_checks.append(evaluation)
                if len(self.warmup_checks) == self.warmup_requests:
                    self.ready.set()
            elif after_fault and not self.done.is_set():
                self.checks.append(evaluation)
                if (
                    choice not in {None, "no_action"}
                    or len(self.checks) >= self.max_checks
                ):
                    self.done.set()
            return evaluation


class RecoveryHarness:
    def __init__(self, evidence: Path) -> None:
        self.evidence = evidence
        self.network: Any = None
        self.fault_tick: float | None = None
        self.fault_at: datetime | None = None
        self.host_processes: dict[int, str] = {}
        self.observation_lock = Lock()

    @staticmethod
    def process_identity(pid: int) -> str | None:
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        except FileNotFoundError:
            return None

    def attach(self, network: Any) -> None:
        self.network = network
        for name, peer_ip, peer_mac in (
            ("h1", "10.0.0.2", "02:00:00:00:00:02"),
            ("h2", "10.0.0.1", "02:00:00:00:00:01"),
        ):
            host = network.get(name)
            identity = self.process_identity(host.pid)
            if identity is None:
                raise RuntimeError("host process identity unavailable")
            self.host_processes[host.pid] = identity
            _, error, status = host.pexec(
                [
                    "ip",
                    "neigh",
                    "replace",
                    peer_ip,
                    "lladdr",
                    peer_mac,
                    "nud",
                    "permanent",
                    "dev",
                    f"{name}-eth0",
                ]
            )
            if status:
                raise RuntimeError(f"neighbor setup failed: {error}")
        # Only the freshly deployed, preflight-checked scenario bridge is modified.
        command("ovs-ofctl", "-O", "OpenFlow13", "del-flows", "s1")
        for expression in (
            "cookie=0x5100,table=0,priority=0,actions=drop",
            "cookie=0x5101,table=0,priority=100,ip,in_port=1,nw_src=10.0.0.1,nw_dst=10.0.0.2,actions=output:2",
            "cookie=0x5102,table=0,priority=100,ip,in_port=2,nw_src=10.0.0.2,nw_dst=10.0.0.1,actions=output:1",
        ):
            command("ovs-ofctl", "-O", "OpenFlow13", "add-flow", "s1", expression)
        baseline = self.probe_round()
        write_json(self.evidence / "baseline.json", baseline)
        if not baseline["success"]:
            raise RuntimeError("healthy baseline connectivity was not established")

    def probe_round(self) -> dict[str, Any]:
        probes = []
        for source, destination in (("h1", "10.0.0.2"), ("h2", "10.0.0.1")):
            process = self.network.get(source).popen(
                ["ping", "-n", "-c", "1", "-W", "1", destination],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                output, error = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                output, error = process.communicate(timeout=2)
                raise RuntimeError("probe did not terminate within its deadline")
            probes.append(
                {
                    "source": source,
                    "destination": destination,
                    "status": process.returncode,
                    "stdout": output.decode(),
                    "stderr": error.decode(),
                }
            )
        return {
            "tick": monotonic(),
            "success": all(p["status"] == 0 for p in probes),
            "probes": probes,
        }

    def inject(self, port: str = "s1-eth1") -> None:
        if port not in {"s1-eth1", "s1-eth2"}:
            raise ValueError("fault port must belong to the scenario switch")
        issued_at = datetime.now(UTC)
        issued_tick = monotonic()
        command("ip", "link", "set", "dev", port, "down")
        completed_at = datetime.now(UTC)
        completed_tick = monotonic()
        state = json.loads(command("ip", "-j", "link", "show", "dev", port))
        if (
            not isinstance(state, list)
            or len(state) != 1
            or state[0].get("ifname") != port
            or "UP" in state[0].get("flags", ["UP"])
        ):
            raise RuntimeError("administrative port-down state was not confirmed")
        self.fault_at = datetime.now(UTC)
        self.fault_tick = monotonic()
        write_json(
            self.evidence / "fault.json",
            {
                "fault": "switch-port-down",
                "port": port,
                "commandIssuedAt": issued_at.isoformat(),
                "commandIssuedTick": issued_tick,
                "commandCompletedAt": completed_at.isoformat(),
                "commandCompletedTick": completed_tick,
                "faultConfirmedAt": self.fault_at.isoformat(),
                "faultTick": self.fault_tick,
                "clockDomain": "process-monotonic",
                "interfaceState": state,
            },
        )

    def verify_recovery(self) -> dict[str, Any]:
        rounds = [self.probe_round() for _ in range(3)]
        flows = command("ovs-ofctl", "-O", "OpenFlow13", "dump-flows", "s1")
        return {
            "rounds": rounds,
            "recovered": all(r["success"] for r in rounds),
            "flows": flows,
            "endToEndRecoverySeconds": rounds[-1]["tick"] - self.fault_tick
            if all(r["success"] for r in rounds) and self.fault_tick is not None
            else None,
        }

    def verify_cleanup(self) -> dict[str, Any]:
        links = {link["ifname"] for link in json.loads(command("ip", "-j", "link"))}
        bridges = command("ovs-vsctl", "list-br").splitlines()
        remaining = sorted(links & {"s1", "s1-eth1", "s1-eth2", "h1-eth0", "h2-eth0"})
        pids = [
            pid
            for pid, identity in self.host_processes.items()
            if self.process_identity(pid) == identity
        ]
        return {
            "verified": not remaining and "s1" not in bridges and not pids,
            "remainingInterfaces": remaining,
            "bridgePresent": "s1" in bridges,
            "remainingHostProcesses": pids,
            "scope": "scenario bridge, interfaces, and captured host process identities",
        }


def model_parameters(args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_model_verification:
        return {"endpoint": args.endpoint, "verifyIdentity": False}
    identity = {
        "repository": args.model_repository,
        "revision": args.model_revision,
        "artifact": args.model_artifact,
        "sha256": args.model_sha256,
    }
    if not all(identity.values()):
        raise ValueError(
            "provide model identity fields or explicitly use --skip-model-verification"
        )
    return {"endpoint": args.endpoint, "expectedIdentity": identity}


def run_trial(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        raise PermissionError("live recovery requires root; no inference was sent")
    # Do not attach to or clean an existing experiment's resources.
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
    document["substrate"]["driver"] = "mininet-ovs"
    model = document["blueprints"][0]["model"]
    model["name"] = args.model_name
    model["parameters"] = model_parameters(args)
    expected = (
        "enable_access_port" if args.fault_port == "s1-eth1" else "enable_peer_port"
    )
    write_json(
        root / "evaluation-contract.json",
        {
            "expectedChoiceAfterFault": expected,
            "expectedChoiceBeforeFault": "no_action",
            "choiceDescriptionVersion": "applicability-conditions-v1",
            "stubChoices": sorted(STUB_CHOICES),
            "escalationExecuted": False,
            "maxPostFaultChecks": args.max_checks,
            "warmupRequests": args.warmup_requests,
            "warmupPolicy": "fixed number of healthy diagnostic decision attempts; no retry-until-success; abort fault injection if any warm-up attempt fails",
            "faultPort": args.fault_port,
            "modelIdentityVerification": "skipped"
            if args.skip_model_verification
            else "requested",
            "warmth": "startup interface probe plus declared warm-up attempts per fresh topology; not cold",
            "artifactHashSource": "operator declaration; not independently verified",
            "timing": "confirmed administrative link-down through three successful bidirectional probe rounds",
            "faultPreparation": "automatic after healthy warm-up readiness, outside diagnostic collection; pre-fault snapshots do not count toward the check limit",
        },
    )
    source = root / "experiment.yaml"
    source.write_text(yaml.safe_dump(document, sort_keys=False))
    plan = compile_experiment(source)
    save_deployment_plan(plan, root / "plan.json")
    harness = RecoveryHarness(root)

    class Observations(MininetOVSObservations):
        def __init__(self, plan, network):
            super().__init__(plan, network)
            harness.attach(network)

        def collect(self, query):
            # Do not let a port snapshot straddle command/confirmation timestamps.
            # This lock contains no failed-ping probes or model inference.
            with harness.observation_lock:
                return super().collect(query)

    substrate = MininetOVSRuntime(observation_factory=Observations)
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    trial = MonitorTrial(args.warmup_requests, args.max_checks, expected)
    warmup_finished = Event()
    evaluations: list[dict[str, Any]] = []
    transport = RecordingTransport(root)

    def retain(record):
        result = record.result
        decision = (
            result.response.metadata.get("executionRecord", {})
            .get("runtime", {})
            .get("boundedDecision", {})
            if result.response is not None
            else {}
        )
        choice = decision.get("selectedChoice")
        if choice not in {
            item["id"] for item in document["decisionScenarios"][0]["choices"]
        }:
            choice = None
        observed_at = result.started_at
        if decision.get("request", {}).get("state"):
            state = json.loads(decision["request"]["state"])
            ports = state["switch"]["diagnosis"].get("portState", {}).get("value", {})
            if isinstance(ports, dict) and isinstance(ports.get("s1"), dict):
                timestamps = [
                    datetime.fromisoformat(port["observedAt"])
                    for port in ports.get("s1", {}).get("ports", [])
                    if port.get("observedAt")
                ]
                if timestamps:
                    observed_at = min(timestamps)
        evaluation = trial.observe(choice, result.status.value, observed_at)
        native_answers = decision.get("response", {}).get("answers")
        answer = (
            native_answers.get("next_action", {})
            if isinstance(native_answers, dict)
            else {}
        )
        probabilities = (
            answer.get("probabilities", {}) if isinstance(answer, dict) else {}
        )
        evaluation.update(
            {
                "invocationId": result.invocation_id,
                "observedAt": observed_at.isoformat(),
                "tokenUsage": decision.get("response", {}).get("usage"),
                "confidence": answer.get("confidence")
                if choice is not None and isinstance(answer, dict)
                else None,
                "choiceProbability": probabilities.get(choice)
                if choice is not None and isinstance(probabilities, dict)
                else None,
                "unnecessaryActions": len(result.action_results)
                if not evaluation["correct"]
                else 0,
            }
        )
        evaluations.append(evaluation)
        with (root / "invocations.jsonl").open("a") as stream:
            stream.write(
                json.dumps(record.model_dump(mode="json", by_alias=True)) + "\n"
            )
        with (root / "evaluations.jsonl").open("a") as stream:
            stream.write(json.dumps(evaluation) + "\n")
        print(json.dumps(evaluation), flush=True)
        if trial.done.is_set():
            runtime.pause_agent(record.agent_id)
        elif trial.ready.is_set() and trial.fault_at is None:
            # Signal only after pausing, so the main thread cannot resume and
            # then be paused again by this completed warm-up callback.
            runtime.pause_agent(record.agent_id)
            warmup_finished.set()

    with closing(SQLiteRunLedger(root / "ledger.sqlite3")) as ledger:
        runtime = ExperimentRuntime(
            plan,
            substrate,
            registries,
            ledger=ledger,
            invocation_listener=retain,
            decision_transport=transport,
        )
        recovery: dict[str, Any] = {"recovered": False, "endToEndRecoverySeconds": None}
        stop_reason = "not-started"
        remote_idle = False
        started = False
        try:
            runtime.start()
            started = True
            print(
                f"Warming up with {args.warmup_requests} healthy diagnostic requests.",
                flush=True,
            )
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
                    print(
                        f"Warm-up complete; automatically bringing {args.fault_port} down.",
                        flush=True,
                    )
                    with trial.lock, harness.observation_lock:
                        harness.inject(args.fault_port)
                        trial.fault_at = harness.fault_at
                    runtime.resume_agent(runtime.agent_ids[0])
                    print(
                        f"Fault confirmed at {harness.fault_at.isoformat()}; observing up to {args.max_checks} checks.",
                        flush=True,
                    )
                    stop_reason = (
                        "check-limit-or-policy-selected"
                        if trial.done.wait(args.trial_timeout)
                        else "trial-timeout"
                    )
            if trial.done.is_set():
                recovery = harness.verify_recovery()
        except KeyboardInterrupt, EOFError:
            stop_reason = "operator-stop"
        except BaseException as error:
            write_json(
                root / "failure.json",
                {"type": type(error).__name__, "error": str(error)},
            )
            raise
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
    if not started:
        write_json(
            root / "summary.json",
            {"passed": False, "stopReason": stop_reason, "cleanup": cleanup},
        )
        return 1
    final_check = trial.checks[-1] if trial.checks else {}
    summary = {
        **score_choice(final_check.get("selectedChoice"), expected),
        **recovery,
        "stopReason": stop_reason,
        "postFaultChecks": trial.checks,
        "warmupRequestsRequested": args.warmup_requests,
        "warmupAttemptsCompleted": len(trial.warmup_checks),
        "warmupSucceeded": trial.warmup_succeeded,
        "warmupEvaluations": trial.warmup_checks,
        "incorrectPostFaultChecks": sum(not check["correct"] for check in trial.checks),
        "firstPostFaultCheckCorrect": trial.checks[0]["correct"]
        if trial.checks
        else None,
        "invocationStatus": final_check.get("status", "unavailable"),
        "confidence": final_check.get("confidence"),
        "choiceProbability": final_check.get("choiceProbability"),
        "tokenUsageByInvocation": [item["tokenUsage"] for item in evaluations],
        "unnecessaryActions": sum(item["unnecessaryActions"] for item in evaluations),
        "remoteIdleAfterStop": remote_idle,
        "cleanup": cleanup,
        "operation": report.operation.value,
        "safeToContinue": cleanup["verified"]
        and remote_idle
        and all(
            issue.phase == "cleanup" and issue.code == "experiment.cleanup.unverified"
            for issue in report.issues
        ),
    }
    summary["passed"] = (
        summary["selectionCorrect"]
        and summary["recovered"]
        and summary["invocationStatus"] == "succeeded"
        and cleanup["verified"]
        and remote_idle
        and report.operation.value == "succeeded"
        and all(
            issue.code == "experiment.cleanup.unverified" for issue in report.issues
        )
    )
    write_json(root / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


def run(args: argparse.Namespace) -> int:
    """Keep all outcomes; stop the batch if another deployment is not safe."""
    if os.geteuid() != 0:
        raise PermissionError("live recovery requires root; no inference was sent")
    root = args.evidence.resolve()
    root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    stop_reason = "completed"
    finished = False

    def retain_batch() -> dict[str, Any]:
        summary = {
            "status": "running"
            if not finished
            else "completed"
            if stop_reason == "completed"
            else "stopped",
            "runsRequested": args.runs,
            "attemptedRuns": len(results),
            "completedRuns": sum(item["status"] == "completed" for item in results),
            "passedRuns": sum(item["passed"] for item in results),
            "failedRuns": sum(
                item["status"] == "completed" and not item["passed"] for item in results
            ),
            "errorRuns": sum(item["status"] == "error" for item in results),
            "interruptedRuns": sum(item["status"] == "interrupted" for item in results),
            "warmupRequestsPerRun": args.warmup_requests,
            "stoppedEarly": finished and len(results) < args.runs,
            "stopReason": stop_reason if finished else None,
            "runs": results,
            "passed": finished
            and stop_reason == "completed"
            and len(results) == args.runs
            and all(item["passed"] for item in results),
        }
        write_json(root / "summary.json", summary)
        return summary

    write_json(
        root / "batch-config.json",
        {
            "runs": args.runs,
            "warmupRequestsPerRun": args.warmup_requests,
            "maxPostFaultChecks": args.max_checks,
            "faultPort": args.fault_port,
            "freshDeploymentPerRun": True,
            "automaticFaultInjection": True,
        },
    )
    retain_batch()
    for index in range(1, args.runs + 1):
        directory = f"run-{index:03d}"
        trial_args = argparse.Namespace(**{**vars(args), "evidence": root / directory})
        print(f"Starting run {index}/{args.runs}: {directory}", flush=True)
        result: dict[str, Any] = {
            "run": index,
            "directory": directory,
            "passed": False,
            "status": "error",
        }
        try:
            exit_code = run_trial(trial_args)
            trial_summary = json.loads(
                (trial_args.evidence / "summary.json").read_text()
            )
            result.update(
                {
                    "status": "completed",
                    "passed": exit_code == 0 and trial_summary.get("passed") is True,
                    **{
                        name: trial_summary.get(name)
                        for name in (
                            "stopReason",
                            "selectedChoice",
                            "selectionCorrect",
                            "firstPostFaultCheckCorrect",
                            "endToEndRecoverySeconds",
                            "confidence",
                            "choiceProbability",
                            "warmupAttemptsCompleted",
                            "warmupSucceeded",
                            "safeToContinue",
                            "operation",
                        )
                    },
                }
            )
            if trial_summary.get("stopReason") == "operator-stop":
                stop_reason = "operator-stop"
                result["status"] = "interrupted"
            elif trial_summary.get("safeToContinue") is not True:
                stop_reason = "unsafe-to-continue"
        except KeyboardInterrupt:
            stop_reason = "operator-stop"
            result["status"] = "interrupted"
            result["error"] = "interrupted"
        except Exception as error:
            stop_reason = "run-error"
            result["error"] = {"type": type(error).__name__, "message": str(error)}
        results.append(result)
        retain_batch()
        if stop_reason != "completed":
            break
    finished = True
    summary = retain_batch()
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://10.10.10.152:9931")
    parser.add_argument("--model-name", default="h2oai/h2o-lightning-4b")
    parser.add_argument("--model-repository")
    parser.add_argument("--model-revision")
    parser.add_argument("--model-artifact")
    parser.add_argument("--model-sha256")
    parser.add_argument(
        "--skip-model-verification",
        action="store_true",
        help="Skip model identity matching, not health, native interface, or output validation",
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--trial-timeout", type=float, default=30)
    parser.add_argument("--ready-timeout", type=float, default=60)
    parser.add_argument(
        "--runs", type=int, default=1, help="Number of fresh topology trials"
    )
    parser.add_argument(
        "--warmup-requests",
        "--settle-checks",
        dest="warmup_requests",
        type=int,
        default=3,
        help="Healthy warm-up decision attempts per run (default: 3)",
    )
    parser.add_argument("--max-checks", type=int, default=3)
    parser.add_argument(
        "--fault-port", choices=["s1-eth1", "s1-eth2"], default="s1-eth1"
    )
    args = parser.parse_args(argv)
    if (
        min(
            args.trial_timeout,
            args.ready_timeout,
            args.warmup_requests,
            args.max_checks,
            args.runs,
        )
        <= 0
    ):
        parser.error("timeouts and check counts must be positive")
    try:
        model_parameters(args)
    except ValueError as error:
        parser.error(str(error))
    return args


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
