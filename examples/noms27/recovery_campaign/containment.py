"""Bounded containment of cancelled System One work before admitting S2.

A local cancellation is not remote or process termination. Before S2 may be
admitted under the shared end-to-end deadline after an escalated S1 stage whose
work was cancelled, deferred work must be resolved, their termination confirmed
against the actual provider and process registry, and the network state
re-observed. Confirmation is capability-keyed: only a runtime that implements
the confirmation probes can ever make a cancelled stage contained, and every
fail-path stays the conservative denial used before this module existed.
"""

from __future__ import annotations

import asyncio

from .supervision import NS

CALL_STATUS_OK = {"completed", "failed"}
CALL_CANCEL_CONFIRMED = "locally_cancelled_remote_confirmed"
POLL_INTERVAL_MS = 50


def unsafe_reasons(pending, model_calls, actions) -> list[str]:
    """Report which cancelled-work classes lack terminal evidence."""
    reasons = []
    if any(not task.done() for task in pending):
        reasons.append("pending_local_task")
    if any(
        call["response"] is None or call["status"] not in CALL_STATUS_OK
        for call in model_calls
    ):
        reasons.append("provider_terminal_evidence")
    if any(
        action["result"] is None or action["result"].get("success") is None
        for action in actions
    ):
        reasons.append("action_terminal_evidence")
    return reasons


async def contain_cancelled_stage(
    runtime,
    clock,
    event,
    deadline_ns,
    pending,
    model_calls,
    actions,
    reasons,
) -> dict:
    """Attempt bounded confirmation of every cancelled-work class.

    All waiting is bounded by the inherited end-to-end deadline. The returned
    record carries the per-phase evidence; ``contained`` is ``True`` only when
    every class was resolved and a fresh observation was captured in time.
    """
    record: dict = {
        "attempted": bool(reasons),
        "reasons": list(reasons),
        "deadline_ns": deadline_ns,
        "phases": {},
        "observation": None,
        "contained": False,
        "contained_ns": None,
    }
    event(
        "escalation_containment_started",
        reasons=record["reasons"],
        deadline_ns=deadline_ns,
    )

    remaining = deadline_ns - clock.now_ns()
    if remaining <= 0:
        record["phases"]["schedule"] = {
            "ok": False,
            "reason": "deadline_exhausted",
        }
        return record

    # Phase 1: local termination. Cancelled tasks must finish unwinding;
    # their local side effects are the only state S2 could otherwise inherit
    # while work is still in flight.
    tasks = {task for task in pending if not task.done()}
    if tasks:
        resolved, still = await asyncio.wait(tasks, timeout=max(0.0, remaining / NS))
        record["phases"]["local"] = {
            "ok": not still,
            "resolved": len(resolved),
            "unresolved": len(still),
        }
    else:
        record["phases"]["local"] = {"ok": True, "resolved": 0, "unresolved": 0}
    if not record["phases"]["local"]["ok"]:
        return record

    # Phase 2: provider termination. Inference with an unobserved terminal
    # response (locally cancelled or connection-failed) is remote-unknown until
    # a runtime capability confirms the provider is no longer processing.
    unresolved_calls = [
        call
        for call in model_calls
        if call.get("termination_confirmation", {}).get("confirmed") is not True
        and (
            call["status"] not in CALL_STATUS_OK | {CALL_CANCEL_CONFIRMED}
            or (call["response"] is None and call["status"] != CALL_CANCEL_CONFIRMED)
        )
    ]
    if unresolved_calls:
        confirm = getattr(runtime, "confirm_inference_termination", None)
        confirmed = await _poll_until_confirmed(
            runtime, clock, event, deadline_ns, confirm, "provider"
        )
        record["phases"]["provider"] = confirmed["phase"]
        if not confirmed["ok"]:
            return record
        for call in unresolved_calls:
            call["termination_confirmation"] = {
                "confirmed": True,
                "probes": confirmed["phase"]["probes"],
            }
            if call["status"] not in CALL_STATUS_OK:
                call["status"] = CALL_CANCEL_CONFIRMED
        record["phases"]["provider"]["confirmed_calls"] = len(unresolved_calls)
    else:
        record["phases"]["provider"] = {"ok": True, "confirmed_calls": 0}

    # Phase 3: action termination. A cancelled ovs-ofctl-style process must have
    # an exit status before the network can be considered quiescent.
    unresolved_actions = [
        action
        for action in actions
        if action["result"] is None or action["result"].get("success") is None
    ]
    if unresolved_actions:
        confirm = getattr(runtime, "confirm_action_termination", None)
        confirmed = await _poll_until_confirmed(
            runtime, clock, event, deadline_ns, confirm, "actions"
        )
        record["phases"]["actions"] = confirmed["phase"]
        if not confirmed["ok"]:
            return record
        record["phases"]["actions"]["confirmed_actions"] = len(unresolved_actions)
    else:
        record["phases"]["actions"] = {"ok": True, "confirmed_actions": 0}

    # Phase 4: settled-state observation, bounded like every other phase.
    observation_task = asyncio.create_task(runtime.diagnose())
    try:
        observation = await asyncio.wait_for(
            observation_task, timeout=(deadline_ns - clock.now_ns()) / NS
        )
    except Exception as error:
        observation_task.cancel()
        record["phases"]["observation"] = {
            "ok": False,
            "error": type(error).__name__,
        }
        return record
    record["observation"] = observation
    record["phases"]["observation"] = {"ok": True}
    record["contained"] = True
    record["contained_ns"] = clock.now_ns()
    event(
        "escalation_contained",
        phases=record["phases"],
        contained_ns=record["contained_ns"],
    )
    return record


async def _poll_until_confirmed(runtime, clock, event, deadline_ns, confirm, phase):
    if confirm is None:
        return {
            "ok": False,
            "phase": {
                "ok": False,
                "confirmed": False,
                "reason": "runtime_capability_missing",
                "probes": 0,
            },
        }
    probes = 0
    while clock.now_ns() < deadline_ns:
        probes += 1
        try:
            report = await confirm()
        except Exception as error:  # probe failure is unconfirmation, not a crash
            report = {"terminated": None, "reason": "probe_raised", "error": str(error)}
        slim = {k: v for k, v in report.items() if isinstance(v, (bool, int, list))}
        event("containment_probe", phase=phase, probe=probes, **slim)
        if report.get("terminated") is True:
            return {
                "ok": True,
                "phase": {"ok": True, "confirmed": True, "probes": probes, **slim},
            }
        if report.get("terminated") is None:
            return {
                "ok": False,
                "phase": {
                    "ok": False,
                    "confirmed": False,
                    "reason": "confirmation_unavailable",
                    "probes": probes,
                    **slim,
                },
            }
        await clock.sleep_until(clock.now_ns() + POLL_INTERVAL_MS * 1_000_000)
    return {
        "ok": False,
        "phase": {
            "ok": False,
            "confirmed": False,
            "reason": "deadline_before_confirmation",
            "probes": probes,
        },
    }
