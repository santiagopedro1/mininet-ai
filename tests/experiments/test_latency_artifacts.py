"""Conformance at the published record and artifact-directory boundaries."""

import json
import shutil
from collections import deque
from copy import deepcopy
from pathlib import Path

import pytest

from mininet_ai.experiments.latency import validate_artifact, validate_record


def operation_event():
    return {
        "schema": "mininet-ai/link-failure-latency/v2",
        "record_id": "event-1",
        "collector_seq": 1,
        "run_id": "run-1",
        "clock_domain_id": "clock-1",
        "tick_ns": "9007199254740993",
        "evidence_ids": [],
        "trial_id": "trial-1",
        "fault_id": "fault-1",
        "type": "operation_started",
        "data": {
            "operation_id": "operation-1",
            "phase": "reasoning",
            "agent_id": "agent-1",
            "invocation_id": "invocation-1",
            "proposal_id": None,
            "outcome": None,
        },
    }


def test_event_schema_preserves_exact_ticks_and_closed_transitions():
    record = operation_event()
    assert validate_record("event", record) == ()
    invalid = deepcopy(record)
    invalid["tick_ns"] = 9007199254740993
    assert validate_record("event", invalid)
    invalid = deepcopy(record)
    invalid["type"] = "operation_incomplete"
    assert validate_record("event", invalid)


def envelope(record_id="agent-record", seq=10, tick="10000000000"):
    return {
        "schema": "mininet-ai/link-failure-latency/v2",
        "record_id": record_id,
        "collector_seq": seq,
        "run_id": "run-1",
        "clock_domain_id": "clock-1",
        "tick_ns": tick,
        "evidence_ids": [],
    }


def unavailable(reason="phase_not_started"):
    return {
        "availability": "unavailable",
        "value_seconds": None,
        "reason": reason,
        "start_record_id": None,
        "end_record_id": None,
        "flags": [],
    }


def agent_record():
    return envelope() | {
        "trial_id": "trial-1",
        "fault_id": "fault-1",
        "agent_id": "agent-1",
        "switch_id": "s1",
        "invocation_ids": [],
        "action_ids": [],
        "measurements": {
            "detection": unavailable(),
            "reasoning": unavailable(),
            "action_effect": unavailable("no_action_attempt"),
        },
        "operations": [],
        "sdk_metrics": {},
        "late": False,
        "incomplete": False,
    }


def test_agent_measurements_distinguish_missing_from_measured_zero():
    agent = agent_record()
    assert validate_record("agent", agent) == ()
    duration = agent["measurements"]["reasoning"]
    duration.update(
        availability="available",
        value_seconds=0,
        reason=None,
        start_record_id="start",
        end_record_id="end",
    )
    assert validate_record("agent", agent) == ()
    for bad in (None, -1, float("nan"), float("inf")):
        duration["value_seconds"] = bad
        assert validate_record("agent", agent)
    duration.update(availability="unavailable", value_seconds=None, reason=None)
    assert validate_record("agent", agent)


@pytest.mark.parametrize(
    "state",
    ["not_started", "in_progress", "completed", "failed", "cancelled", "incomplete"],
)
def test_agent_operation_states_are_closed_and_independent_of_lateness(state):
    agent = agent_record()
    agent["operations"] = [
        {
            "operation_id": "op-1",
            "phase": "reasoning",
            "state": state,
            "outcome": "provider_error",
            "late": True,
            "duration": unavailable("incomplete_phase"),
            "evidence_ids": [],
        }
    ]
    assert validate_record("agent", agent) == ()
    agent["operations"][0]["state"] = "timed_out"
    assert validate_record("agent", agent)


def manifest_record():
    golden = json.loads(
        (
            Path(__file__).parents[2]
            / "docs/fixtures/link-failure-latency-seed-v1-0.json"
        ).read_text()
    )
    scenario = {
        "graph": golden["edges"],
        "ports_by_switch": golden["ports_by_switch"],
        "host_attachments": golden["host_attachments"],
        "host_addresses": golden["host_addresses"],
        "designated_traffic": [
            {"source": "h1", "destination": "h2"},
            {"source": "h2", "destination": "h1"},
        ],
        "eligible_failure_links": golden["eligible_failure_links"],
        "failure_order": {
            "warmup": [],
            "measured": [golden["measured_failure_order"][0]],
        },
        "healthy_paths": golden["healthy_paths"],
        "policies": {
            "healthy": fixture_policy(golden, None)["rules"],
            "recovery": [
                fixture_policy(golden, edge)
                for edge in golden["eligible_failure_links"]
            ],
        },
        "network": {
            "bandwidth_mbps": 100,
            "delay_ms": 1,
            "loss_percent": 0,
            "openflow": "OpenFlow13",
            "fail_mode": "secure",
            "controller": None,
        },
    }
    return envelope("manifest", 0, "0") | {
        "created_at_utc": "2026-10-16T00:00:00Z",
        "generator_version": "sha256-counter-v1",
        "seed": 0,
        "scenario_id": "scenario-0",
        "agents": [
            {"agent_id": f"agent-{i}", "switch_id": f"s{i}"} for i in range(1, 11)
        ],
        "configuration": {
            "measured_attempts": 1,
            "warmup_attempts": 0,
            "budgets_seconds": {
                "setup": 120,
                "injection": 10,
                "response": 120,
                "cleanup": 30,
            },
            "sampling_period_seconds": 0.1,
            "probe_period_seconds": 1,
        },
        "scenario": scenario,
        "provenance": {
            "code_revision": "test-revision",
            "provider": "ollama",
            "model": "test-model",
            "provider_configuration": {},
            "resolved_model_identity": None,
            "environment": {"platform": "fixture"},
        },
    }


