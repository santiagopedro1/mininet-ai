"""Durable v2 collection and projections. Collector order is not timestamp order."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from mininet_ai.runtime.instrumentation import PhaseRecord

from .validation import validate_record


def duration(
    start=None,
    end=None,
    *,
    reason="incomplete_phase",
    applicable=True,
    reversed_reason="timestamp_order_invalid",
    trustworthy=True,
):
    value = None
    flags = []
    availability = "unavailable" if applicable else "not_applicable"
    if not applicable:
        reason = "no_confirmed_fault"
    elif not trustworthy:
        reason = "missing_evidence"
        flags.append(reason)
    elif start is not None and end is not None:
        delta = int(end["tick_ns"]) - int(start["tick_ns"])
        if start["clock_domain_id"] != end["clock_domain_id"]:
            reason = "clock_domain_mismatch"
            flags.append(reason)
        elif delta < 0:
            reason = reversed_reason
            flags.append(reason)
        else:
            availability, value, reason = "available", delta / 1e9, None
    return {
        "availability": availability,
        "value_seconds": value,
        "reason": reason,
        "start_record_id": start["record_id"] if start else None,
        "end_record_id": end["record_id"] if end else None,
        "flags": flags,
    }


class EvidenceWriter:
    """Exclusive directory ownership; failed writes poison the collector permanently.

    Every record is flushed/fsynced before publication to projections. No retries,
    truncation, or fabricated terminal events on a storage failure.
    """

    def __init__(self, directory: Path, run_id: str, domain: str, clock):
        directory.mkdir(parents=True, exist_ok=False)
        self.directory, self.run_id, self.domain, self.clock = (
            directory,
            run_id,
            domain,
            clock,
        )
        self.lock = RLock()
        self.records: list[dict[str, Any]] = []
        self.failure: Exception | None = None
        self.sealed = False
        self.observations = {}
        self.frozen_trials: set[str] = set()

    def write(self, family, payload, tick=None):
        with self.lock:
            if self.failure:
                raise RuntimeError(
                    "evidence storage previously failed"
                ) from self.failure
            if self.sealed:
                raise RuntimeError("evidence collector finalized")
            record = {
                "schema": "mininet-ai/link-failure-latency/v2",
                "record_id": str(uuid4()),
                "collector_seq": len(self.records),
                "run_id": self.run_id,
                "clock_domain_id": self.domain,
                "tick_ns": str(self.clock() if tick is None else tick),
                "evidence_ids": [],
            } | payload
            issues = validate_record(family, record)
            if issues:
                raise ValueError(f"invalid {family} record: {issues}")
            filename = {
                "run": "run.json",
                "event": "events.jsonl",
                "trial": "trials.jsonl",
                "agent": "agents.jsonl",
                "summary": "summary.json",
            }[family]
            try:
                encoded = json.dumps(record, allow_nan=False) + "\n"
                if family in {"run", "summary"}:
                    temporary = self.directory / (filename + ".tmp")
                    with temporary.open("x", encoding="utf-8") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.directory / filename)
                    fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                else:
                    with (self.directory / filename).open(
                        "a", encoding="utf-8"
                    ) as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
            except Exception as error:
                self.failure = error
                raise
            self.records.append(record)
            return record

    def event(self, context, kind, data, tick=None):
        with self.lock:
            if context is not None and context.trial_id in self.frozen_trials:
                raise RuntimeError("bounded trial collection finalized")
            record = self.write(
                "event",
                {
                    "type": kind,
                    "trial_id": context.trial_id if context else None,
                    "fault_id": context.fault_id if context else None,
                    "data": data,
                },
                tick,
            )
            if kind == "observation":
                self.observations[data["observation_id"]] = record
            return record

    def phase(self, phase: PhaseRecord):
        context = phase.context
        if phase.stamp.tick_ns is None:
            raise ValueError("phase timestamp unavailable")
        with self.lock:
            self.event(
                context,
                phase.transition,
                {
                    "operation_id": phase.operation_id,
                    "phase": phase.phase.value,
                    "agent_id": context.agent_id,
                    "invocation_id": context.invocation_id,
                    "proposal_id": context.proposal_id,
                    "outcome": phase.outcome,
                },
                int(phase.stamp.tick_ns),
            )
            if (
                phase.phase == "admission"
                and phase.outcome is not None
                and phase.outcome.startswith("trial_")
            ):
                self.event(
                    context,
                    "admission",
                    {
                        "kind": "invocation"
                        if "invocation" in phase.outcome
                        else "action",
                        "operation_id": phase.operation_id,
                        "agent_id": context.agent_id,
                        "invocation_id": context.invocation_id,
                        "proposal_id": context.proposal_id,
                        "accepted": phase.outcome.endswith("admitted"),
                    },
                    int(phase.stamp.tick_ns),
                )
            if (
                phase.phase == "detector"
                and phase.outcome == "relevant_failure_recognized"
                and context.agent_id
            ):
                observed = self.observations.get(context.observation_id)
                if observed:
                    self.event(
                        context,
                        "recognition",
                        {
                            "agent_id": context.agent_id,
                            "observation_record_id": observed["record_id"],
                            "event_id": context.event_id or phase.operation_id,
                            "source": context.observation_source or "global_harness",
                        },
                        int(phase.stamp.tick_ns),
                    )
            if (
                phase.phase == "stopping"
                and phase.outcome == "response_admission_closed"
            ):
                self.event(
                    context,
                    "admission_closed",
                    {"reason": "response deadline"},
                    int(phase.stamp.tick_ns),
                )

    def project(self, attempt, scenario, *, trustworthy=True, sdk_metrics=None):
        execution = attempt.execution
        context, outcome = execution.context, execution.outcome
        rows = [r for r in self.records if r.get("trial_id") == context.trial_id]

        def first(kind):
            return next((r for r in rows if r.get("type") == kind), None)

        start, end, cleanup = (
            first(k) for k in ("trial_started", "trial_ended", "cleanup_completed")
        )
        assert start is not None and end is not None and cleanup is not None
        fault = first("fault_confirmed") if outcome.onset_ns is not None else None
        recovery = first("recovery_confirmed")
        operations = defaultdict(list)
        for row in rows:
            if row.get("type", "").startswith("operation_"):
                operations[row["data"]["operation_id"]].append(row)
        injection_ops = [
            rs for rs in operations.values() if rs[0]["data"]["phase"] == "injection"
        ]
        injection_start = injection_ops[0][0] if injection_ops else None
        injection_end = (
            injection_ops[0][-1]
            if injection_ops and len(injection_ops[0]) == 2
            else None
        )
        applicable = fault is not None
        payload = {
            "trial_id": context.trial_id,
            "fault_id": context.fault_id,
            "trial_kind": attempt.kind,
            "ordinal": attempt.ordinal,
            "scenario_id": scenario.plan.digest,
            "failed_link": start["data"]["failed_link"],
            "healthy_paths": paths(scenario),
            "recovery_paths": paths(scenario, True),
            "outcome": outcome.status,
            "stage": outcome.stage,
            "reason": outcome.reason,
            "started_record_id": start["record_id"],
            "ended_record_id": end["record_id"],
            "cleanup_record_id": cleanup["record_id"],
            "fault_record_id": fault["record_id"] if fault else None,
            "recovery_record_id": recovery["record_id"] if recovery else None,
            "response_deadline_ns": str(outcome.response_deadline_ns)
            if outcome.response_deadline_ns is not None
            else None,
            "cleanup_outcome": execution.cleanup.status,
            "measurements": {
                "setup": duration(start, injection_start or end),
                "injection": duration(
                    injection_start, injection_end, reason="phase_not_started"
                ),
                "recovery": duration(
                    fault,
                    recovery,
                    reason="no_confirmed_recovery",
                    applicable=applicable,
                    trustworthy=trustworthy,
                ),
                "response": duration(fault, end, applicable=applicable),
                "stopping": duration(end, cleanup),
                "total": duration(start, cleanup),
            },
        }
        self.write("trial", payload)
        for agent in scenario.plan.agents:
            selected = [
                rs
                for rs in operations.values()
                if rs[0]["data"]["agent_id"] == agent.id
            ]
            projected = []
            for rs in selected:
                begin = next((r for r in rs if r["type"] == "operation_started"), None)
                terminal = next(
                    (r for r in rs if r["type"] != "operation_started"), None
                )
                projected.append(
                    {
                        "operation_id": rs[0]["data"]["operation_id"],
                        "phase": rs[0]["data"]["phase"],
                        "state": {
                            "operation_completed": "completed",
                            "operation_failed": "failed",
                            "operation_cancelled": "cancelled",
                        }.get(terminal["type"] if terminal else "", "incomplete"),
                        "outcome": terminal["data"]["outcome"] if terminal else None,
                        "late": bool(
                            terminal and int(terminal["tick_ns"]) > outcome.ended_ns
                        ),
                        "duration": duration(begin, terminal, trustworthy=trustworthy),
                        "evidence_ids": [r["record_id"] for r in rs],
                    }
                )
            recognition = next(
                (
                    r
                    for r in rows
                    if r.get("type") == "recognition"
                    and r["data"]["agent_id"] == agent.id
                ),
                None,
            )
            reasoning = next((p for p in projected if p["phase"] == "reasoning"), None)
            action = next(
                (
                    rs[0]
                    for rs in selected
                    if rs[0]["data"]["phase"] == "action_attempt"
                ),
                None,
            )
            self.write(
                "agent",
                {
                    "trial_id": context.trial_id,
                    "fault_id": context.fault_id,
                    "agent_id": agent.id,
                    "switch_id": agent.attachment.targets[0],
                    "invocation_ids": sorted(
                        {
                            r["data"]["invocation_id"]
                            for rs in selected
                            for r in rs
                            if r["data"]["invocation_id"]
                        }
                    ),
                    "action_ids": sorted(
                        {
                            r["data"]["proposal_id"]
                            for rs in selected
                            for r in rs
                            if r["data"]["proposal_id"]
                        }
                    ),
                    "measurements": {
                        "detection": duration(
                            fault,
                            recognition,
                            reason="no_detection_before_trial_end",
                            applicable=applicable,
                            reversed_reason="recognition_before_onset",
                            trustworthy=trustworthy,
                        ),
                        "reasoning": reasoning["duration"]
                        if applicable and reasoning
                        else duration(
                            reason="phase_not_started", applicable=applicable
                        ),
                        "action_effect": duration(
                            action,
                            recovery,
                            reason="no_action_attempt"
                            if action is None
                            else "no_confirmed_recovery",
                            applicable=applicable,
                            reversed_reason="action_after_recovery",
                            trustworthy=trustworthy,
                        ),
                    },
                    "operations": projected,
                    "sdk_metrics": (sdk_metrics or {}).get(agent.id, {}),
                    "late": any(p["late"] for p in projected),
                    "incomplete": any(p["state"] == "incomplete" for p in projected),
                },
            )

    def request(self, previous, request):
        with self.lock:
            context = request.context
            if previous is None:
                self.event(
                    context,
                    "provider_request_started",
                    {
                        "request_id": request.request_id,
                        "provider_request_id": request.provider_request_id,
                        "invocation_id": context.invocation_id,
                        "agent_id": context.agent_id,
                        "provider": request.provider,
                    },
                    int(request.started.tick_ns),
                )
            if request.cancel_requested is not None and (
                previous is None
                or request.cancel_requested != previous.cancel_requested
            ):
                self.event(
                    context,
                    "provider_cancel_requested",
                    {
                        "request_id": request.request_id,
                        "reason": request.local_error or "local cancellation requested",
                    },
                    int(request.cancel_requested.tick_ns),
                )
            if request.terminal is not None and (
                previous is None or previous.terminal is None
            ):
                self.event(
                    context,
                    "provider_request_terminal",
                    {
                        "request_id": request.request_id,
                        "evidence": request.terminal_evidence,
                        "source": request.terminal_source,
                    },
                    int(request.terminal.tick_ns),
                )


def paths(scenario, failed=False):
    return {
        f"{a}_to_{b}": [a, *scenario.route(a, b, failed=failed), b]
        for a, b in (("h1", "h2"), ("h2", "h1"))
    }
