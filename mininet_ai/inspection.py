"""Best-effort, private derived views; never authoritative run outcomes."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mininet_ai.saved_runs import RunEvidence, identity, open_source
from mininet_ai.storage import open_private_directory

if TYPE_CHECKING:
    from mininet_ai.experiment import ExperimentRuntimeReport


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _publish(directory: Path, name: str, content: str) -> None:
    """Publish a complete private file without replacing any existing entry."""
    parent = open_private_directory(directory, create=False)
    temporary = f".inspection-{uuid.uuid4().hex}"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent,
                follow_symlinks=False)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def _human(summary: dict[str, Any]) -> str:
    outcomes = summary["outcomes"]
    counts = summary["counts"]
    invocation_counts = counts["invocations"]

    def known(value: Any) -> str:
        return "unknown" if value is None else str(value).lower()

    problems = "".join(f"- {problem['reason']}\n" for problem in summary["problems"])
    return (
        f"# Run {summary['run_id']}\n\n"
        f"Results: {summary['results_directory']}\n\n"
        "## What happened\n\n"
        f"- Recorded execution: {outcomes['recorded_execution']}; operation: {outcomes['operation']}.\n"
        f"- Cleanup: {outcomes['cleanup']['status']}; evidence finalized: {outcomes['evidence_finalized']}.\n"
        f"- Observed invocation results: {known(invocation_counts['succeeded']['value'])} succeeded, "
        f"{known(invocation_counts['failed']['value'])} unsuccessful (partial or unknown coverage, not totals).\n"
        f"- Observed action failures: {known(counts['actions']['failed']['value'])} (partial or unknown coverage).\n"
        f"- Observed interruption: {known(outcomes['interruption_observed'])}; experiment success: unknown.\n\n"
        "## What remains uncertain\n\n"
        f"Execution conclusion: {summary['conclusions']['execution'] or 'unknown'}. "
        f"Evidence completeness: {outcomes['evidence_completeness']}. "
        f"Recorded problems: {len(summary['problems'])}; supporting references and general gaps below.\n\n"
        + problems + "\n"
        "## Where to inspect\n\n"
        f"- Readable ledger: {summary['inspection']['evidence/ledger.json']} — evidence/ledger.json.\n"
        f"- Diagnostics: {summary['sources']['log']}.\n"
        f"- Workload artifacts/measurements, if recorded: {summary['sources']['artifacts']}.\n\n"
        "Private derived inspection, not an export or a certification of success.\n"
        "Machine facts: summary.json. Complete available recorded structured responses: "
        "evidence/ledger.json. Diagnostics and workload artifact locations are listed below.\n\n"
        "Unknown is not zero; applied actions do not establish verified recovery. "
        "Client interruption does not establish remote Provider termination.\n\n"
        "The following facts are identical to the machine summary:\n\n```json\n"
        + _json(summary) + "```\n"
    )


def _count(value: int | None, population: str, references: list[str]) -> dict[str, Any]:
    return {"value": value, "coverage": "partial" if value is not None else "unknown",
            "population": population, "references": references}


def _observations(ledger: dict[str, Any] | None, problems: list[dict[str, Any]],
                  source: str) -> dict[str, Any]:
    """Count supported audit populations, not overlapping runtime aggregates.

    Reasoning completion precedes action/state commitment, so it is deliberately
    not classified as successful invocation completion. Mirrored runtime and
    coordination records are not added to the audit populations.
    """
    counts: dict[str, Any] = {
        "invocations": {
            "total": _count(None, "all agent invocations", []),
            "succeeded": _count(None, "successfully completed agent invocations", []),
        },
        "actions": {"total": _count(None, "all capability execution attempts", [])},
    }
    groups = {
        "agent.invocation.started": ("invocations", "observed_started"),
        "agent.invocation.completed": ("invocations", "reasoning_completed"),
        "agent.invocation.failed": ("invocations", "reasoning_failed"),
        "capability.execution.failed": ("actions", "failed"),
    }
    observed: dict[tuple[str, str], list[str]] = {value: [] for value in groups.values()}
    observed.update({("actions", key): [] for key in ("succeeded", "rejected")})
    invocation_ids: set[tuple[str, str]] = set()
    for record in ledger["records"] if ledger else []:
        kind = record["type"]
        if record["category"] not in {"invocation", "action"}:
            continue
        group = groups.get(kind)
        reference = f"{source}#sequence={record['sequence']}"
        payload = record["data"].get("data", {})
        if not isinstance(payload, dict) or not isinstance(payload.get("result", {}), dict):
            problems.append({"reason": "Malformed audit payload; classification unavailable",
                             "reference": reference})
            continue
        result = payload.get("result", {})
        if kind == "capability.execution.completed" and result.get("status") in {"succeeded", "failed", "rejected"}:
            group = ("actions", result["status"])
        if group is not None:
            invocation = record["data"].get("invocationId")
            identity = (kind, invocation)
            # Only invocation audit entries have one occurrence per invocation;
            # repeated capability executions are distinct action attempts.
            if group[0] != "invocations" or identity not in invocation_ids:
                observed[group].append(reference)
                invocation_ids.add(identity)
        if kind.endswith(".failed") or result.get("status") in {"failed", "rejected"}:
            issue = result.get("issue") or {}
            if not isinstance(issue, dict):
                issue = {}
            problems.append({"code": payload.get("code", issue.get("code", kind)),
                             "reason": payload.get("message", issue.get("message", "Recorded failure; inspect referenced record")),
                             "reference": reference})
    for (population, classification), references in observed.items():
        counts[population][classification] = _count(
            len(references) if ledger else None,
            f"{classification} in recorded {population} audit records", references,
        )
    return counts


def publish_inspection(evidence: RunEvidence, *, interrupted: bool | None = None,
                       report: ExperimentRuntimeReport | None = None,
                       operation_problem: dict[str, str] | None = None) -> list[str]:
    """Return final diagnostics, including exactly which writes succeeded/failed.

    This is called only from current-run end reporting, never for old saved runs.
    Every inspection failure is observational and leaves existing exit rules alone.
    """
    metadata = evidence.metadata
    directory = evidence.directory
    problems: list[dict[str, Any]] = []
    if operation_problem is not None:
        problems.append(operation_problem)
    if report is not None and report.run.id != metadata.run_id:
        problems.append({"reason": "End report identity conflicts with reserved run; report facts unavailable"})
        report = None
    messages: list[str] = []
    ledger = None
    ledger_status = "unavailable"
    source_availability = {}
    for role, path in metadata.sources.items():
        try:
            fd = open_source(Path(path))
            try:
                if identity(fd) != metadata.source_identities.get(role):
                    raise ValueError("source identity differs from recorded source")
            finally:
                os.close(fd)
            source_availability[role] = {"available": True, "path": path,
                                         "scope": "private source identity present; contents not certified"}
        except Exception as error:
            source_availability[role] = {"available": False, "path": path,
                                         "reason": str(error)}
            problems.append({"source": path, "reason": str(error)})
    try:
        ledger = evidence.inspection_ledger()
        if ledger["manifest"] is None and not ledger["records"]:
            ledger = None
            raise ValueError("no recorded manifest or ledger records; no empty ledger view produced")
        fd = open_private_directory(directory / "evidence", create=True)
        os.close(fd)
        _publish(directory / "evidence", "ledger.json", _json(ledger))
        ledger_status = "available"
        messages.append(f"Inspection available: {directory / 'evidence/ledger.json'}")
    except Exception as error:
        problems.append({"source": metadata.sources["ledger"],
                         "view": "evidence/ledger.json", "reason": str(error)})
        messages.append(f"Inspection unavailable: {directory / 'evidence/ledger.json'}: {error}")
    ledger_coverage = "unknown"
    execution = None
    if ledger is not None:
        lifecycle = [record for record in ledger["records"] if record["category"] == "run.lifecycle"]
        last_phase = max((index for index, record in enumerate(lifecycle)
                          if record["type"] in {"run.stopping", "run.stop-retrying"}), default=-1)
        terminal = [record for record in lifecycle[last_phase + 1:]
                    if record["type"] in {"run.stopped", "run.failed"}]
        ledger_coverage = "complete"
        if ledger["manifest"] is None:
            ledger_coverage = "partial"
            problems.append({"source": metadata.sources["ledger"],
                             "reason": "Missing run manifest; start time and provenance unavailable"})
        if not terminal:
            ledger_coverage = "partial"
            problems.append({"source": metadata.sources["ledger"],
                             "reason": "Missing terminal lifecycle record; execution conclusion unknown"})
        elif len({record["type"] for record in terminal}) > 1 or (
            terminal[-1]["type"] == "run.failed" and metadata.outcome == "succeeded"
        ):
            ledger_coverage = "partial"
            problems.append({"source": str(directory / "run.json"),
                             "reason": "Conflicting terminal lifecycle/metadata records; execution conclusion unknown",
                             "references": [f"evidence/ledger.json#/records/{record['sequence'] - 1}" for record in terminal]})
        else:
            execution = metadata.outcome
    counts = _observations(ledger, problems, metadata.sources["ledger"])
    started_at = ledger["manifest"]["createdAt"] if ledger and ledger["manifest"] else None
    if report is not None:
        report_start = report.run.model_dump(mode="json")["started_at"]
        if started_at is not None and started_at != report_start:
            problems.append({"reason": "Manifest and end report disagree on run start time"})
            started_at = None
        elif started_at is None:
            started_at = report_start
    invocations: dict[str, dict[str, Any]] = {}
    if report is not None:
        for record in report.continuous.invocations:
            results = [record.result]
            if record.coordination is not None:
                results.extend(item.result for item in record.coordination.invocations)
            for result in results:
                if result.run_id != metadata.run_id:
                    problems.append({"reason": "Invocation result identity conflicts with reserved run"})
                    continue
                item = {"invocation_id": result.invocation_id, "agent_id": result.agent_id,
                        "status": result.status.value,
                        "issue": result.issue.model_dump(mode="json", by_alias=True) if result.issue else None,
                        "source": "current-run end report; not a new authoritative ledger record"}
                previous = invocations.get(result.invocation_id)
                if previous is not None and previous["status"] != item["status"]:
                    item["status"] = None
                    problems.append({"reason": "Conflicting reported invocation outcomes",
                                     "invocation_id": result.invocation_id})
                invocations[result.invocation_id] = item
        for issue in report.issues:
            problems.append({"code": issue.code, "reason": issue.message,
                             "source": "current-run end report", "phase": issue.phase})
        for issue in report.previous_issues:
            problems.append({"code": issue.code, "reason": issue.message,
                             "source": "previous cleanup attempt in current-run end report",
                             "phase": issue.phase})
    for classification in ("succeeded", "failed"):
        references = [f"#/observations/invocations/{index}" for index, item in enumerate(invocations.values())
                      if item["status"] is not None and
                      (item["status"] == "succeeded") == (classification == "succeeded")]
        counts["invocations"][classification] = _count(
            len(references) if report is not None else None,
            f"{'successful' if classification == 'succeeded' else 'unsuccessful (failed or rejected)'} unique invocation results in current-run end report; excludes missing results and delivery issues",
            references,
        )
    summary = {
        "run_id": metadata.run_id,
        "results_directory": str(directory),
        "started_at": started_at,
        "outcomes": {
            "recorded_execution": metadata.outcome,
            "operation": metadata.operation.value,
            "effects": metadata.effects.value,
            "runtime_state": report.state.value if report is not None else None,
            "registry_lifecycle": metadata.lifecycle,
            "interruption_observed": interrupted,
            "remote_provider_termination": None,
            "cleanup": metadata.cleanup.model_dump(mode="json"),
            "teardown_error": metadata.teardown_error.model_dump(mode="json") if metadata.teardown_error else None,
            "evidence_finalized": metadata.finalized,
            "managed_writers_verified": metadata.managed_writers_verified,
            "evidence_completeness": "unknown",
            "export_completion": "not_attempted_by_inspection",
            "experiment_success": {"value": None, "criterion": None,
                                   "reason": "No explicit criterion and independent measurement evaluated by inspection"},
        },
        "conclusions": {"execution": execution},
        "coverage": {"ledger": ledger_coverage,
                     "population": "validated ledger sequence, manifest and terminal presence only; not evidence completeness"},
        "sources": metadata.sources,
        "source_availability": source_availability,
        "references": {"outcomes": str(directory / "run.json"),
                       "ledger": "evidence/ledger.json", "measurements": metadata.sources["artifacts"]},
        "inspection": {
            "evidence/ledger.json": ledger_status,
            "summary.md": "publication confirmed separately on stderr; check file presence",
            "summary.json": "publication confirmed separately on stderr; check file presence",
        },
        "problems": problems,
        "counts": counts,
        "observations": {"invocations": list(invocations.values())},
        "optional_raw_payloads": {
            "capture_added_by_inspection": False,
            "availability": "only material already present in the recorded ledger; no raw-capture setting inferred",
            "omissions": "unrecorded optional prompts/observations/Provider payloads are not manufactured and are distinct from essential evidence gaps",
        },
        "gaps": ["Workload measurement criteria are not evaluated by generic bookkeeping",
                 "Raw prompts, observations and Provider payloads are available only if explicitly recorded; no additional capture is performed"],
    }
    for name, content in (("summary.json", _json(summary)), ("summary.md", _human(summary))):
        try:
            _publish(directory, name, content)
            messages.append(f"Inspection available: {directory / name}")
        except Exception as error:
            messages.append(f"Inspection unavailable: {directory / name}: {error}; surviving sources: {_json(metadata.sources).strip()}")
    return messages