def fixture_policy(golden, failed_link):
    """Build synthetic replay evidence, not a generator conformance oracle."""
    neighbors = {switch: [] for switch in golden["canonical_switch_ids"]}
    for left, right in golden["edges"]:
        if failed_link is not None and {left, right} == set(failed_link):
            continue
        neighbors[left].append(right)
        neighbors[right].append(left)
    rules, paths = [], {}
    for host, attachment in golden["host_attachments"].items():
        distance = {attachment: 0}
        queue = deque([attachment])
        while queue:
            node = queue.popleft()
            for neighbor in neighbors[node]:
                if neighbor not in distance:
                    distance[neighbor] = distance[node] + 1
                    queue.append(neighbor)
        next_hop = {}
        for switch, adjacent in neighbors.items():
            hop = (
                host
                if switch == attachment
                else min(n for n in adjacent if distance[n] == distance[switch] - 1)
            )
            next_hop[switch] = hop
            port = next(
                p["port"]
                for p in golden["ports_by_switch"][switch]
                if p["neighbor"] == hop
            )
            for eth_type in ("arp", "ipv4"):
                rules.append(
                    {
                        "switch_id": switch,
                        "destination": host,
                        "eth_type": eth_type,
                        "output_port": port,
                    }
                )
        source = "h2" if host == "h1" else "h1"
        path = [source, golden["host_attachments"][source]]
        while path[-1] != host:
            path.append(next_hop[path[-1]])
        paths[f"{source}_to_{host}"] = path
    return {"failed_link": failed_link, "paths": paths, "rules": rules}


def test_manifest_requires_replay_and_explicit_unknown_model_identity():
    record = manifest_record()
    assert validate_record("run", record) == ()
    del record["provenance"]["resolved_model_identity"]
    assert validate_record("run", record)


EVENT_DATA = {
    "trial_started": {
        "trial_kind": "measured",
        "ordinal": 1,
        "scenario_id": "scenario-0",
        "failed_link": ["s1", "s2"],
    },
    "trial_ended": {"outcome": "timeout", "stage": "response", "reason": "deadline"},
    "observation_ready": {"source": "global", "agent_id": "agent-1"},
    "observation": {
        "observation_id": "obs-1",
        "source": "global",
        "scheduled_ns": "1",
        "started_ns": "1",
        "completed_ns": "2",
        "endpoint_states": ["down", "down"],
    },
    "recognition": {
        "agent_id": "agent-1",
        "observation_record_id": "observation-record",
        "event_id": "detector-event",
        "source": "global",
    },
    "fault_confirmed": {"observation_record_id": "observation-record"},
    "probe_round": {
        "probe_round_id": "probe-1",
        "purpose": "recovery",
        "slot": 1,
        "scheduled_ns": "1",
        "started_ns": "1",
        "completed_ns": "2",
        "directions": [
            {
                "source": "h1",
                "destination": "h2",
                "success": True,
                "observation_id": "p1",
            },
            {
                "source": "h2",
                "destination": "h1",
                "success": True,
                "observation_id": "p2",
            },
        ],
        "link_before": ["down", "down"],
        "link_after": ["down", "down"],
        "late": False,
    },
    "slot_missed": {
        "schedule": "probe",
        "slot": 2,
        "scheduled_ns": "3",
        "reason": "overrun",
    },
    "recovery_confirmed": {
        "probe_record_ids": ["probe-record-1", "probe-record-2", "probe-record-3"],
        "first_success_record_id": "probe-record-1",
    },
    "admission": {
        "kind": "action",
        "operation_id": "op-1",
        "agent_id": "agent-1",
        "invocation_id": "inv-1",
        "proposal_id": "proposal-1",
        "accepted": True,
    },
    "admission_closed": {"reason": "deadline"},
    "provider_request_started": {
        "request_id": "request-1",
        "provider_request_id": None,
        "invocation_id": "inv-1",
        "agent_id": "agent-1",
        "provider": "ollama",
    },
    "provider_request_terminal": {
        "request_id": "request-1",
        "evidence": "normal_response",
        "source": "ollama-protocol",
    },
    "provider_cancel_requested": {"request_id": "request-1", "reason": "deadline"},
    "cleanup_completed": {
        "outcome": "unknown",
        "network_absent": True,
        "workers_stopped": True,
        "provider_contained": False,
        "reason": "provider_unknown",
    },
    "recorder_loss": {"reason": "queue_full", "lost_count": 1},
    "run_finished": {"reason": "attempts_completed"},
    "run_aborted": {"reason": "provider_unknown"},
}


