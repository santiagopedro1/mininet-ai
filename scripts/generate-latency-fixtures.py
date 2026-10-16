"""Publish deterministic synthetic conformance fixtures (run from the repo root).

The fixture recipes live beside the public-boundary conformance tests. This is
development tooling, not an experiment runner, schema generator, or live evidence.
"""

import json
from copy import deepcopy
from pathlib import Path

from tests.experiments.test_latency_artifacts import (
    EVENT_DATA,
    agent_record,
    artifact_records,
    envelope,
    manifest_record,
    operation_event,
    recovered_artifact,
    summary_record,
    trial_record,
    with_provider,
    with_reasoning,
    write_artifact,
)


def publish(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)

    def write(name, value):
        (destination / name).write_text(
            json.dumps(value, indent=2, allow_nan=False) + "\n"
        )

    valid = [
        {"family": family, "record": record}
        for family, record in (
            ("run", manifest_record()),
            ("agent", agent_record()),
            ("trial", trial_record()),
            ("summary", summary_record()),
        )
    ]
    for transition in (
        "operation_started",
        "operation_completed",
        "operation_failed",
        "operation_cancelled",
    ):
        record = operation_event()
        record["type"] = transition
        if transition != "operation_started":
            record["data"]["outcome"] = "fixture_terminal"
        valid.append({"family": "event", "record": record})
    for kind, data in EVENT_DATA.items():
        record = envelope(kind, 1) | {
            "type": kind,
            "trial_id": "trial-1",
            "fault_id": "fault-1",
            "data": deepcopy(data),
        }
        if kind in {"run_finished", "run_aborted"}:
            record.update(trial_id=None, fault_id=None)
        valid.append({"family": "event", "record": record})
    write("records-valid.json", valid)
    invalid = deepcopy(valid)
    for case in invalid:
        case["record"]["schema"] = "mininet-ai/link-failure-latency/v99"
    write("records-invalid.json", invalid)

    cases = {}

    def artifact(name, records, state, integrity, code=None):
        path = destination / name
        path.mkdir(exist_ok=True)
        write_artifact(path, records)
        cases[name] = {"state": state, "integrity": integrity, "code": code}

    artifact("complete-setup-failure", artifact_records(), "completed", "valid")
    artifact("complete-recovery", recovered_artifact(), "completed", "valid")
    artifact("failed-phase", with_reasoning(), "completed", "valid")
    artifact("provider-terminal-response", with_provider(True), "completed", "valid")
    artifact("provider-unknown-abort", with_provider(False), "aborted", "valid")
    records = artifact_records()
    records["events.jsonl"] = records["events.jsonl"][:1]
    for key in ("trials.jsonl", "agents.jsonl", "summary.json"):
        del records[key]
    artifact("interrupted-started-trial", records, "incomplete", "valid")
    records = artifact_records()
    records["agents.jsonl"][0]["evidence_ids"] = ["missing-record"]
    artifact("invalid-reference", records, "completed", "invalid", "reference")
    records = artifact_records()
    records["agents.jsonl"][0]["record_id"] = "trial-record"
    artifact("invalid-duplicate", records, "completed", "invalid", "duplicate_id")
    records = artifact_records()
    records["summary.json"]["counts"]["measured"]["finalized"] = 0
    artifact("invalid-counts", records, "completed", "invalid", "counts")
    records = with_provider(False)
    records["events.jsonl"][-2]["data"].update(
        outcome="verified", provider_contained=True
    )
    records["trials.jsonl"][0]["cleanup_outcome"] = "verified"
    artifact(
        "invalid-local-cancellation-proof", records, "aborted", "invalid", "containment"
    )
    records = artifact_records()
    records["run.json"]["configuration"]["warmup_attempts"] = 1
    records["run.json"]["scenario"]["failure_order"]["warmup"] = [["s3", "s9"]]
    records["events.jsonl"][0]["data"]["trial_kind"] = "warmup"
    records["events.jsonl"][-1].update(
        type="run_aborted", data={"reason": "warmup_failed"}
    )
    records["trials.jsonl"][0]["trial_kind"] = "warmup"
    summary = records["summary.json"]
    summary.update(state="aborted", terminal_reason="warmup_failed")
    summary["counts"]["warmup"] = deepcopy(summary["counts"]["measured"])
    summary["counts"]["measured"].update(
        started=0,
        finalized=0,
        cleanup_completed=0,
        unstarted=1,
        outcomes={key: 0 for key in summary["counts"]["measured"]["outcomes"]},
    )
    for row in summary["distributions"]:
        row.update(not_applicable=0, not_applicable_reasons={})
    summary["rates"][0].update(denominator=0, value=None)
    artifact("warmup-failure-abort", records, "aborted", "valid")
    records = artifact_records()
    artifact("truncated-tail", records, "incomplete", "valid", "truncated_tail")
    with (destination / "truncated-tail/agents.jsonl").open("a") as stream:
        stream.write('{"record_id":')
    write("cases.json", cases)


if __name__ == "__main__":
    publish(Path("docs/fixtures/link-failure-latency-v2"))
