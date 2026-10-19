"""Validate local records against bundled, offline Draft 2020-12 schemas."""

import itertools
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from statistics import mean, median
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    location: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class ArtifactValidation:
    """Persisted state and integrity are independent; an aborted run can be valid."""

    state: str
    integrity: str
    issues: tuple[ValidationIssue, ...]
    incomplete_reasons: tuple[str, ...]
    records: tuple[dict[str, Any], ...]


@cache
def _validators() -> dict[str, Draft202012Validator]:
    schemas = {}
    for path in files(__package__).joinpath("schemas").iterdir():
        if path.name.endswith(".json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            schemas[path.name.removesuffix(".schema.json")] = schema
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return {
        name: Draft202012Validator(
            schema, registry=registry, format_checker=FormatChecker()
        )
        for name, schema in schemas.items()
    }


def validate_record(family: str, record: Any) -> tuple[ValidationIssue, ...]:
    """Return diagnostics, never coerce or repair evidence. Unknown families raise."""
    if family not in _validators() or family == "common":
        raise ValueError(f"unknown record family: {family}")
    issues = [
        ValidationIssue(
            "schema", "/" + "/".join(map(str, error.absolute_path)), error.message
        )
        for error in _validators()[family].iter_errors(record)
    ]

    def finite(value: Any, path: str) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            issues.append(
                ValidationIssue("non_finite", path, "JSON numbers must be finite")
            )
        elif isinstance(value, dict):
            for key, child in value.items():
                finite(child, f"{path}/{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                finite(child, f"{path}/{index}")

    finite(record, "")
    return tuple(issues)


def validate_artifact(directory: Path | str) -> ArtifactValidation:
    """Read evidence without modifying it. Missing final artifacts are not success."""
    directory = Path(directory)
    issues: list[ValidationIssue] = []
    incomplete: list[str] = []
    records: list[dict[str, Any]] = []
    families: dict[str, list[dict[str, Any]]] = {}
    for filename, family in (
        ("run.json", "run"),
        ("events.jsonl", "event"),
        ("trials.jsonl", "trial"),
        ("agents.jsonl", "agent"),
        ("summary.json", "summary"),
    ):
        families[family] = []
        path = directory / filename
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            incomplete.append(f"missing:{filename}")
            if family == "run":
                issues.append(
                    ValidationIssue(
                        "manifest_missing", filename, "run manifest is required"
                    )
                )
            continue
        except OSError as error:
            issues.append(ValidationIssue("read", filename, str(error)))
            incomplete.append(f"unreadable:{filename}")
            continue
        lines = (
            content.splitlines(keepends=True)
            if filename.endswith(".jsonl")
            else [content]
        )
        for index, line in enumerate(lines):
            location = f"{filename}:{index + 1}"
            try:
                record = json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                )
            except (ValueError, RecursionError) as error:
                at_end = (
                    filename.endswith(".jsonl")
                    and index == len(lines) - 1
                    and not line.endswith(b"\n")
                )
                syntax_tail = isinstance(error, json.JSONDecodeError) and (
                    error.pos >= len(error.doc.rstrip())
                    or error.msg.startswith("Unterminated string")
                )
                utf8_tail = (
                    isinstance(error, UnicodeDecodeError)
                    and error.reason == "unexpected end of data"
                )
                tail = at_end and (syntax_tail or utf8_tail)
                if tail:
                    incomplete.append(f"truncated:{filename}")
                issues.append(
                    ValidationIssue(
                        "truncated_tail" if tail else "json",
                        location,
                        str(error),
                        "warning" if tail else "error",
                    )
                )
                continue
            local = validate_record(family, record)
            issues.extend(
                ValidationIssue(i.code, location + i.location, i.message) for i in local
            )
            if not local:
                records.append(record)
                families[family].append(record)

    events = families["event"]
    terminals = _terminal_records(events)
    if not terminals:
        incomplete.append("missing:terminal_event")
    if families["run"]:
        _ArtifactChecks(families, issues, incomplete).check()
    state = _artifact_state(terminals, incomplete)
    return ArtifactValidation(
        state,
        "invalid" if any(i.severity == "error" for i in issues) else "valid",
        tuple(issues),
        tuple(incomplete),
        tuple(sorted(records, key=lambda r: r["collector_seq"])),
    )


def _terminal_records(events):
    return [r for r in events if r["type"] in {"run_finished", "run_aborted"}]


def _artifact_state(terminals, incomplete):
    if incomplete or not terminals:
        return "incomplete"
    return "aborted" if terminals[-1]["type"] == "run_aborted" else "completed"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"non-finite JSON number: {value}")