@pytest.mark.parametrize("kind", EVENT_DATA)
def test_raw_event_variants_have_closed_required_payloads(kind):
    record = envelope() | {
        "trial_id": "trial-1",
        "fault_id": "fault-1",
        "type": kind,
        "data": deepcopy(EVENT_DATA[kind]),
    }
    if kind in {"run_finished", "run_aborted"}:
        record.update(trial_id=None, fault_id=None)
    assert validate_record("event", record) == ()
    record["data"]["unrecognized"] = True
    assert validate_record("event", record)


def trial_record():
    return envelope("trial-record", 10) | {
        "trial_id": "trial-1",
        "fault_id": "fault-1",
        "trial_kind": "measured",
        "ordinal": 1,
        "scenario_id": "scenario-0",
        "failed_link": ["s3", "s9"],
        "healthy_paths": manifest_record()["scenario"]["healthy_paths"],
        "recovery_paths": {
            "h1_to_h2": ["h1", "s7", "s9", "s10", "s6", "s2", "h2"],
            "h2_to_h1": ["h2", "s2", "s6", "s10", "s9", "s7", "h1"],
        },
        "outcome": "timeout",
        "stage": "response",
        "reason": "deadline",
        "started_record_id": "trial-start",
        "ended_record_id": "trial-end",
        "cleanup_record_id": "cleanup",
        "fault_record_id": None,
        "recovery_record_id": None,
        "response_deadline_ns": None,
        "cleanup_outcome": "verified",
        "measurements": {
            name: unavailable()
            for name in [
                "setup",
                "injection",
                "recovery",
                "response",
                "stopping",
                "total",
            ]
        },
    }


@pytest.mark.parametrize(
    "outcome",
    [
        "recovered",
        "timeout",
        "invalid",
        "setup_failed",
        "injection_failed",
        "no_observed_disruption",
        "error",
    ],
)
def test_trial_schema_closes_outcomes_and_requires_separate_cleanup(outcome):
    record = trial_record()
    record["outcome"] = outcome
    assert validate_record("trial", record) == ()
    record["cleanup_outcome"] = "success"
    assert validate_record("trial", record)
    record = manifest_record()
    record["seed"] = 2**64
    assert validate_record("run", record)
    record = manifest_record()
    del record["scenario"]["ports_by_switch"]
    assert validate_record("run", record)


def summary_record():
    counts = {
        "requested": 1,
        "started": 1,
        "finalized": 1,
        "cleanup_completed": 1,
        "unknown": 0,
        "unstarted": 0,
        "outcomes": {
            name: int(name == "setup_failed")
            for name in [
                "recovered",
                "timeout",
                "invalid",
                "setup_failed",
                "injection_failed",
                "no_observed_disruption",
                "error",
            ]
        },
    }
    return envelope("summary", 17) | {
        "state": "completed",
        "terminal_reason": "attempts_completed",
        "counts": {
            "measured": counts,
            "warmup": dict(
                counts,
                requested=0,
                started=0,
                finalized=0,
                cleanup_completed=0,
                outcomes={k: 0 for k in counts["outcomes"]},
            ),
        },
        "distributions": [],
        "rates": [],
    }


def test_summary_requires_explicit_counts_and_completion_state():
    record = summary_record()
    assert validate_record("summary", record) == ()
    record["state"] = "success"
    assert validate_record("summary", record)


