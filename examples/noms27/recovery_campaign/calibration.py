"""Synthetic-only calibration auditing and draft planning; no policy execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .blocks import VALIDITY_RULES, Manifest, build_manifest
from .feasibility import read_json
from .scenarios import CLASSES


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


METADATA_FIELDS = (
    "models",
    "runtime_settings",
    "prompts",
    "decision_schemas",
    "repair_catalogues",
    "combined_s1_interface",
    "budgets",
    "workload",
    "code_revision",
    "dependencies",
    "measurement",
    "reset_protocol",
    "randomization",
    "analysis",
    "resource_estimate",
    "executor",
)
METADATA_DETAILS = {
    "models": ("s1", "s2", "revisions", "local_model_identity"),
    "runtime_settings": ("s1", "s2", "decoding", "hardware"),
    "prompts": ("s1", "s2_direct", "s2_escalated"),
    "decision_schemas": ("s1", "s2"),
    "repair_catalogues": ("s1", "s2"),
    "combined_s1_interface": (
        "instructions",
        "criteria",
        "decision_confidence_fields",
        "catalogue",
    ),
    "budgets": (
        "s1_cap_seconds",
        "common_deadline_seconds",
        "feasibility_evidence",
        "handoff_verification_allowance",
    ),
    "workload": (
        "bottleneck_mbps",
        "payload_bytes",
        "primary_load",
        "higher_load",
        "return_path",
        "traffic_generator_revision",
    ),
    "dependencies": ("lock_revision", "runtime_versions"),
    "measurement": ("clock_accuracy", "packet_evidence_schema", "validation_evidence"),
    "reset_protocol": ("initial_state", "verification", "containment"),
    "randomization": (
        "algorithm",
        "order_seed",
        "reset_between_trials",
        "repeat_model_calls",
    ),
    "analysis": (
        "paired_complete_block_bootstrap",
        "within_placement",
        "interval_level",
        "bootstrap_repetitions",
        "bootstrap_seed",
        "censoring",
        "failure_inclusion",
        "report_by_regime_placement",
    ),
    "resource_estimate": (
        "runtime",
        "tokens",
        "inference_latency",
        "assumptions",
        "unknowns",
        "replacements",
    ),
}
SCIENTIFIC_BLOCKERS = [
    "implementation_containment_measurement_live_validation_pending",
    "six_class_fault_free_and_induced_fault_feasibility_pending",
    "successful_observation_sufficiency_agreement_pending",
    "Pedro_budget_resource_approval_pending",
    "workload_and_placement_scientific_freeze_pending",
    "real_calibration_pending",
    "Pedro_frozen_evaluation_approval_pending",
    "offline_artifacts_never_authorize_execution",
]


def preregister(manifest: Manifest, metadata: dict | None = None) -> dict:
    """Bind the full candidate/allocation/metadata snapshot before fixture collection."""
    if type(manifest.repetitions) is not int or any(
        type(seed) is not int
        for seed in (*manifest.calibration_seeds, *manifest.evaluation_seeds)
    ):
        raise ValueError("repetitions and split seeds must be integers, not booleans")
    manifest = Manifest.model_validate_json(manifest.model_dump_json(), strict=True)
    metadata = {} if metadata is None else metadata
    if not isinstance(metadata, dict):
        raise TypeError("freeze metadata must be an object")
    if set(metadata) - set(METADATA_FIELDS):
        raise ValueError("unknown freeze metadata fields")
    # Round trip rejects non-JSON/nonfinite inputs and detaches caller mutations.
    metadata = json.loads(json.dumps(metadata, allow_nan=False))
    assert isinstance(metadata, dict)
    for field, value in metadata.items():
        if value is not None and (
            (field in {"code_revision", "executor"} and not isinstance(value, str))
            or (
                field not in {"code_revision", "executor"}
                and not isinstance(value, dict)
            )
        ):
            raise ValueError(f"malformed freeze metadata: {field}")
    budgets = metadata.get("budgets") or {}
    workload = metadata.get("workload") or {}
    analysis = metadata.get("analysis") or {}
    for section, fields in (
        (budgets, ("s1_cap_seconds", "common_deadline_seconds")),
        (workload, ("bottleneck_mbps",)),
    ):
        for field in fields:
            value = section.get(field)
            if value is not None and (type(value) not in (int, float) or value <= 0):
                raise ValueError(f"{field} requires a positive numeric fixture value")
    if (
        all(
            budgets.get(k) is not None
            for k in ("s1_cap_seconds", "common_deadline_seconds")
        )
        and budgets["s1_cap_seconds"] > budgets["common_deadline_seconds"]
    ):
        raise ValueError("S1 cap cannot exceed common deadline")
    for section, field, value in (
        (workload, "payload_bytes", 1200),
        (workload, "primary_load", 0.1),
        (workload, "higher_load", 0.7),
        (analysis, "interval_level", 0.95),
    ):
        if (
            field in section
            and section[field] is not None
            and (type(section[field]) not in (int, float) or section[field] != value)
        ):
            raise ValueError(f"{field} conflicts with frozen scientific contract")
    for field in (
        "paired_complete_block_bootstrap",
        "within_placement",
        "failure_inclusion",
        "report_by_regime_placement",
    ):
        if (
            field in analysis
            and analysis[field] is not None
            and analysis[field] is not True
        ):
            raise ValueError(f"analysis requires {field}")
    for field in ("bootstrap_repetitions", "bootstrap_seed"):
        if (
            field in analysis
            and analysis[field] is not None
            and (
                type(analysis[field]) is not int
                or analysis[field] < (1 if field == "bootstrap_repetitions" else 0)
            )
        ):
            raise ValueError(f"invalid {field}")
    missing = [k for k in METADATA_FIELDS if not metadata.get(k)]
    missing.extend(
        f"{field}.{detail}"
        for field, details in METADATA_DETAILS.items()
        if metadata.get(field)
        for detail in details
        if metadata[field].get(detail) in (None, "", [], {})
    )
    candidates = {"gate_disabled": {"enabled": False, "threshold": None}}
    candidates.update(
        {
            f"threshold_{i / 10:.1f}": {"enabled": True, "threshold": i / 10}
            for i in range(11)
        }
    )
    result = {
        "schema_version": "recovery-offline-preregistration/v1",
        "kind": "synthetic_mechanism_only",
        "manifest": manifest.model_dump(mode="json"),
        "candidates": candidates,
        "configuration_sha256": {
            name: digest(
                {
                    "treatment": "combined_ungated"
                    if name == "combined_ungated"
                    else "combined_gated",
                    "policy": policy,
                    "shared_settings": metadata,
                }
            )
            for name, policy in {
                "combined_ungated": candidates["gate_disabled"],
                **candidates,
            }.items()
        },
        "comparator": "combined_ungated",
        "comparison": "strictly_below",
        "allocation": manifest.allocation(),
        "calibration_blocks": manifest.plan(split="calibration"),
        "calibration_trial_count": len(manifest.plan(split="calibration")) * 13,
        "metadata": metadata,
        "missing_metadata": missing,
        "measurement_contract": {
            "payload_bytes": 1200,
            "primary_load": 0.1,
            "higher_load": 0.7,
            "delivery_deadline_ms": 100,
            "sustained_seconds": 1,
            "bin_ms": 100,
            "timely_fraction": 0.99,
            "observation": "common_deadline_plus_5_seconds",
            "disruption": "delivery_failed_packets_divided_by_fixed_offered_packet_rate",
            "failure_outcomes_retained": True,
        },
        "validity_rules": VALIDITY_RULES,
        "selection_rule": "failure_count_eligibility_then_equal_six_class_disruption",
        "tie_rule": "gate_disabled; numeric ties use preregistered ascending order",
        "scientific_freeze": False,
        "execution_authorized": False,
        "blockers": list(SCIENTIFIC_BLOCKERS)
        + (["freeze_metadata_incomplete"] if missing else []),
    }
    return {**result, "registration_sha256": digest(result)}


class FixtureRecord(BaseModel):
    """Deliberately not live evidence: outcome assertions for mechanism checks only."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    trial_id: str = Field(min_length=1)
    block_id: str = Field(min_length=1)
    replaces: str | None
    registration_sha256: str
    evidence_kind: str
    placement_id: str
    repetition: int = Field(ge=0)
    traffic_seed: int
    environment_seed: int
    scenario_sha256: str
    candidate: str
    configuration_sha256: str
    valid: bool
    invalid_reasons: list[str]
    recovery_failure: bool
    disruption_seconds: float = Field(ge=0)