class _ArtifactChecks:
    """Cross-file invariants, applied only to locally schema-valid records."""

    def __init__(self, families, issues, incomplete):
        self.families = families
        self.issues = issues
        self.incomplete = incomplete
        self.manifest = families["run"][0]
        self.records = [r for rows in families.values() for r in rows]
        self.by_id = {r["record_id"]: r for r in self.records}
        self.events = families["event"]
        self.terminals = _terminal_records(self.events)
        self.starts = {
            r["trial_id"]: r for r in self.events if r["type"] == "trial_started"
        }
        self.ends = {
            r["trial_id"]: r for r in self.events if r["type"] == "trial_ended"
        }
        self.cleanups = {
            r["trial_id"]: r for r in self.events if r["type"] == "cleanup_completed"
        }
        self.trials = {r["trial_id"]: r for r in families["trial"]}

    def error(self, code, record, message):
        self.issues.append(ValidationIssue(code, record["record_id"], message))

    def check(self):
        self.identities()
        self.references()
        self.projections()
        self.operations()
        self.durations()
        self.measurements()
        self.lifecycle()
        self.recovery()
        self.counts()
        self.summaries()

    def identities(self):
        for identity, count in Counter(r["record_id"] for r in self.records).items():
            if count > 1:
                self.error(
                    "duplicate_id",
                    self.by_id[identity],
                    "record identity is not unique",
                )
        sequences = sorted(r["collector_seq"] for r in self.records)
        if sequences != list(range(len(sequences))):
            self.error(
                "sequence",
                self.manifest,
                "collector sequences must be unique and contiguous from zero",
            )
        if self.manifest["collector_seq"] != 0:
            self.error(
                "sequence", self.manifest, "the manifest must be the first record"
            )
        assigned = self.manifest["agents"]
        if (
            len({a["agent_id"] for a in assigned}) != 10
            or len({a["switch_id"] for a in assigned}) != 10
        ):
            self.error(
                "agent_population",
                self.manifest,
                "assign exactly ten unique agents to ten unique switches",
            )
        for r in self.records:
            if r["run_id"] != self.manifest["run_id"]:
                self.error("run_identity", r, "record belongs to another run")
            if r["clock_domain_id"] != self.manifest["clock_domain_id"]:
                self.error(
                    "clock_domain",
                    r,
                    "experiment timestamps must share the manifest clock domain",
                )
            trial_id = r.get("trial_id")
            if trial_id is not None:
                start = self.starts.get(trial_id)
                if start is None:
                    self.error(
                        "trial_identity", r, "record refers to an unstarted trial"
                    )
                elif r["fault_id"] != start["fault_id"]:
                    self.error(
                        "scope", r, "fault identity disagrees with the trial start"
                    )
            elif r.get("fault_id") is not None:
                self.error("scope", r, "a fault identity requires a trial identity")
            if r.get("type") == "recorder_loss":
                self.error("recorder_loss", r, "required evidence was lost")
        for kind in ("trial_started", "trial_ended", "cleanup_completed"):
            selected = [r for r in self.events if r["type"] == kind]
            if len({r["trial_id"] for r in selected}) != len(selected):
                self.error(
                    "trial_identity", self.manifest, f"duplicate {kind} for a trial"
                )
        if len(self.terminals) > 1:
            self.error(
                "terminal", self.terminals[-1], "a run has at most one terminal event"
            )
        if len(self.trials) != len(self.families["trial"]):
            self.error(
                "projection", self.manifest, "duplicate finalized trial projections"
            )

    def references(self):
        def walk(value: Any):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith("_record_id") and child is not None:
                        yield child
                    elif key in {"evidence_ids", "probe_record_ids"} and isinstance(
                        child, list
                    ):
                        yield from child
                    else:
                        yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        for r in self.records:
            for identity in walk(r):
                target = self.by_id.get(identity)
                if target is None:
                    self.error("reference", r, f"missing referenced record: {identity}")
                    continue
                for key in ("run_id", "trial_id", "fault_id", "agent_id"):
                    left, right = r.get(key), target.get(key)
                    if left is not None and right is not None and left != right:
                        self.error("scope", r, f"reference crosses {key}: {identity}")

    def expected(self, record, field, event_type):
        event = self.by_id.get(record[field])
        if event is not None and event.get("type") != event_type:
            self.error("projection", record, f"{field} must refer to {event_type}")
            return None
        return event

    def projections(self):
        assigned = {a["agent_id"]: a["switch_id"] for a in self.manifest["agents"]}
        for trial_id, trial in self.trials.items():
            start = self.expected(trial, "started_record_id", "trial_started")
            end = self.expected(trial, "ended_record_id", "trial_ended")
            cleanup = self.expected(trial, "cleanup_record_id", "cleanup_completed")
            if start:
                for key in ("trial_kind", "ordinal", "scenario_id", "failed_link"):
                    if trial[key] != start["data"][key]:
                        self.error(
                            "projection", trial, f"{key} differs from start evidence"
                        )
            if end:
                for key in ("outcome", "stage", "reason"):
                    if trial[key] != end["data"][key]:
                        self.error(
                            "projection",
                            trial,
                            f"{key} differs from frozen end evidence",
                        )
            if cleanup and trial["cleanup_outcome"] != cleanup["data"]["outcome"]:
                self.error(
                    "projection", trial, "cleanup outcome differs from cleanup evidence"
                )
            if cleanup and int(trial["tick_ns"]) < int(cleanup["tick_ns"]):
                self.error("projection", trial, "projection finalized before cleanup")
            agents = [a for a in self.families["agent"] if a["trial_id"] == trial_id]
            actual = {a["agent_id"]: a["switch_id"] for a in agents}
            if len(agents) != 10 or actual != assigned:
                self.error(
                    "agent_population",
                    trial,
                    "finalized trial needs exactly its ten assigned agent projections",
                )
        for a in self.families["agent"]:
            if assigned.get(a["agent_id"]) != a["switch_id"]:
                self.error(
                    "agent_population",
                    a,
                    "agent/switch assignment differs from manifest",
                )
        for trial_id in self.starts:
            if trial_id not in self.trials:
                self.incomplete.append(f"missing:trial_projection:{trial_id}")

    def counts(self):
        for kind in ("warmup", "measured"):
            starts = [
                s for s in self.starts.values() if s["data"]["trial_kind"] == kind
            ]
            ids = {s["trial_id"] for s in starts}
            requested = self.manifest["configuration"][f"{kind}_attempts"]
            if len(starts) > requested:
                self.error(
                    "counts",
                    self.manifest,
                    f"started more {kind} attempts than requested",
                )
            ordinals = sorted(s["data"]["ordinal"] for s in starts)
            if ordinals != list(range(1, len(starts) + 1)):
                self.error(
                    "counts", self.manifest, f"noncontiguous {kind} attempt ordinals"
                )
            for summary in self.families["summary"]:
                counts = summary["counts"][kind]
                ended = [self.ends[i] for i in ids if i in self.ends]
                expected = {
                    "requested": requested,
                    "started": len(starts),
                    "finalized": len(ended),
                    "cleanup_completed": len(ids & self.cleanups.keys()),
                    "unknown": len(starts) - len(ended),
                    "unstarted": requested - len(starts),
                }
                if any(counts[key] != value for key, value in expected.items()):
                    self.error(
                        "counts",
                        summary,
                        f"{kind} counts disagree with persisted lifecycle evidence",
                    )
                outcomes = Counter(e["data"]["outcome"] for e in ended)
                if any(
                    value != outcomes[key] for key, value in counts["outcomes"].items()
                ):
                    self.error(
                        "counts",
                        summary,
                        f"{kind} outcome counts disagree with frozen outcomes",
                    )
                state = _artifact_state(self.terminals, self.incomplete)
                # An incomplete prefix may retain the summary written before a torn append.
                if not self.incomplete and summary["state"] != state:
                    self.error(
                        "terminal",
                        summary,
                        "summary completion state disagrees with terminal evidence",
                    )

    def operations(self):
        grouped = defaultdict(list)
        for event in self.events:
            if event["type"].startswith("operation_"):
                grouped[(event["trial_id"], event["data"]["operation_id"])].append(
                    event
                )
        for events in grouped.values():
            starts = [e for e in events if e["type"] == "operation_started"]
            ends = [e for e in events if e["type"] != "operation_started"]
            if len(starts) != 1 or len(ends) > 1:
                self.error(
                    "transition",
                    events[0],
                    "operation needs exactly one start and at most one terminal transition",
                )
                continue
            start = starts[0]
            for end in ends:
                for field in ("phase", "agent_id", "invocation_id", "proposal_id"):
                    if start["data"][field] != end["data"][field]:
                        self.error(
                            "scope" if field != "phase" else "transition",
                            end,
                            f"operation {field} changed",
                        )
                if int(end["tick_ns"]) < int(start["tick_ns"]):
                    self.error("timing", end, "operation terminates before its start")
        for agent in self.families["agent"]:
            raw_ids = {
                e["data"]["operation_id"]
                for e in self.events
                if e["trial_id"] == agent["trial_id"]
                and e["type"].startswith("operation_")
                and e["data"]["agent_id"] == agent["agent_id"]
            }
            projected_ids = {op["operation_id"] for op in agent["operations"]}
            if not raw_ids <= projected_ids:
                self.error(
                    "projection",
                    agent,
                    "finalized agent projection omits recorded phase operations",
                )
            seen = set()
            for operation in agent["operations"]:
                identity = operation["operation_id"]
                if identity in seen:
                    self.error("projection", agent, "duplicate operation projection")
                seen.add(identity)
                events = grouped.get((agent["trial_id"], identity), [])
                ends = [e for e in events if e["type"] != "operation_started"]
                expected = {
                    "operation_completed": "completed",
                    "operation_failed": "failed",
                    "operation_cancelled": "cancelled",
                }
                state = (
                    expected[ends[0]["type"]]
                    if ends
                    else ("incomplete" if events else "not_started")
                )
                if operation["state"] != state:
                    self.error(
                        "projection",
                        agent,
                        "operation state disagrees with raw transitions",
                    )
                for event in events:
                    if (
                        event["data"]["agent_id"] != agent["agent_id"]
                        or event["data"]["phase"] != operation["phase"]
                    ):
                        self.error(
                            "scope",
                            agent,
                            "operation projection belongs to another agent or phase",
                        )
                if ends:
                    end = ends[0]
                    if operation["outcome"] != end["data"]["outcome"]:
                        self.error(
                            "projection",
                            agent,
                            "operation outcome differs from terminal evidence",
                        )
                    trial_end = self.ends.get(agent["trial_id"])
                    if trial_end and operation["late"] != (
                        int(end["tick_ns"]) > int(trial_end["tick_ns"])
                    ):
                        self.error(
                            "projection",
                            agent,
                            "operation lateness disagrees with frozen trial end",
                        )

    def durations(self):
        def walk(value: Any):
            if isinstance(value, dict):
                if "availability" in value:
                    yield value
                else:
                    for child in value.values():
                        yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        for record in self.records:
            for duration in walk(record):
                start = self.by_id.get(duration["start_record_id"])
                end = self.by_id.get(duration["end_record_id"])
                if duration["availability"] != "available":
                    continue
                if not start or not end:
                    continue  # Reference diagnostics retain the missing identities.
                elapsed = int(end["tick_ns"]) - int(start["tick_ns"])
                if (
                    start["clock_domain_id"] != end["clock_domain_id"]
                    or elapsed < 0
                    or not math.isclose(
                        duration["value_seconds"],
                        elapsed / 1_000_000_000,
                        rel_tol=1e-12,
                        abs_tol=1e-15,
                    )
                ):
                    self.error(
                        "timing",
                        record,
                        "available duration disagrees with same-domain raw ticks",
                    )

    def lifecycle(self):
        for trial_id, start in self.starts.items():
            end = self.ends.get(trial_id)
            cleanup = self.cleanups.get(trial_id)
            if end and int(end["tick_ns"]) < int(start["tick_ns"]):
                self.error("lifecycle", end, "trial ended before it started")
            if cleanup and (not end or int(cleanup["tick_ns"]) < int(end["tick_ns"])):
                self.error(
                    "lifecycle",
                    cleanup,
                    "cleanup completion requires an earlier frozen trial end",
                )
        for terminal in self.events:
            if terminal["type"] in {"run_finished", "run_aborted"}:
                predecessors = [
                    *self.starts.values(),
                    *self.ends.values(),
                    *self.cleanups.values(),
                ]
                if any(
                    int(r["tick_ns"]) > int(terminal["tick_ns"]) for r in predecessors
                ):
                    self.error(
                        "lifecycle",
                        terminal,
                        "run terminal event precedes retained trial lifecycle evidence",
                    )
        requests = {}
        completed = {}
        for event in sorted(
            self.events, key=lambda r: (int(r["tick_ns"]), r["collector_seq"])
        ):
            kind, data = event["type"], event["data"]
            if kind == "provider_request_started":
                if data["request_id"] in requests:
                    self.error(
                        "containment", event, "duplicate Provider request identity"
                    )
                requests[data["request_id"]] = event
            elif kind in {"provider_request_terminal", "provider_cancel_requested"}:
                start = requests.get(data["request_id"])
                if not start or start["trial_id"] != event["trial_id"]:
                    self.error(
                        "containment",
                        event,
                        "Provider evidence has no matching same-trial request",
                    )
                elif kind == "provider_request_terminal":
                    if data["request_id"] in completed:
                        self.error(
                            "containment", event, "duplicate Provider terminal evidence"
                        )
                    completed[data["request_id"]] = event
            elif kind == "cleanup_completed":
                outstanding = [
                    key
                    for key, start in requests.items()
                    if start["trial_id"] == event["trial_id"] and key not in completed
                ]
                if data["provider_contained"] is True and outstanding:
                    self.error(
                        "containment",
                        event,
                        "local cancellation does not clear outstanding Provider requests",
                    )
                if data["outcome"] == "verified" and not all(
                    data[key] is True
                    for key in (
                        "network_absent",
                        "workers_stopped",
                        "provider_contained",
                    )
                ):
                    self.error(
                        "cleanup",
                        event,
                        "verified cleanup requires all three containment proofs",
                    )
            elif kind == "admission" and data["accepted"]:
                closures = [
                    r
                    for r in self.events
                    if r["trial_id"] == event["trial_id"]
                    and r["type"] in {"admission_closed", "trial_ended"}
                ]
                if any(
                    int(r["tick_ns"]) < int(event["tick_ns"])
                    or (
                        r["tick_ns"] == event["tick_ns"]
                        and r["collector_seq"] < event["collector_seq"]
                    )
                    for r in closures
                ):
                    self.error(
                        "admission",
                        event,
                        "new work admitted after trial admission closed",
                    )

        ordered = sorted(self.starts.values(), key=lambda r: int(r["tick_ns"]))
        for previous, current in itertools.pairwise(ordered):
            cleanup = self.cleanups.get(previous["trial_id"])
            end = self.ends.get(previous["trial_id"])
            if (
                not cleanup
                or cleanup["data"]["outcome"] != "verified"
                or int(cleanup["tick_ns"]) > int(current["tick_ns"])
            ):
                self.error(
                    "containment",
                    current,
                    "next attempt started without verified preceding cleanup",
                )
            if previous["data"]["trial_kind"] == "warmup" and (
                not end or end["data"]["outcome"] != "recovered"
            ):
                self.error(
                    "containment",
                    current,
                    "failed warm-up must abort before another attempt",
                )

        for event in self.events:
            if event["type"] != "run_finished":
                continue
            for trial_id, start in self.starts.items():
                cleanup = self.cleanups.get(trial_id)
                if not cleanup or cleanup["data"]["outcome"] != "verified":
                    self.error(
                        "terminal",
                        event,
                        "run finished without verified cleanup for every started attempt",
                    )
            for kind in ("warmup", "measured"):
                count = sum(
                    s["data"]["trial_kind"] == kind for s in self.starts.values()
                )
                if count != self.manifest["configuration"][f"{kind}_attempts"]:
                    self.error(
                        "terminal", event, "run finished without all requested attempts"
                    )
        for summary in self.families["summary"]:
            if (
                self.terminals
                and summary["terminal_reason"] != self.terminals[-1]["data"]["reason"]
            ):
                self.error(
                    "terminal", summary, "terminal reason disagrees with raw evidence"
                )

    def measurements(self):
        for agent in self.families["agent"]:
            trial = self.trials.get(agent["trial_id"])
            if not trial:
                continue
            for metric, duration in agent["measurements"].items():
                if trial["fault_record_id"] is None:
                    if duration["availability"] != "not_applicable":
                        self.error(
                            "measurement",
                            agent,
                            "fault-response measurements require a confirmed fault",
                        )
                    continue
                if duration["availability"] != "available":
                    continue
                start = self.by_id.get(duration["start_record_id"])
                end = self.by_id.get(duration["end_record_id"])
                if not start or not end:
                    continue
                relevant = [
                    e
                    for e in self.events
                    if e["trial_id"] == agent["trial_id"]
                    and e["data"].get("agent_id") == agent["agent_id"]
                ]
                if metric == "detection":
                    recognitions = [e for e in relevant if e["type"] == "recognition"]
                    first = min(
                        recognitions, key=lambda r: int(r["tick_ns"]), default=None
                    )
                    valid = (
                        start["record_id"] == trial["fault_record_id"]
                        and first is not None
                        and end["record_id"] == first["record_id"]
                    )
                elif metric == "reasoning":
                    valid = (
                        start.get("type") == "operation_started"
                        and start["data"].get("phase") == "reasoning"
                        and end.get("type")
                        in {
                            "operation_completed",
                            "operation_failed",
                            "operation_cancelled",
                        }
                        and start["data"]["operation_id"] == end["data"]["operation_id"]
                        and end["data"].get("phase") == "reasoning"
                        and start["data"].get("agent_id") == agent["agent_id"]
                    )
                else:
                    attempts = [
                        e
                        for e in relevant
                        if e["type"] == "operation_started"
                        and e["data"]["phase"] == "action_attempt"
                    ]
                    first = min(attempts, key=lambda r: int(r["tick_ns"]), default=None)
                    valid = (
                        first is not None
                        and start["record_id"] == first["record_id"]
                        and end["record_id"] == trial["recovery_record_id"]
                    )
                if not valid:
                    self.error(
                        "measurement",
                        agent,
                        f"{metric} references do not match its defined phase boundaries",
                    )

    def recovery(self):
        for event in self.events:
            data = event["data"]
            if event["type"] in {"observation", "probe_round"} and not (
                int(data["scheduled_ns"])
                <= int(data["started_ns"])
                <= int(data["completed_ns"])
                <= int(event["tick_ns"])
            ):
                self.error(
                    "timing",
                    event,
                    "sample scheduled/start/completion timestamps are out of order",
                )
            if event["type"] == "fault_confirmed":
                observation = self.by_id.get(data["observation_record_id"])
                if (
                    not observation
                    or observation.get("type") != "observation"
                    or observation["data"]["endpoint_states"] != ["down", "down"]
                    or observation["data"]["completed_ns"] != event["tick_ns"]
                ):
                    self.error(
                        "recovery",
                        event,
                        "confirmed onset needs a fresh observation of both endpoints down",
                    )
        for trial in self.trials.values():
            if trial["outcome"] != "recovered":
                if (
                    trial["recovery_record_id"] is not None
                    or trial["measurements"]["recovery"]["availability"] == "available"
                ):
                    self.error(
                        "recovery",
                        trial,
                        "unrecovered trial cannot have a confirmed recovery measurement",
                    )
                continue
            fault = self.by_id.get(trial["fault_record_id"])
            recovery = self.by_id.get(trial["recovery_record_id"])
            if (
                not fault
                or fault.get("type") != "fault_confirmed"
                or not recovery
                or recovery.get("type") != "recovery_confirmed"
                or trial["response_deadline_ns"] is None
            ):
                self.error(
                    "recovery",
                    trial,
                    "recovered outcome needs confirmed onset, recovery, and deadline evidence",
                )
                continue
            duration = trial["measurements"]["recovery"]
            if (
                duration["availability"] != "available"
                or duration["start_record_id"] != fault["record_id"]
                or duration["end_record_id"] != recovery["record_id"]
            ):
                self.error(
                    "recovery",
                    trial,
                    "recovery duration must span confirmed onset through confirmed recovery",
                )
            rounds = [
                self.by_id.get(identity)
                for identity in recovery["data"]["probe_record_ids"]
            ]
            if any(r is None or r.get("type") != "probe_round" for r in rounds):
                self.error(
                    "recovery", recovery, "recovery requires three probe-round records"
                )
                continue
            rounds = [r for r in rounds if r is not None]
            trial_end = self.ends.get(trial["trial_id"])
            for raw in self.events:
                if raw["trial_id"] != trial["trial_id"]:
                    continue
                data = raw["data"]
                if raw["type"] == "probe_round" and data["purpose"] == "baseline":
                    continue
                if (
                    raw["type"] == "slot_missed"
                    and data["schedule"] == "probe"
                    and any(
                        data["slot"] == probe["data"]["slot"]
                        and data["scheduled_ns"] == probe["data"]["scheduled_ns"]
                        for probe in rounds
                    )
                ):
                    self.error(
                        "recovery",
                        recovery,
                        "a missed scheduled slot cannot be backfilled by later probe evidence",
                    )
                if trial_end and int(fault["tick_ns"]) <= int(raw["tick_ns"]) <= int(
                    trial_end["tick_ns"]
                ):
                    states = (
                        data.get("endpoint_states")
                        if raw["type"] == "observation"
                        else (data.get("link_before", []) or [])
                        + (data.get("link_after", []) or [])
                    )
                    if states and "up" in states:
                        self.error(
                            "recovery",
                            recovery,
                            "observed failed-link restoration invalidates recovered outcome",
                        )
            previous = None
            designated = {
                (t["source"], t["destination"])
                for t in self.manifest["scenario"]["designated_traffic"]
            }
            for probe in rounds:
                data = probe["data"]
                directions = {
                    (d["source"], d["destination"]) for d in data["directions"]
                }
                if (
                    directions != designated
                    or not all(d["success"] is True for d in data["directions"])
                    or data["link_before"] != ["down", "down"]
                    or data["link_after"] != ["down", "down"]
                    or data["late"]
                    or data["purpose"] != "recovery"
                ):
                    self.error(
                        "recovery",
                        recovery,
                        "each confirmation round needs timely bidirectional success and both link checks",
                    )
                if int(probe["tick_ns"]) >= int(trial["response_deadline_ns"]) or int(
                    probe["tick_ns"]
                ) < int(fault["tick_ns"]):
                    self.error(
                        "recovery",
                        recovery,
                        "confirmation round is outside the response window",
                    )
                if previous and (
                    data["slot"] != previous["data"]["slot"] + 1
                    or int(data["scheduled_ns"])
                    != int(previous["data"]["scheduled_ns"]) + 1_000_000_000
                    or int(data["started_ns"]) < int(previous["data"]["completed_ns"])
                ):
                    self.error(
                        "recovery",
                        recovery,
                        "confirmation slots must be consecutive and non-overlapping",
                    )
                previous = probe
            if (
                recovery["tick_ns"] != rounds[-1]["tick_ns"]
                or recovery["data"]["first_success_record_id"] != rounds[0]["record_id"]
            ):
                self.error(
                    "recovery",
                    recovery,
                    "confirmation must end at the third round, retaining the first round separately",
                )
            disruption = [
                e
                for e in self.events
                if e["trial_id"] == trial["trial_id"]
                and e["type"] == "probe_round"
                and e["data"]["purpose"] == "disruption"
                and any(d["success"] is False for d in e["data"]["directions"])
                and int(e["tick_ns"]) <= int(rounds[0]["tick_ns"])
            ]
            if not disruption:
                self.error(
                    "recovery",
                    recovery,
                    "confirmed recovery requires observed prior traffic disruption",
                )

    def summaries(self):
        metrics = {
            "recovery",
            "detection",
            "reasoning",
            "action_effect",
            "execution",
            "verification",
        }
        rate_metrics = {
            "recovery_all_started",
            "recovery_valid_disrupted",
            "detection_participation",
            "action_participation",
        }

        def matches(group, trial, agent=None, operation=None):
            if (
                operation is None
                and agent is not None
                and group["operation_outcome"] is not None
            ):
                operation = next(
                    (
                        p
                        for p in agent["operations"]
                        if p["phase"] == "reasoning"
                        and p["duration"] == agent["measurements"]["reasoning"]
                    ),
                    None,
                )
            actual = {
                "agent_id": agent["agent_id"] if agent else None,
                "failed_link": trial["failed_link"],
                "trial_outcome": trial["outcome"],
                "operation_outcome": operation["outcome"] if operation else None,
            }
            return all(
                value is None or value == actual[key] for key, value in group.items()
            )

        def close(left, right):
            return (
                left == right
                if left is None or right is None
                else math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-15)
            )

        for summary in self.families["summary"]:
            totals = [
                r for r in summary["distributions"] if not any(r["group"].values())
            ]
            rates = [r for r in summary["rates"] if not any(r["group"].values())]
            if summary["state"] != "incomplete" and (
                {r["metric"] for r in totals} != metrics
                or {r["metric"] for r in rates} != rate_metrics
            ):
                self.error(
                    "summary",
                    summary,
                    "complete/aborted summaries require all six timing totals and four participation/recovery rates",
                )
            seen = set()
            for row in summary["distributions"]:
                key = (row["metric"], json.dumps(row["group"], sort_keys=True))
                if key in seen:
                    self.error("summary", summary, "duplicate timing population/group")
                seen.add(key)
                metric = row["metric"]
                population = (
                    "trial"
                    if metric == "recovery"
                    else (
                        "action"
                        if metric in {"execution", "verification"}
                        else "agent_trial"
                    )
                )
                if row["population"] != population:
                    self.error(
                        "summary",
                        summary,
                        "metric population has the wrong observational unit",
                    )
                sources = []
                for trial in self.trials.values():
                    if trial["trial_kind"] != "measured":
                        continue
                    if metric == "recovery":
                        if matches(row["group"], trial):
                            sources.append((trial["measurements"][metric], False))
                        continue
                    for agent in self.families["agent"]:
                        if agent["trial_id"] != trial["trial_id"]:
                            continue
                        if metric in {"execution", "verification"}:
                            for operation in agent["operations"]:
                                if operation["phase"] == metric and matches(
                                    row["group"], trial, agent, operation
                                ):
                                    sources.append(
                                        (operation["duration"], operation["late"])
                                    )
                        elif matches(row["group"], trial, agent):
                            duration = agent["measurements"][metric]
                            end = self.by_id.get(duration["end_record_id"])
                            trial_end = self.ends.get(trial["trial_id"])
                            late = bool(
                                end
                                and trial_end
                                and int(end["tick_ns"]) > int(trial_end["tick_ns"])
                            )
                            sources.append((duration, late))
                availability = Counter(d["availability"] for d, _ in sources)
                reasons = {
                    name: dict(
                        Counter(
                            d["reason"] for d, _ in sources if d["availability"] == name
                        )
                    )
                    for name in ("unavailable", "not_applicable")
                }
                values = [
                    d["value_seconds"]
                    for d, late in sources
                    if d["availability"] == "available" and not late
                ]
                expected = {
                    "available": availability["available"],
                    "unavailable": availability["unavailable"],
                    "not_applicable": availability["not_applicable"],
                    "unavailable_reasons": reasons["unavailable"],
                    "not_applicable_reasons": reasons["not_applicable"],
                    "included": len(values),
                    "count": len(values),
                    "late_excluded": sum(
                        d["availability"] == "available" and late for d, late in sources
                    ),
                }
                if any(row[key] != value for key, value in expected.items()):
                    self.error(
                        "summary",
                        summary,
                        f"{metric} population/counts disagree with projections",
                    )
                statistics = (
                    [mean(values), median(values), min(values), max(values)]
                    if values
                    else [None] * 4
                )
                if not all(
                    close(row[key], value)
                    for key, value in zip(
                        (
                            "mean_seconds",
                            "median_seconds",
                            "min_seconds",
                            "max_seconds",
                        ),
                        statistics,
                    )
                ):
                    self.error(
                        "summary",
                        summary,
                        f"{metric} statistics disagree with included values",
                    )
                if row["complete"] != (summary["state"] != "incomplete"):
                    self.error(
                        "summary",
                        summary,
                        "population completeness disagrees with summary state",
                    )
            seen.clear()
            for row in summary["rates"]:
                key = (row["metric"], json.dumps(row["group"], sort_keys=True))
                if key in seen:
                    self.error("summary", summary, "duplicate rate population/group")
                seen.add(key)
                trials = [
                    t
                    for t in self.trials.values()
                    if t["trial_kind"] == "measured" and matches(row["group"], t)
                ]
                metric = row["metric"]
                numerator = sum(t["outcome"] == "recovered" for t in trials)
                if metric == "recovery_all_started":
                    denominator = sum(
                        s["data"]["trial_kind"] == "measured"
                        and (
                            row["group"]["failed_link"] is None
                            or row["group"]["failed_link"] == s["data"]["failed_link"]
                        )
                        for s in self.starts.values()
                    )
                elif metric == "recovery_valid_disrupted":
                    denominator = sum(
                        t["outcome"] in {"recovered", "timeout"} for t in trials
                    )
                else:
                    opportunities = [
                        (t, a)
                        for t in self.trials.values()
                        if t["trial_kind"] == "measured"
                        and t["fault_record_id"] is not None
                        for a in self.families["agent"]
                        if a["trial_id"] == t["trial_id"]
                        and matches(row["group"], t, a)
                    ]
                    denominator = len(opportunities)
                    numerator = 0
                    for trial, agent in opportunities:
                        participated = any(
                            e["trial_id"] == trial["trial_id"]
                            and e["data"].get("agent_id") == agent["agent_id"]
                            and (
                                e["type"] == "recognition"
                                if metric == "detection_participation"
                                else e["type"] == "operation_started"
                                and e["data"]["phase"] == "action_attempt"
                            )
                            for e in self.events
                        )
                        numerator += participated
                value = numerator / denominator if denominator else None
                if (
                    row["numerator"] != numerator
                    or row["denominator"] != denominator
                    or not close(row["value"], value)
                ):
                    self.error(
                        "summary",
                        summary,
                        f"{metric} numerator/denominator/rate disagree with evidence",
                    )