def artifact_records():
    events = []
    for seq, (record_id, kind, data) in enumerate(
        [
            ("trial-start", "trial_started", EVENT_DATA["trial_started"]),
            (
                "trial-end",
                "trial_ended",
                {
                    "outcome": "setup_failed",
                    "stage": "setup",
                    "reason": "deployment_failed",
                },
            ),
            (
                "cleanup",
                "cleanup_completed",
                {
                    "outcome": "verified",
                    "network_absent": True,
                    "workers_stopped": True,
                    "provider_contained": True,
                    "reason": "verified",
                },
            ),
            ("terminal", "run_finished", EVENT_DATA["run_finished"]),
        ],
        1,
    ):
        events.append(
            envelope(record_id, seq, str(seq))
            | {
                "trial_id": "trial-1",
                "fault_id": "fault-1",
                "type": kind,
                "data": deepcopy(data),
            }
        )
    events[-1].update(trial_id=None, fault_id=None)
    events[0]["data"]["failed_link"] = ["s3", "s9"]
    trial = trial_record()
    trial.update(
        collector_seq=5,
        tick_ns="4",
        outcome="setup_failed",
        stage="setup",
        reason="deployment_failed",
    )
    agents = []
    for i in range(1, 11):
        agent = agent_record()
        agent.update(
            record_id=f"agent-record-{i}",
            agent_id=f"agent-{i}",
            switch_id=f"s{i}",
            collector_seq=5 + i,
            tick_ns="4",
        )
        for duration in agent["measurements"].values():
            duration.update(availability="not_applicable", reason="no_confirmed_fault")
        agents.append(agent)
    summary = summary_record()
    summary.update(collector_seq=16, tick_ns="4")
    summary["distributions"] = [
        distribution(metric)
        for metric in [
            "recovery",
            "detection",
            "reasoning",
            "action_effect",
            "execution",
            "verification",
        ]
    ]
    for row in summary["distributions"]:
        count = (
            1
            if row["metric"] == "recovery"
            else (10 if row["population"] == "agent_trial" else 0)
        )
        row.update(
            not_applicable=count,
            not_applicable_reasons={"no_confirmed_fault": count} if count else {},
        )
    trial["measurements"]["recovery"].update(
        availability="not_applicable", reason="no_confirmed_fault"
    )
    summary["rates"] = [
        rate(metric, 0, int(metric == "recovery_all_started"))
        for metric in [
            "recovery_all_started",
            "recovery_valid_disrupted",
            "detection_participation",
            "action_participation",
        ]
    ]
    return {
        "run.json": manifest_record(),
        "events.jsonl": events,
        "trials.jsonl": [trial],
        "agents.jsonl": agents,
        "summary.json": summary,
    }


def group():
    return {
        "agent_id": None,
        "failed_link": None,
        "trial_outcome": None,
        "operation_outcome": None,
    }


def distribution(metric):
    return {
        "metric": metric,
        "population": "trial"
        if metric == "recovery"
        else ("action" if metric in {"execution", "verification"} else "agent_trial"),
        "unit": "seconds",
        "trial_kind": "measured",
        "group": group(),
        "complete": True,
        "available": 0,
        "unavailable": 0,
        "not_applicable": 0,
        "unavailable_reasons": {},
        "not_applicable_reasons": {},
        "included": 0,
        "late_excluded": 0,
        "count": 0,
        "mean_seconds": None,
        "median_seconds": None,
        "min_seconds": None,
        "max_seconds": None,
    }