def report(registration: dict, records: list[dict]) -> dict:
    """Audit complete matched fixture blocks and emit a non-executable draft."""
    manifest = Manifest.model_validate_json(
        json.dumps(registration["manifest"]), strict=True
    )
    expected = preregister(manifest, registration["metadata"])
    if (
        registration != expected
        or digest({k: v for k, v in registration.items() if k != "registration_sha256"})
        != registration["registration_sha256"]
    ):
        raise ValueError("registration snapshot/hash does not match frozen design")
    # JSON object order is not an input to the preregistered numeric tie rule.
    registration = expected
    # Preserve a detached copy of every original, including invalid/unsuccessful rows.
    originals = json.loads(json.dumps(records, allow_nan=False))
    errors = []
    parsed = []
    for index, raw in enumerate(originals):
        try:
            row = FixtureRecord.model_validate(raw)
            if row.registration_sha256 != registration["registration_sha256"]:
                raise ValueError("evidence is not bound to preregistration")
            if row.evidence_kind != "synthetic_calibration_fixture":
                raise ValueError("only synthetic calibration fixtures permitted")
            if row.configuration_sha256 != registration["configuration_sha256"].get(
                row.candidate
            ):
                raise ValueError(
                    "candidate configuration/policy/shared interface mismatch"
                )
            parsed.append(row)
        except ValueError as exc:
            errors.append({"record_index": index, "error": str(exc)})
    if len({r.trial_id for r in parsed}) != len(parsed):
        errors.append({"error": "duplicate trial IDs"})
    plans = {
        (b["placement_id"], b["repetition"]): b
        for b in registration["calibration_blocks"]
    }
    members = ["combined_ungated", *registration["candidates"]]
    groups: dict[str, list[FixtureRecord]] = {}
    for row in parsed:
        groups.setdefault(row.block_id, []).append(row)
    accepted = {}
    excluded = []
    block_keys = {}
    replacements: dict[str, list[str]] = {}
    for block_id, rows in groups.items():
        key = (rows[0].placement_id, rows[0].repetition)
        plan = plans.get(key)
        if (
            plan is None
            or len(rows) != len(members)
            or {r.candidate for r in rows} != set(members)
            or any(
                (r.placement_id, r.repetition) != key
                or any(
                    getattr(r, field) != plan[field]
                    for field in ("traffic_seed", "environment_seed", "scenario_sha256")
                )
                or r.replaces != rows[0].replaces
                or (r.valid and bool(r.invalid_reasons))
                or (not r.valid and not r.invalid_reasons)
                or not set(r.invalid_reasons) <= set(VALIDITY_RULES["invalid"])
                for r in rows
            )
        ):
            errors.append(
                {"block_id": block_id, "error": "unmatched_or_malformed_block"}
            )
        else:
            block_keys[block_id] = key
            if rows[0].replaces is not None:
                replacements.setdefault(rows[0].replaces, []).append(block_id)
    visited = set()
    for root, root_rows in groups.items():
        if root_rows[0].replaces is not None or root not in block_keys:
            continue
        current = root
        key = block_keys[root]
        while current not in visited:
            visited.add(current)
            rows = groups[current]
            children = replacements.get(current, [])
            invalid = any(not r.valid for r in rows)
            if not invalid:
                if children or key in accepted:
                    errors.append(
                        {
                            "block_id": current,
                            "error": "valid_block_replaced_or_duplicate_allocation",
                        }
                    )
                else:
                    accepted[key] = rows
                break
            if len(children) != 1 or block_keys.get(children[0]) != key:
                errors.append(
                    {
                        "block_id": current,
                        "error": "invalid_block_requires_one_complete_matched_replacement",
                    }
                )
                break
            child = children[0]
            excluded.append(
                {
                    "block_id": current,
                    "replaced_by": child,
                    "reasons": sorted(
                        {reason for r in rows for reason in r.invalid_reasons}
                    ),
                }
            )
            current = child
    if visited != set(groups):
        errors.append({"error": "orphan_cyclic_or_malformed_replacement_blocks"})
    if set(accepted) != set(plans):
        errors.append({"error": "complete_matched_calibration_coverage_required"})
    summaries = {}
    selection = None
    if not errors:
        for candidate in members:
            by_class = {c: [] for c in CLASSES}
            for key, rows in accepted.items():
                by_class[plans[key]["class_id"]].append(
                    next(r for r in rows if r.candidate == candidate)
                )
            summaries[candidate] = {
                "class_sample_counts": {c: len(rs) for c, rs in by_class.items()},
                "failure_count": sum(
                    r.recovery_failure for rs in by_class.values() for r in rs
                ),
                "class_failures": {
                    c: sum(r.recovery_failure for r in rs) for c, rs in by_class.items()
                },
                "class_mean_disruption_seconds": {
                    c: math.fsum(r.disruption_seconds / len(rs) for r in rs)
                    for c, rs in by_class.items()
                },
            }
            summaries[candidate]["mean_disruption_seconds"] = math.fsum(
                value / 6
                for value in summaries[candidate][
                    "class_mean_disruption_seconds"
                ].values()
            )
        comparator = summaries["combined_ungated"]
        for candidate, summary in summaries.items():
            summary["eligible"] = (
                summary["failure_count"] <= comparator["failure_count"]
            )
            summary["mean_disruption_difference_seconds"] = (
                summary["mean_disruption_seconds"]
                - comparator["mean_disruption_seconds"]
            )
            summary["class_disruption_differences_seconds"] = {
                c: summary["class_mean_disruption_seconds"][c]
                - comparator["class_mean_disruption_seconds"][c]
                for c in CLASSES
            }
            summary["class_failure_differences"] = {
                c: summary["class_failures"][c] - comparator["class_failures"][c]
                for c in CLASSES
            }
        eligible = [c for c in registration["candidates"] if summaries[c]["eligible"]]
        # Stable candidate order preregisters disabled-first / ascending numeric ties.
        best = min(
            eligible,
            key=lambda c: summaries[c]["mean_disruption_seconds"],
            default=None,
        )
        winner = (
            best
            if best is not None
            and summaries[best]["mean_disruption_seconds"]
            < comparator["mean_disruption_seconds"]
            else "gate_disabled"
        )
        if summaries[winner]["eligible"]:
            selection = {
                "candidate": winner,
                "policy": registration["candidates"][winner],
            }
        else:
            errors.append(
                {"error": "gate_disabled_fixture_inconsistent_with_comparator"}
            )
    return {
        "schema_version": "recovery-offline-calibration-report/v1",
        "kind": "synthetic_mechanism_only",
        "registration_sha256": registration["registration_sha256"],
        "retained_records": originals,
        "excluded_blocks": excluded,
        "matched_block_count": len(accepted),
        "errors": errors,
        "candidates": summaries,
        "selection": selection,
        "scientific_selected_policy": None,
        "draft_evaluation": {
            "status": "draft_unapproved_non_executable",
            "registration_sha256": registration["registration_sha256"],
            "calibration_selection_sha256": digest(
                {"records": originals, "selection": selection}
            ),
            "treatment_policies": {
                slot: selection["policy"]
                if selection and slot == "combined_gated"
                else None
                if slot == "combined_gated"
                else {"enabled": False, "threshold": None}
                for slot in manifest.primary_treatments
            },
            "execution_authorized": False,
            "selected_policy": selection["policy"] if selection else None,
            "manifest": manifest.model_dump(mode="json"),
            "allocation": manifest.allocation(),
            "primary": manifest.plan(),
            "higher_load": manifest.plan(load_fraction=0.7),
            "metadata": registration["metadata"],
            "missing_metadata": registration["missing_metadata"],
            "measurement_contract": registration["measurement_contract"],
            "approvals": {"budget_resource": None, "frozen_evaluation_Pedro": None},
            "validity_rules": VALIDITY_RULES,
        },
        "interpretation": "observed failure counts only; not population noninferiority",
        "scientific_freeze": False,
        "execution_authorized": False,
        "blockers": list(registration["blockers"])
        + (["synthetic_evidence_incomplete_or_invalid"] if errors else []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    registration_parser = sub.add_parser("preregister")
    registration_parser.add_argument("--manifest", type=Path)
    registration_parser.add_argument("--metadata", type=Path)
    registration_parser.add_argument("--output", type=Path, required=True)
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--registration", type=Path, required=True)
    report_parser.add_argument("--evidence", type=Path, required=True)
    report_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "preregister":
        manifest = (
            Manifest.model_validate_json(
                json.dumps(read_json(args.manifest)), strict=True
            )
            if args.manifest
            else build_manifest()
        )
        metadata = read_json(args.metadata) if args.metadata else None
        result = preregister(manifest, metadata)
    else:
        registration = read_json(args.registration)
        records = read_json(args.evidence)
        if not isinstance(registration, dict) or not isinstance(records, list):
            raise ValueError(
                "expected registration object and synthetic evidence array"
            )
        result = report(registration, records)
        result["sources"] = {
            name: {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in {
                "registration": args.registration,
                "evidence": args.evidence,
            }.items()
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "execution_authorized": False,
                "blockers": result["blockers"],
            }
        )
    )


if __name__ == "__main__":
    main()
