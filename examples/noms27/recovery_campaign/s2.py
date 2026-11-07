"""Direct System Two policy: bounded, forwarding-only observe–act–verify."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from examples.diamond_recovery import PORTS

from .supervision import record_inference, run_action

NS = 1_000_000_000
MAX_ATTEMPTS = 3
MODEL_SETTINGS = {
    "think": False,
    "keep_alive": -1,
    "options": {"temperature": 0.3, "num_predict": 1024},
}
PERMISSIONS = {
    "telemetry": "surviving_switches_network_wide",
    "forwarding": "surviving_switches_only",
    "fault_reversal": False,
    "resource_revival": False,
    "offered_traffic_changes": False,
    "evaluator_labels": False,
    "max_attempts": MAX_ATTEMPTS,
}
INSTRUCTIONS = (
    "Diagnose the directional h1-to-h2 service alarm using the shared initial local "
    "snapshot and current network-wide observations. Propose forwarding changes on "
    "surviving switches only. Each action installs a fixed IPv4 h1-to-h2 rule for "
    "10.0.0.1 -> 10.0.0.2, matching in_port and outputting out_port. "
    "Never enable ports, revive resources, reverse outages, change traffic, or "
    "construct commands. Use only observed up ports. A repair may span switches. "
    'Return JSON only: {"status":"repair","actions":[{"switch":"s1",'
    '"in_port":1,"out_port":3}]} or {"status":"unable","actions":[]}.'
)


class ForwardingAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    switch: str
    in_port: int = Field(gt=0)
    out_port: int = Field(gt=0)


class Repair(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["repair", "unable", "no_action"]
    actions: list[ForwardingAction] = Field(max_length=16)


def validate_repair(value, observation):
    repair = Repair.model_validate(value)
    if (repair.status == "repair") != bool(repair.actions):
        raise ValueError("repair requires actions; inability/no_action forbids actions")
    switches = observation.get("switches", {})
    for action in repair.actions:
        ports = switches.get(action.switch, {}).get("ports", {})
        if action.switch not in PORTS or action.in_port == action.out_port:
            raise ValueError("unknown switch or non-forwarding action")
        allowed = set(PORTS[action.switch].values())
        if any(
            port not in allowed or ports.get(str(port)) is not True
            for port in (action.in_port, action.out_port)
        ):
            raise ValueError("repair requires surviving observed ports")
    return repair.model_dump()


async def run_direct(runtime, clock, deadline, initial, bounded, event, calls, actions):
    history = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        event("s2_attempt_start", attempt=attempt, deadline_ns=deadline)
        observation = await bounded("telemetry", runtime.observe_network(), deadline)
        context = {
            "alarm": "directional_service_disruption",
            "initial_snapshot": initial,
            "observation": observation,
            "permissions": PERMISSIONS,
            "history": list(history),
        }
        call = {
            "stage": "s2",
            "attempt": attempt,
            "started_ns": clock.now_ns(),
            "request": context,
            "response": None,
            "tokens": {"input": None, "output": None, "reasoning": None},
            "latency_seconds": None,
            "late": False,
            "status": "pending",
            "retry": 0,
        }
        calls.append(call)

        async def infer(call=call, context=context):
            # Bind the current record: late completions must never mutate a later call.
            value = await record_inference(
                lambda: runtime.decide_s2(context, (deadline - clock.now_ns()) / NS),
                call,
                clock,
                deadline,
            )
            if value.get("remote_terminal") == "unconfirmed":
                call["status"] = "remote_terminal_unknown"
            return value

        try:
            decision = await bounded("inference", infer(), deadline)
            repair = validate_repair(decision.get("repair"), observation)
        except TimeoutError:
            raise
        except Exception as error:
            event("s2_decision_rejected", attempt=attempt, error=str(error))
            history.append(
                {
                    "attempt": attempt,
                    "observation": observation,
                    "status": "unusable_decision",
                }
            )
            if call["response"] is None or call["status"] == "remote_terminal_unknown":
                # Do not overlap further requests with a possibly outstanding
                # provider operation whose terminal response was never observed.
                return "treatment_error"
            continue
        if repair["status"] != "repair":
            return "explicit_inability" if repair["status"] == "unable" else "no_action"
        action = {
            "stage": "s2",
            "attempt": attempt,
            "repair": repair,
            "started_ns": clock.now_ns(),
            "result": None,
        }
        actions.append(action)

        result = await run_action(
            lambda repair=repair: runtime.execute_s2(repair),
            action,
            clock,
            deadline,
            bounded,
        )
        signal = await bounded(
            "verification", runtime.service_signal(clock.now_ns()), deadline
        )
        status = (
            "verified" if signal.get("recovered") is True else "verification_failed"
        )
        history.append(
            {
                "attempt": attempt,
                "observation": observation,
                "repair": repair,
                "result": result,
                "verification": signal,
                "status": status,
            }
        )
        event("s2_attempt_complete", **history[-1])
        if status == "verified":
            return status
    return "attempts_exhausted"