def rate(metric, numerator, denominator):
    return {
        "metric": metric,
        "trial_kind": "measured",
        "group": group(),
        "complete": True,
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def write_artifact(directory, records):
    for name, value in records.items():
        text = (
            "".join(json.dumps(row) + "\n" for row in value)
            if name.endswith(".jsonl")
            else json.dumps(value)
        )
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def test_complete_artifact_validates_deferred_references_and_separate_cleanup(tmp_path):
    records = artifact_records()
    # Projection evidence is allowed to arrive after the raw event that refers to it.
    records["events.jsonl"][0]["evidence_ids"] = ["trial-record"]
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.issues == ()
    assert result.state == "completed"
    assert result.integrity == "valid"
    assert len(result.records) == 17


def test_interrupted_trial_retains_evidence_without_fabricated_projections(tmp_path):
    records = artifact_records()
    records["events.jsonl"] = records["events.jsonl"][:1]
    del records["summary.json"]
    del records["trials.jsonl"]
    del records["agents.jsonl"]
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.state == "incomplete"
    assert result.integrity == "valid"
    assert len(result.records) == 2


@pytest.mark.parametrize(
    "case,code",
    [
        ("duplicate_id", "duplicate_id"),
        ("sequence_gap", "sequence"),
        ("reference", "reference"),
        ("scope", "scope"),
        ("run_identity", "run_identity"),
        ("clock", "clock_domain"),
        ("outcome", "projection"),
        ("cleanup", "projection"),
        ("agent_missing", "agent_population"),
        ("agent_duplicate", "agent_population"),
        ("counts", "counts"),
        ("requested", "counts"),
        ("invented_trial", "trial_identity"),
        ("terminal_duplicate", "terminal"),
        ("recorder_loss", "recorder_loss"),
    ],
)
def test_artifact_rejects_cross_file_integrity_failures(tmp_path, case, code):
    records = artifact_records()
    agent = records["agents.jsonl"][0]
    trial = records["trials.jsonl"][0]
    if case == "duplicate_id":
        agent["record_id"] = trial["record_id"]
    elif case == "sequence_gap":
        agent["collector_seq"] = 100
    elif case == "reference":
        agent["evidence_ids"] = ["missing"]
    elif case == "scope":
        agent["fault_id"] = "another-fault"
        agent["evidence_ids"] = ["trial-end"]
    elif case == "run_identity":
        agent["run_id"] = "another-run"
    elif case == "clock":
        agent["clock_domain_id"] = "remote-clock"
    elif case == "outcome":
        trial["outcome"] = "timeout"
    elif case == "cleanup":
        trial["cleanup_outcome"] = "unknown"
    elif case == "agent_missing":
        records["agents.jsonl"].pop()
    elif case == "agent_duplicate":
        records["agents.jsonl"][1]["agent_id"] = agent["agent_id"]
    elif case == "counts":
        records["summary.json"]["counts"]["measured"]["finalized"] = 0
    elif case == "requested":
        records["summary.json"]["counts"]["warmup"]["requested"] = 1
    elif case == "invented_trial":
        agent["trial_id"] = "never-started"
    elif case == "terminal_duplicate":
        records["events.jsonl"].append(
            envelope("terminal-2", 17)
            | {
                "type": "run_aborted",
                "trial_id": None,
                "fault_id": None,
                "data": {"reason": "error"},
            }
        )
    elif case == "recorder_loss":
        records["events.jsonl"].insert(
            1,
            envelope("lost", 17)
            | {
                "type": "recorder_loss",
                "trial_id": "trial-1",
                "fault_id": "fault-1",
                "data": EVENT_DATA["recorder_loss"],
            },
        )
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert code in {issue.code for issue in result.issues}


def test_truncated_tail_is_recoverable_but_never_complete(tmp_path):
    write_artifact(tmp_path, artifact_records())
    with (tmp_path / "agents.jsonl").open("a") as stream:
        stream.write('{"record_id":')
    result = validate_artifact(tmp_path)
    assert result.state == "incomplete"
    assert result.integrity == "valid"
    assert len(result.records) == 17
    assert "truncated_tail" in {issue.code for issue in result.issues}


def test_malformed_interior_line_is_not_a_recoverable_tail(tmp_path):
    write_artifact(tmp_path, artifact_records())
    with (tmp_path / "events.jsonl").open("a") as stream:
        stream.write('{"record_id":\n')
    assert validate_artifact(tmp_path).integrity == "invalid"


def resequence(records):
    sequence = 0
    for name in [
        "run.json",
        "events.jsonl",
        "trials.jsonl",
        "agents.jsonl",
        "summary.json",
    ]:
        value = records.get(name, [])
        rows = value if isinstance(value, list) else [value]
        for row in rows:
            row["collector_seq"] = sequence
            sequence += 1


def with_reasoning():
    records = recovered_artifact()
    start = operation_event()
    start.update(record_id="reasoning-start", tick_ns="11")
    end = deepcopy(start)
    end.update(record_id="reasoning-end", type="operation_failed", tick_ns="12")
    end["data"]["outcome"] = "provider_error"
    records["events.jsonl"][3:3] = [start, end]
    agent = records["agents.jsonl"][0]
    agent["invocation_ids"] = ["invocation-1"]
    duration = {
        "availability": "available",
        "value_seconds": 1e-9,
        "reason": None,
        "start_record_id": "reasoning-start",
        "end_record_id": "reasoning-end",
        "flags": [],
    }
    agent["measurements"]["reasoning"] = deepcopy(duration)
    agent["operations"] = [
        {
            "operation_id": "operation-1",
            "phase": "reasoning",
            "state": "failed",
            "outcome": "provider_error",
            "late": False,
            "duration": duration,
            "evidence_ids": ["reasoning-start", "reasoning-end"],
        }
    ]
    row = next(
        row
        for row in records["summary.json"]["distributions"]
        if row["metric"] == "reasoning"
    )
    row.update(
        available=1,
        unavailable=9,
        unavailable_reasons={"phase_not_started": 9},
        included=1,
        count=1,
        mean_seconds=1e-9,
        median_seconds=1e-9,
        min_seconds=1e-9,
        max_seconds=1e-9,
    )
    resequence(records)
    return records


def test_failed_operation_elapsed_time_is_valid_and_collector_order_is_not_time_order(
    tmp_path,
):
    records = with_reasoning()
    # A delayed start record may be delivered after its terminal transition.
    events = records["events.jsonl"]
    start_index = next(
        i for i, event in enumerate(events) if event["record_id"] == "reasoning-start"
    )
    events[start_index : start_index + 2] = reversed(
        events[start_index : start_index + 2]
    )
    resequence(records)
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.issues == ()


@pytest.mark.parametrize(
    "case,code",
    [
        ("orphan_terminal", "transition"),
        ("duplicate_terminal", "transition"),
        ("phase_changed", "transition"),
        ("backwards", "timing"),
        ("wrong_duration", "timing"),
        ("wrong_state", "projection"),
        ("wrong_agent", "scope"),
        ("missing_start", "reference"),
    ],
)
def test_phase_evidence_and_projections_must_reconcile(tmp_path, case, code):
    records = with_reasoning()
    start = next(
        event
        for event in records["events.jsonl"]
        if event["record_id"] == "reasoning-start"
    )
    end = next(
        event
        for event in records["events.jsonl"]
        if event["record_id"] == "reasoning-end"
    )
    operation = records["agents.jsonl"][0]["operations"][0]
    if case == "orphan_terminal":
        records["events.jsonl"].remove(start)
    elif case == "duplicate_terminal":
        duplicate = deepcopy(end)
        duplicate["record_id"] = "another-end"
        records["events.jsonl"].insert(3, duplicate)
    elif case == "phase_changed":
        end["data"]["phase"] = "execution"
    elif case == "backwards":
        end["tick_ns"] = "0"
    elif case == "wrong_duration":
        operation["duration"]["value_seconds"] = 10
    elif case == "wrong_state":
        operation["state"] = "completed"
    elif case == "wrong_agent":
        end["data"]["agent_id"] = "agent-2"
    elif case == "missing_start":
        operation["duration"]["start_record_id"] = "does-not-exist"
    resequence(records)
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert code in {i.code for i in result.issues}


def with_provider(terminal=True):
    records = artifact_records()
    request = envelope("request-start", 0, "1") | {
        "type": "provider_request_started",
        "trial_id": "trial-1",
        "fault_id": "fault-1",
        "data": deepcopy(EVENT_DATA["provider_request_started"]),
    }
    records["events.jsonl"].insert(1, request)
    if terminal:
        records["events.jsonl"].insert(
            2,
            envelope("request-end", 0, "2")
            | {
                "type": "provider_request_terminal",
                "trial_id": "trial-1",
                "fault_id": "fault-1",
                "data": deepcopy(EVENT_DATA["provider_request_terminal"]),
            },
        )
    else:
        records["events.jsonl"].insert(
            2,
            envelope("request-cancel", 0, "2")
            | {
                "type": "provider_cancel_requested",
                "trial_id": "trial-1",
                "fault_id": "fault-1",
                "data": deepcopy(EVENT_DATA["provider_cancel_requested"]),
            },
        )
        records["events.jsonl"][-2]["data"].update(
            outcome="unknown", provider_contained=False, reason="provider_unknown"
        )
        records["trials.jsonl"][0]["cleanup_outcome"] = "unknown"
        records["events.jsonl"][-1].update(
            type="run_aborted", data={"reason": "provider_unknown"}
        )
        records["summary.json"].update(
            state="aborted", terminal_reason="provider_unknown"
        )
    resequence(records)
    return records


@pytest.mark.parametrize("terminal,state", [(True, "completed"), (False, "aborted")])
def test_provider_response_or_unknown_containment_preserves_truthful_run_state(
    tmp_path, terminal, state
):
    result = validate_artifact(write_artifact(tmp_path, with_provider(terminal)))
    assert result.integrity == "valid", result.issues
    assert result.state == state


def test_local_cancellation_cannot_certify_provider_containment(tmp_path):
    records = with_provider(False)
    records["events.jsonl"][-2]["data"].update(
        outcome="verified", provider_contained=True
    )
    records["trials.jsonl"][0]["cleanup_outcome"] = "verified"
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert "containment" in {i.code for i in result.issues}


def test_missing_summary_or_terminal_never_implies_completed_run(tmp_path):
    records = artifact_records()
    del records["summary.json"]
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.state == "incomplete"
    assert result.integrity == "valid"


@pytest.mark.parametrize(
    "case,code",
    [
        ("admission", "admission"),
        ("cleanup", "cleanup"),
        ("summary_reason", "terminal"),
    ],
)
def test_closure_cleanup_and_terminal_claims_require_matching_evidence(
    tmp_path, case, code
):
    records = artifact_records()
    if case == "admission":
        closed = envelope("closed", 0, "1") | {
            "type": "admission_closed",
            "trial_id": "trial-1",
            "fault_id": "fault-1",
            "data": {"reason": "stop"},
        }
        admitted = envelope("admitted", 0, "2") | {
            "type": "admission",
            "trial_id": "trial-1",
            "fault_id": "fault-1",
            "data": deepcopy(EVENT_DATA["admission"]),
        }
        records["events.jsonl"][1:1] = [closed, admitted]
    elif case == "cleanup":
        records["events.jsonl"][2]["data"]["network_absent"] = False
    elif case == "summary_reason":
        records["summary.json"]["terminal_reason"] = "invented"
    resequence(records)
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert code in {i.code for i in result.issues}


def recovered_artifact():
    records = artifact_records()
    extra = []
    for record_id, kind, tick, data in [
        (
            "fault-observation",
            "observation",
            "10",
            dict(
                EVENT_DATA["observation"],
                scheduled_ns="9",
                started_ns="9",
                completed_ns="10",
            ),
        ),
        (
            "fault",
            "fault_confirmed",
            "10",
            {"observation_record_id": "fault-observation"},
        ),
        (
            "disruption",
            "probe_round",
            "20",
            dict(
                EVENT_DATA["probe_round"],
                purpose="disruption",
                slot=0,
                scheduled_ns="11",
                started_ns="11",
                completed_ns="20",
                directions=[
                    dict(d, success=False)
                    for d in EVENT_DATA["probe_round"]["directions"]
                ],
            ),
        ),
    ]:
        extra.append(
            envelope(record_id, 0, tick)
            | {
                "type": kind,
                "trial_id": "trial-1",
                "fault_id": "fault-1",
                "data": deepcopy(data),
            }
        )
    for slot in range(1, 4):
        tick = slot * 1_000_000_000
        data = deepcopy(EVENT_DATA["probe_round"])
        data.update(
            probe_round_id=f"probe-{slot}",
            slot=slot,
            scheduled_ns=str(tick),
            started_ns=str(tick),
            completed_ns=str(tick + 10),
        )
        extra.append(
            envelope(f"probe-record-{slot}", 0, str(tick + 10))
            | {
                "type": "probe_round",
                "trial_id": "trial-1",
                "fault_id": "fault-1",
                "data": data,
            }
        )
    extra.append(
        envelope("recovery", 0, "3000000010")
        | {
            "type": "recovery_confirmed",
            "trial_id": "trial-1",
            "fault_id": "fault-1",
            "data": deepcopy(EVENT_DATA["recovery_confirmed"]),
        }
    )
    records["events.jsonl"][1:1] = extra
    records["events.jsonl"][-3].update(
        tick_ns="3000000010",
        data={"outcome": "recovered", "stage": "response", "reason": "confirmed"},
    )
    records["events.jsonl"][-2]["tick_ns"] = "3000000020"
    records["events.jsonl"][-1]["tick_ns"] = "3000000030"
    trial = records["trials.jsonl"][0]
    trial.update(
        outcome="recovered",
        stage="response",
        reason="confirmed",
        tick_ns="3000000020",
        fault_record_id="fault",
        recovery_record_id="recovery",
        response_deadline_ns="120000000010",
    )
    trial["measurements"]["recovery"] = {
        "availability": "available",
        "value_seconds": 3,
        "reason": None,
        "start_record_id": "fault",
        "end_record_id": "recovery",
        "flags": [],
    }
    for agent in records["agents.jsonl"]:
        agent["tick_ns"] = "3000000020"
        for duration in agent["measurements"].values():
            duration.update(availability="unavailable", reason="phase_not_started")
    records["summary.json"]["tick_ns"] = "3000000030"
    records["summary.json"]["counts"]["measured"]["outcomes"].update(
        setup_failed=0, recovered=1
    )
    rows = records["summary.json"]["distributions"]
    rows[0].update(
        available=1,
        not_applicable=0,
        not_applicable_reasons={},
        included=1,
        count=1,
        mean_seconds=3,
        median_seconds=3,
        min_seconds=3,
        max_seconds=3,
    )
    for row in rows[1:4]:
        row.update(
            unavailable=10,
            unavailable_reasons={"phase_not_started": 10},
            not_applicable=0,
            not_applicable_reasons={},
        )
    records["summary.json"]["rates"] = [
        rate("recovery_all_started", 1, 1),
        rate("recovery_valid_disrupted", 1, 1),
        rate("detection_participation", 0, 10),
        rate("action_participation", 0, 10),
    ]
    resequence(records)
    return records


def test_confirmed_recovery_has_three_bidirectional_rounds_and_a_separate_cleanup(
    tmp_path,
):
    result = validate_artifact(write_artifact(tmp_path, recovered_artifact()))
    assert result.issues == ()
    assert result.state == "completed"


@pytest.mark.parametrize(
    "case",
    [
        "direction",
        "link",
        "missed_slot",
        "late",
        "unconfirmed_fault",
        "wrong_projection",
        "no_disruption",
    ],
)
def test_recovery_claims_require_complete_matching_evidence(tmp_path, case):
    records = recovered_artifact()
    probe = next(
        r for r in records["events.jsonl"] if r["record_id"] == "probe-record-2"
    )
    trial = records["trials.jsonl"][0]
    if case == "direction":
        probe["data"]["directions"][1]["source"] = "h1"
    elif case == "link":
        probe["data"]["link_after"] = ["up", "down"]
    elif case == "missed_slot":
        probe["data"]["slot"] = 5
    elif case == "late":
        probe["data"]["late"] = True
    elif case == "unconfirmed_fault":
        trial["fault_record_id"] = None
    elif case == "wrong_projection":
        trial["measurements"]["recovery"]["end_record_id"] = "probe-record-1"
        trial["measurements"]["recovery"]["value_seconds"] = 1
    elif case == "no_disruption":
        disruption = next(
            r for r in records["events.jsonl"] if r["record_id"] == "disruption"
        )
        for direction in disruption["data"]["directions"]:
            direction["success"] = True
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert "recovery" in {i.code for i in result.issues}


@pytest.mark.parametrize(
    "case",
    [
        "mean",
        "population",
        "missing_count",
        "denominator",
        "empty_rate",
        "missing_totals",
    ],
)
def test_summary_populations_reconcile_with_persisted_projections(tmp_path, case):
    records = recovered_artifact()
    summary = records["summary.json"]
    if case == "mean":
        summary["distributions"][0]["mean_seconds"] = 1
    elif case == "population":
        summary["distributions"][0]["population"] = "agent_trial"
    elif case == "missing_count":
        summary["distributions"][1]["unavailable"] = 0
    elif case == "denominator":
        summary["rates"][0]["denominator"] = 10
    elif case == "empty_rate":
        summary["rates"][0].update(numerator=0, denominator=0, value=0)
    elif case == "missing_totals":
        summary["distributions"] = []
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert "summary" in {i.code for i in result.issues}


FIXTURES = Path(__file__).parents[2] / "docs/fixtures/link-failure-latency-v2"


@pytest.mark.parametrize("case", json.loads((FIXTURES / "cases.json").read_text()))
def test_published_artifact_fixtures(tmp_path, case):
    expected = json.loads((FIXTURES / "cases.json").read_text())[case]
    directory = shutil.copytree(FIXTURES / case, tmp_path / case)
    result = validate_artifact(directory)
    assert result.state == expected["state"], result.issues
    assert result.integrity == expected["integrity"], result.issues
    if expected["code"]:
        assert expected["code"] in {i.code for i in result.issues}


@pytest.mark.parametrize("valid", [True, False])
def test_published_record_fixtures_cover_every_family_and_event_variant(valid):
    cases = json.loads(
        (FIXTURES / f"records-{'valid' if valid else 'invalid'}.json").read_text()
    )
    assert {case["family"] for case in cases} == {
        "run",
        "event",
        "trial",
        "agent",
        "summary",
    }
    for case in cases:
        assert bool(validate_record(case["family"], case["record"])) is not valid, case


@pytest.mark.parametrize("tick", ["01", "+1", "-1", "1.0", "1\n", "", " 1"])
def test_raw_ticks_are_canonical_decimal_strings(tick):
    record = operation_event()
    record["tick_ns"] = tick
    assert validate_record("event", record)


@pytest.mark.parametrize(
    "tail", ['{"record_id":"first","record_id":"second"}', '{"value":NaN}', "{garbage"]
)
def test_invalid_json_without_newline_is_not_misreported_as_a_torn_tail(tmp_path, tail):
    write_artifact(tmp_path, artifact_records())
    with (tmp_path / "agents.jsonl").open("a") as stream:
        stream.write(tail)
    result = validate_artifact(tmp_path)
    assert result.integrity == "invalid"


def test_torn_utf8_tail_preserves_complete_preceding_records(tmp_path):
    write_artifact(tmp_path, artifact_records())
    with (tmp_path / "agents.jsonl").open("ab") as stream:
        stream.write(b'{"reason":"\xc3')
    result = validate_artifact(tmp_path)
    assert result.integrity == "valid", result.issues
    assert result.state == "incomplete"
    assert len(result.records) == 17


@pytest.mark.parametrize("kind", ["trial_ended", "cleanup_completed", "run_finished"])
def test_lifecycle_evidence_cannot_precede_its_required_predecessor(tmp_path, kind):
    records = artifact_records()
    next(e for e in records["events.jsonl"] if e["type"] == kind)["tick_ns"] = "0"
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert "lifecycle" in {i.code for i in result.issues}


@pytest.mark.parametrize("confirmed_fault", [False, True])
def test_detection_cannot_use_arbitrary_equal_tick_evidence(tmp_path, confirmed_fault):
    records = recovered_artifact() if confirmed_fault else artifact_records()
    duration = records["agents.jsonl"][0]["measurements"]["detection"]
    duration.update(
        availability="available",
        value_seconds=0,
        reason=None,
        start_record_id="trial-start",
        end_record_id="trial-start",
    )
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert "measurement" in {i.code for i in result.issues}


@pytest.mark.parametrize(
    "case", ["restored_outside_selected_rounds", "missed_slot_backfilled"]
)
def test_recovery_cannot_ignore_contradictory_raw_evidence(tmp_path, case):
    records = recovered_artifact()
    if case == "restored_outside_selected_rounds":
        disruption = next(
            e for e in records["events.jsonl"] if e["record_id"] == "disruption"
        )
        disruption["data"].update(link_before=["up", "up"], link_after=["up", "up"])
    else:
        records["events.jsonl"].append(
            envelope("missed-probe-2", 0, "2000000000")
            | {
                "type": "slot_missed",
                "trial_id": "trial-1",
                "fault_id": "fault-1",
                "data": {
                    "schedule": "probe",
                    "slot": 2,
                    "scheduled_ns": "2000000000",
                    "reason": "overrun",
                },
            }
        )
        resequence(records)
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert "recovery" in {i.code for i in result.issues}


def test_finalized_agent_cannot_silently_drop_raw_phase_operations(tmp_path):
    records = with_reasoning()
    records["agents.jsonl"][0]["operations"] = []
    result = validate_artifact(write_artifact(tmp_path, records))
    assert result.integrity == "invalid"
    assert "projection" in {i.code for i in result.issues}
    record = summary_record()
    del record["counts"]["measured"]["unknown"]
    assert validate_record("summary", record)
