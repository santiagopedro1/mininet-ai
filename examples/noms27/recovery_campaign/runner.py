"""One bounded S1 incident; no escalation, fault reversal or automatic failover."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import platform
import subprocess
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

NS = 1_000_000_000
BIN = NS // 10
CATALOGUE = {
    "bypass": {
        "switch": "s1",
        "flow": "cookie=0x3101,priority=300,idle_timeout=0,hard_timeout=0,ip,in_port=1,nw_src=10.0.0.1,nw_dst=10.0.0.2,actions=output:3",
    },
    "no_action": None,
    "unable": None,
}
INSTRUCTIONS = (
    "Use only current s1 port/flow diagnostics. Choose bypass to redirect active "
    "h1-to-h2 traffic to port 3 when primary port 2 is down; downstream forwarding "
    "is preinstalled. Never enable a port, reverse an outage, change traffic, or "
    "construct a command. Choose no_action if healthy, unable if no permitted repair. "
    "Confidence is diagnostic only. Select exactly one frozen catalogue entry."
)
CRITERIA = {
    "bypass": "Primary port 2 is down and surviving port 3 can carry active traffic.",
    "no_action": "Current local state needs no repair.",
    "unable": "Cannot resolve using the permitted local catalogue or evidence is unavailable.",
}


class CampaignConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    trial_id: str = Field(min_length=1)
    treatment: Literal["s1_only"]
    scenario: Literal["diamond_s1_s2_link"]
    deadline_seconds: float = Field(gt=0)
    s1_cap_seconds: float = Field(gt=0)
    bottleneck_mbps: float = Field(gt=0)
    seed: int
    purpose: Literal["mechanism_validation", "live_feasibility"] = (
        "mechanism_validation"
    )
    budget_status: Literal["development_unapproved"] = "development_unapproved"
    payload_bytes: Literal[1200] = 1200
    load_fraction: float = Field(default=0.1, ge=0.1, le=0.1)
    delivery_deadline_ms: Literal[100] = 100
    bin_ms: Literal[100] = 100
    sustained_seconds: Literal[1] = 1
    timely_fraction: float = Field(default=0.99, ge=0.99, le=0.99)
    baseline_seconds: Literal[5] = 5
    post_deadline_seconds: Literal[5] = 5
    endpoint: str = "http://127.0.0.1:8080"
    model_name: str = "unconfigured"
    expected_identity: dict[str, Any] | None = None
    execution_order: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def check_budgets(self):
        if self.s1_cap_seconds > self.deadline_seconds:
            raise ValueError("S1 cap cannot exceed the common deadline")
        if self.packet_rate < 100:
            raise ValueError(
                "at least 10 packets per 100ms bin required for this slice"
            )
        return self

    @property
    def packet_rate(self) -> float:
        return (
            self.bottleneck_mbps * 1e6 * self.load_fraction / (self.payload_bytes * 8)
        )


class Clock:
    def now_ns(self) -> int:
        return time.monotonic_ns()

    async def sleep_until(self, tick: int) -> None:
        await asyncio.sleep(max(0, tick - self.now_ns()) / NS)


class CampaignRuntime(Protocol):
    """Adapter for owned network, inference and independent traffic collection."""

    clock: Any
    evidence_kind: str

    async def setup(self, config: CampaignConfig, root: Path) -> dict: ...
    async def start_traffic(self) -> int: ...
    async def inject(self, start_ns: int) -> dict: ...
    async def diagnose(self) -> dict: ...
    async def decide(self, diagnostics: dict, remaining_seconds: float) -> dict: ...
    async def execute(self, choice: str) -> dict: ...
    async def service_signal(self, since_ns: int) -> dict: ...
    async def cancel(self, phase: str) -> dict: ...
    async def collect(self, observation_end_ns: int) -> dict: ...
    async def cleanup(self) -> dict: ...


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def measure(config: CampaignConfig, packets: list[dict], origin: int) -> dict:
    """Authoritative send-time accounting; raw duplicate arrivals remain in evidence."""
    end = round((config.deadline_seconds + 5) * NS)
    bins: dict[int, list[int]] = {}
    errors = []
    seen = set()
    arrivals = []
    duplicates = late = failed = sent_count = 0
    for packet in packets:
        seq, sent = packet["seq"], packet["send_ns"]
        if seq in seen:
            errors.append("duplicate_send_sequence")
            continue
        seen.add(seq)
        offset = sent - origin
        if not -5 * NS <= offset < end:
            continue
        raw = packet["arrivals_ns"]
        duplicates += max(0, len(raw) - 1)
        if any(tick < sent for tick in raw):
            errors.append("arrival_precedes_send_clock_error")
        timely = [tick for tick in raw if sent <= tick <= sent + BIN]
        bucket = bins.setdefault(offset // BIN, [0, 0])
        bucket[0] += 1
        bucket[1] += bool(timely)
        if offset >= 0:
            sent_count += 1
            failed += not timely
            late += bool(raw) and not timely
            if timely:
                arrivals.append(min(timely) - origin)
    timeline = []
    baseline_good = True
    # Only full bins can qualify; the partial final bin still contributes disruption.
    for index in range(-50, math.ceil(end / BIN)):
        sent, timely = bins.get(index, [0, 0])
        full = (index + 1) * BIN <= end
        enough = abs(sent - config.packet_rate / 10) <= max(
            1, config.packet_rate / 10 * 0.05
        )
        if full and not enough:
            errors.append(f"offered_load_or_send_evidence_gap:bin={index}")
        good = bool(sent) and timely / sent >= 0.99 and full
        if index < 0:
            baseline_good &= good and enough
        else:
            timeline.append(
                {
                    "bin": index,
                    "start_seconds": index / 10,
                    "sent": sent,
                    "timely": timely,
                    "failed": sent - timely,
                    "qualifies": good,
                }
            )
    streak = 0
    recovery = completion = None
    relapse = []
    in_relapse = False
    for item in timeline:
        streak = streak + 1 if item["qualifies"] else 0
        if recovery is None and streak >= 10 and (item["bin"] + 2) * BIN <= end:
            recovery = (item["bin"] - 9) / 10
            completion = (item["bin"] + 2) / 10  # bin end + 100ms delivery allowance
        if (
            recovery is not None
            and not item["qualifies"]
            and (item["bin"] + 1) * BIN <= end
        ):
            if not in_relapse:
                relapse.append(item["start_seconds"])
            in_relapse = True
        else:
            in_relapse = False
    points = [0, *sorted(min(tick, end) for tick in arrivals), end]
    longest_gap = (
        max((right - left for left, right in pairwise(points)), default=end) / NS
    )
    return {
        "baseline_healthy": baseline_good,
        "measurement_errors": sorted(set(errors)),
        "sent_packets": sent_count,
        "failed_packets": failed,
        "late_packets": late,
        "observed_disruption": failed > 0,
        "duplicate_arrivals": duplicates,
        "packet_rate": config.packet_rate,
        "disruption_seconds": failed / config.packet_rate,
        "longest_delivery_gap_seconds": longest_gap,
        "loss_timeline": timeline,
        "recovery_start_seconds": recovery,
        "verification_complete_seconds": completion,
        "late_restoration": completion is not None
        and completion > config.deadline_seconds,
        "relapse": bool(relapse),
        "relapse_starts_seconds": relapse,
        "censored": recovery is None,
        "censor_seconds": end / NS if recovery is None else None,
        "observation_seconds": end / NS,
    }


def provenance(config: CampaignConfig) -> dict:
    def git(*args):
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=Path(__file__).resolve().parents[3],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    dependencies = {}
    for name in ("mininet-ai", "mininet", "pydantic", "httpx"):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = None
    return {
        "budget_status": config.budget_status,
        "code_revision": git("rev-parse", "HEAD"),
        "worktree_status": git("status", "--porcelain"),
        "python": platform.python_version(),
        "dependencies": dependencies,
        "utc_started": datetime.now(UTC).isoformat(),
        "clock_domain": "linux-CLOCK_MONOTONIC-shared-network-namespaces",
        "clock_accuracy_ns": None,
        "model_revision": config.expected_identity,
        "runtime_model_settings": {
            "endpoint": config.endpoint,
            "model_name": config.model_name,
        },
        "instructions": INSTRUCTIONS,
        "criteria": CRITERIA,
        "catalogue": CATALOGUE,
        "catalogue_sha256": hashlib.sha256(
            json.dumps(CATALOGUE, sort_keys=True).encode()
        ).hexdigest(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_manifest": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).parent.glob("*.py"))
        },
        "seed": config.seed,
        "execution_order": config.execution_order,
        "unavailable": {
            "code_revision": "null when git metadata/tools are absent; retain source archive origin",
            "dependencies": "individual null entries mean installed distribution metadata is absent",
            "clock_accuracy_ns": "not independently calibrated",
            "model_revision": "provider identity unavailable unless configured",
        },
    }


async def run_campaign(
    config: CampaignConfig,
    evidence_dir: Path,
    *,
    runtime: CampaignRuntime | None = None,
) -> dict:
    """Run one incident and retain invalid runs and valid unsuccessful outcomes alike."""
    if runtime is None:
        from .live import LiveRuntime

        if config.purpose != "live_feasibility":
            raise ValueError("live runtime requires explicit purpose=live_feasibility")
        runtime = LiveRuntime()
    evidence_dir.mkdir(parents=True, exist_ok=False)
    write_json(evidence_dir / "config.json", config.model_dump(mode="json"))
    clock = runtime.clock
    events: list[dict] = []
    pending: list[asyncio.Task] = []
    model_calls = []
    actions = []
    invalid = []
    policy = "not_started"
    injection_start = None
    deadline = None
    reset = cleanup = packet_evidence = None
    timed_out = False

    def event(kind, **data):
        record = {
            "id": len(events) + 1,
            "trial_id": config.trial_id,
            "tick_ns": clock.now_ns(),
            "kind": kind,
            **data,
        }
        events.append(record)
        with (evidence_dir / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")

    async def bounded(name, operation, limit):
        if clock.now_ns() >= limit:
            operation.close()
            event(name + "_admission_denied", deadline_ns=limit)
            raise TimeoutError(name)
        event(name + "_start")
        task = asyncio.create_task(operation)
        timer = asyncio.create_task(clock.sleep_until(limit))
        done, _ = await asyncio.wait((task, timer), return_when=asyncio.FIRST_COMPLETED)
        if task in done and clock.now_ns() <= limit:
            timer.cancel()
            try:
                result = task.result()
            except Exception as error:
                event(name + "_failed", error=str(error))
                if getattr(error, "code", None) == "agent.bounded-decision.timeout":
                    event("cancellation", phase=name, **await runtime.cancel(name))
                    raise TimeoutError(name) from error
                raise
            event(name + "_complete", result=result)
            return result
        event(name + "_expired", deadline_ns=limit)
        cancellation = await runtime.cancel(name)
        event("cancellation", phase=name, **cancellation)
        task.cancel()
        pending.append(task)

        def terminal(completed):
            if completed.cancelled():
                event("local_task_cancelled", phase=name, remote_termination="unknown")
            else:
                try:
                    result = completed.result()
                    event("late_completion", phase=name, result=result)
                except Exception as error:
                    event("late_completion", phase=name, error=str(error))

        task.add_done_callback(terminal)
        timer.cancel()
        raise TimeoutError(name)

    prov = provenance(config)
    if runtime.evidence_kind == "mechanism_validation":
        prov["clock_domain"] = "mechanism-validation-runtime-clock"
    try:
        event("setup_start")
        reset = await runtime.setup(config, evidence_dir)
        event("reset_checks", result=reset)
        if (
            reset.get("initial_state") is not True
            or reset.get("automatic_backup") is not False
            or reset.get("fresh_context") is not True
        ):
            raise RuntimeError("broken_initial_state")
        traffic_start = await runtime.start_traffic()
        event("traffic_ready", start_ns=traffic_start)
        await clock.sleep_until(traffic_start + 5 * NS)
        injection_start = clock.now_ns()
        deadline = injection_start + round(config.deadline_seconds * NS)
        stage_limit = injection_start + round(config.s1_cap_seconds * NS)
        event("injection_start", deadline_ns=deadline)
        fault = await bounded("injection", runtime.inject(injection_start), deadline)
        if fault.get("both_endpoints_down") is not True:
            invalid.append("incorrect_injection")
        else:
            event("fault_confirmed")
            try:
                diagnostics = await bounded(
                    "telemetry", runtime.diagnose(), stage_limit
                )
                event("detection", source="harness-local-s1-diagnostics")
                inference_start = clock.now_ns()
                call = {
                    "started_ns": inference_start,
                    "request": {"diagnostics": diagnostics},
                    "response": None,
                    "tokens": {"input": None, "output": None, "reasoning": None},
                    "latency_seconds": None,
                    "late": False,
                    "status": "pending",
                    "retry": 0,
                }
                model_calls.append(call)

                async def infer():
                    try:
                        value = await runtime.decide(
                            diagnostics, max(0, stage_limit - clock.now_ns()) / NS
                        )
                        call.update(value, status="completed")
                        return value
                    except asyncio.CancelledError:
                        call["status"] = "locally_cancelled_remote_unknown"
                        raise
                    except Exception as error:
                        call.update(
                            status="failed",
                            error=str(error),
                            response=getattr(error, "native_response", None),
                        )
                        raw = call["response"]
                        usage = raw.get("usage") if isinstance(raw, dict) else None
                        if isinstance(usage, dict):
                            for field in ("input", "output"):
                                count = usage.get(
                                    field + "_tokens", usage.get(field + "Tokens")
                                )
                                if (
                                    isinstance(count, int)
                                    and not isinstance(count, bool)
                                    and count >= 0
                                ):
                                    call["tokens"][field] = count
                        raise
                    finally:
                        call["completed_ns"] = clock.now_ns()
                        call["latency_seconds"] = (
                            call["completed_ns"] - inference_start
                        ) / NS
                        call["late"] = call["completed_ns"] > stage_limit

                decision = await bounded("inference", infer(), stage_limit)
                choice = decision.get("choice")
                if choice not in CATALOGUE:
                    policy = "unusable_decision"
                elif CATALOGUE[choice] is None:
                    policy = "explicit_inability" if choice == "unable" else "no_action"
                else:
                    action = {
                        "choice": choice,
                        "started_ns": clock.now_ns(),
                        "result": None,
                    }
                    actions.append(action)
                    try:
                        result = await bounded(
                            "action", runtime.execute(choice), stage_limit
                        )
                    except TimeoutError:
                        action["result"] = {
                            "success": None,
                            "status": "cancelled_execution_unknown",
                        }
                        raise
                    except Exception as error:
                        result = {"success": False, "error": str(error)}
                    action.update(result=result, completed_ns=clock.now_ns())
                    if result.get("success") is not True:
                        policy = "action_failed"
                    else:
                        signal = await bounded(
                            "verification",
                            runtime.service_signal(action["completed_ns"]),
                            stage_limit,
                        )
                        policy = (
                            "verified"
                            if signal.get("recovered") is True
                            else "verification_failed"
                        )
            except TimeoutError:
                timed_out, policy = True, "timeout"
            except Exception as error:
                # Malformed decisions, failed inference/actions are treatment outcomes.
                policy = "treatment_error"
                event("treatment_error", error=str(error))
        event("policy_terminated", status=policy)
    except TimeoutError:
        invalid.append("injection_deadline_or_missing_confirmation")
        timed_out, policy = True, "timeout"
    except Exception as error:
        invalid.append(str(error))
        event("experimental_failure", error=str(error))
    finally:
        if injection_start is not None and deadline is not None:
            observation_end = deadline + 5 * NS
            # Finish collecting arrivals for the last send bin before classification.
            await clock.sleep_until(observation_end + BIN)
            event("observation_complete", send_window_end_ns=observation_end)
            try:
                packet_evidence = await runtime.collect(observation_end)
            except Exception as error:
                invalid.append("missing_essential_measurements:" + str(error))
        try:
            cleanup = await runtime.cleanup()
            event("cleanup", result=cleanup)
        except Exception as error:
            cleanup = {"verified": False, "error": str(error)}
            event("cleanup", result=cleanup)
        await asyncio.sleep(0)

    measurement = None
    if packet_evidence is not None and injection_start is not None:
        try:
            measurement = measure(config, packet_evidence["packets"], injection_start)
        except KeyError, ValueError, TypeError:
            invalid.append("missing_or_malformed_essential_packet_records")
    if measurement is not None and packet_evidence is not None:
        invalid.extend(packet_evidence.get("measurement_errors", []))
        invalid.extend(measurement["measurement_errors"])
        if not measurement["baseline_healthy"]:
            invalid.append("broken_initial_service")
        checks = packet_evidence.get("fault_checks", [])
        if not checks or any(
            check.get("both_endpoints_down") is not True for check in checks
        ):
            invalid.append("fault_persistence_unverified_or_reversed")
        event(
            "independent_verification",
            start_seconds=measurement["recovery_start_seconds"],
            complete_seconds=measurement["verification_complete_seconds"],
        )
    else:
        invalid.append("missing_essential_measurements")
    recovered = bool(
        measurement
        and measurement["verification_complete_seconds"] is not None
        and measurement["verification_complete_seconds"] <= config.deadline_seconds
    )
    messages = []
    for item in events:
        category = (
            "telemetry"
            if item["kind"] == "telemetry_complete"
            else "action"
            if item["kind"] in {"action_start", "action_complete"}
            else None
        )
        if category:
            messages.append(
                {
                    "category": category,
                    "event_id": item["id"],
                    "bytes": len(json.dumps(item, sort_keys=True).encode()),
                    "scope": "runner_adapter_json_not_wire",
                }
            )
    tokens = {
        "calls": len(model_calls),
        "retries": 0,
        "reasoning_accounting": "separate_not_added_to_output",
    }
    for field in ("input", "output", "reasoning"):
        values = [call["tokens"].get(field) for call in model_calls]
        tokens[field] = (
            sum(values)
            if values and all(value is not None for value in values)
            else None
        )
    artifacts = {
        str(path.relative_to(evidence_dir)): {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(evidence_dir.rglob("*"))
        if path.is_file()
    }
    result = {
        "schema_version": "recovery-s1/v1",
        "trial_id": config.trial_id,
        "config": config.model_dump(mode="json"),
        "evidence_kind": runtime.evidence_kind,
        "validity": {"valid": not invalid, "reasons": sorted(set(invalid))},
        "outcome": {
            "action_execution_success": actions[-1]["result"].get("success")
            if actions and actions[-1]["result"] is not None
            else None,
            "service_recovery": recovered,
            "recovery_failure": not recovered,
            "recovery_timeout": timed_out,
            "policy_status": policy,
        },
        "measurement": measurement,
        "events": events,
        "model_calls": model_calls,
        "actions": actions,
        "packet_evidence": packet_evidence,
        "tokens": tokens,
        "artifacts": artifacts,
        "reset": reset,
        "cleanup": cleanup,
        "provenance": prov,
        "messages": {
            "records": messages,
            "handoff_count": 0,
            "handoff_bytes": 0,
            "wire_bytes": None,
            "unavailable_reason": "wire accounting not instrumented",
        },
        "outstanding_local_tasks": sum(not task.done() for task in pending),
        "containment": {
            "next_trial_allowed": not timed_out
            and all(task.done() for task in pending),
            "remote_termination_after_cancel": "unknown"
            if timed_out
            else "not_requested",
        },
        "journal_snapshot_tick_ns": clock.now_ns(),
        "artifacts_scope": "snapshot_at_export; events/model-wire journals may retain later terminal evidence",
        "unavailable": {
            "energy": "not measured",
            "remote_cancellation": "no authoritative provider termination API",
            "reasoning_tokens": "null unless provider supplies separate non-overlapping usage",
        },
    }
    # Later task/provider completions append to durable journals, not frozen outcomes.
    snapshot = copy.deepcopy(result)
    write_json(evidence_dir / "evidence.json", snapshot)
    return snapshot
