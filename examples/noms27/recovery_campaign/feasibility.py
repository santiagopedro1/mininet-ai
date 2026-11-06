"""Read-only offline feasibility planning/reporting. Never executes or approves runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .blocks import PRIMARY, Manifest, build_manifest
from .runner import CampaignConfig, measure
from .scenarios import CLASSES


class OfflineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    successful_observation_minimum: int | None = Field(default=None, ge=1)
    handoff_seconds: float | None = Field(default=None, ge=0)
    verification_seconds: float = Field(default=1.1, ge=1.1)
    replication_increase: int = Field(default=0, ge=0)
    calibration_loads: list[float] = Field(default_factory=lambda: [0.1])
    share_ungated_comparator: bool = False
    comparator_sharing_justification: str | None = None
    nonrepairable_attempts_per_cell: int = Field(default=30, ge=1)
    replacement_trials: int | None = Field(default=None, ge=0)
    local_model_identity: str | None = None
    input_tokens_per_trial: float | None = Field(default=None, ge=0)
    output_tokens_per_trial: float | None = Field(default=None, ge=0)
    reasoning_tokens_per_trial: float | None = Field(default=None, ge=0)
    inference_seconds_per_trial: float | None = Field(default=None, ge=0)
    runtime_seconds_per_trial: float | None = Field(default=None, gt=0)
    runtime_assumptions: str | None = None
    evidence_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_assumptions(self):
        if (
            not self.calibration_loads
            or len(set(self.calibration_loads)) != len(self.calibration_loads)
            or not set(self.calibration_loads) <= {0.1, 0.7}
        ):
            raise ValueError("calibration loads must be distinct declared loads")
        if self.share_ungated_comparator and not self.comparator_sharing_justification:
            raise ValueError("sharing requires an explicit matching justification")
        if len(set(self.evidence_paths)) != len(self.evidence_paths):
            raise ValueError("duplicate evidence paths")
        if (
            self.runtime_seconds_per_trial is not None
            and self.inference_seconds_per_trial is not None
            and self.runtime_seconds_per_trial < self.inference_seconds_per_trial
        ):
            raise ValueError("runtime must include inference, not be added to it")
        return self


def read_json(path: Path):
    """Reject JSON extensions and ambiguous duplicate object keys, at every depth."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def bad_constant(value):
        raise ValueError(f"nonfinite JSON number: {value}")

    data = json.loads(
        path.read_text(), object_pairs_hook=pairs, parse_constant=bad_constant
    )

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite JSON number")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)

    finite(data)
    return data


class Packet(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    seq: int = Field(ge=0)
    send_ns: int
    arrivals_ns: list[int]


def observation(path: Path, manifest: Manifest) -> dict:
    """Reconcile retained packet measurements with the existing evaluator."""
    row = {
        "path": str(path),
        "accepted_for_timing": False,
        "errors": [],
        "missing_metadata": [],
    }
    try:
        raw = path.read_bytes()
        row["sha256"] = hashlib.sha256(raw).hexdigest()
        evidence = read_json(path)
        trial_id = evidence["trial_id"]
        if not isinstance(trial_id, str) or not trial_id:
            raise ValueError("trial_id must be a nonempty string")
        row["trial_id"] = trial_id
        # JSON round-trip is needed for tuple-valued scenario fields; strict scalar
        # checks below prevent Pydantic's permissive numeric/bool coercions.
        raw_config = evidence["config"]
        for field in (
            "deadline_seconds",
            "s1_cap_seconds",
            "bottleneck_mbps",
            "load_fraction",
            "cleanup_budget_seconds",
            "payload_bytes",
            "delivery_deadline_ms",
            "bin_ms",
            "sustained_seconds",
            "timely_fraction",
            "baseline_seconds",
            "post_deadline_seconds",
        ):
            if field in raw_config and type(raw_config[field]) not in (int, float):
                raise ValueError(f"{field} must be numeric, not bool/string")
        for field in ("seed", "execution_order", "environment_seed"):
            if raw_config.get(field) is not None and type(raw_config[field]) is not int:
                raise ValueError(f"{field} must be an integer")
        config = CampaignConfig.model_validate_json(json.dumps(raw_config))
        if config.trial_id != trial_id or config.scenario_instance is None:
            raise ValueError("trial/config identity or structural scenario missing")
        scenario = config.scenario_instance
        if scenario not in manifest.scenarios:
            raise ValueError("scenario does not match declared campaign manifest")
        if (
            config.seed in manifest.evaluation_seeds
            or config.seed not in manifest.calibration_seeds
        ):
            raise ValueError(
                "heldout/unknown traffic seed excluded from mechanism fixtures"
            )
        if json.dumps(evidence["scenario"]["definition"], sort_keys=True) != json.dumps(
            scenario.model_dump(mode="json"), sort_keys=True
        ) or json.dumps(
            evidence["scenario"]["structural_check"], sort_keys=True
        ) != json.dumps(scenario.structural_check(), sort_keys=True):
            raise ValueError("inconsistent structural metadata")
        row.update(
            {
                "class_id": scenario.class_id,
                "load_fraction": config.load_fraction,
                "treatment": config.treatment,
                "evidence_kind": evidence["evidence_kind"],
                "split": scenario.split,
            }
        )
        if (
            evidence["evidence_kind"] != "mechanism_validation"
            or config.purpose != "mechanism_validation"
        ):
            raise ValueError(
                "calibration/heldout/live evidence cannot enter offline feasibility budgets"
            )
        if scenario.split == "heldout":
            raise ValueError("heldout evidence is excluded even when labeled synthetic")
        expected_schema = (
            "recovery-s1/v1"
            if config.treatment == "s1_only"
            else "recovery-s2/v1"
            if config.treatment == "s2_only"
            else "recovery-combined/v1"
        )
        if evidence["schema_version"] != expected_schema:
            raise ValueError("inconsistent evidence schema/treatment")
        validity = evidence["validity"]
        if type(validity["valid"]) is not bool or not isinstance(
            validity["reasons"], list
        ):
            raise ValueError("malformed validity")
        row["valid"] = validity["valid"]
        row["validity_reasons"] = validity["reasons"]
        events = evidence["events"]
        if len({e["id"] for e in events}) != len(events) or any(
            type(e["id"]) is not int
            or type(e["tick_ns"]) is not int
            or e["trial_id"] != trial_id
            for e in events
        ):
            raise ValueError("duplicate or inconsistent event identity/clock")
        start = [
            e["tick_ns"]
            for e in events
            if e["kind"] == "injection_start" and "deadline_ns" in e
        ]
        end = [e["tick_ns"] for e in events if e["kind"] == "injection_complete"]
        if len(start) != 1 or len(end) != 1 or end[0] < start[0]:
            raise ValueError("missing/inconsistent injection timing")
        # Event emission follows timestamp capture. Reuse the recorded deadline
        # anchor and confirmed_ns rather than moving the measurement origin to
        # the slightly later event-journal tick.
        start_event = next(
            e for e in events if e["kind"] == "injection_start" and "deadline_ns" in e
        )
        deadline_ns = start_event["deadline_ns"]
        confirmed = [
            e["confirmed_ns"] for e in events if e["kind"] == "fault_confirmed"
        ]
        if (
            type(deadline_ns) is not int
            or len(confirmed) != 1
            or type(confirmed[0]) is not int
        ):
            raise ValueError("missing/inconsistent clock anchors")
        origin = deadline_ns - round(config.deadline_seconds * 1_000_000_000)
        if not origin <= start[0] <= end[0] <= confirmed[0] <= deadline_ns:
            raise ValueError("inconsistent injection/deadline anchors")
        packet_evidence = evidence["packet_evidence"]
        packets = [
            Packet.model_validate(p).model_dump() for p in packet_evidence["packets"]
        ]
        measurement = measure(
            config, packets, origin, recovery_not_before_ns=confirmed[0]
        )
        if json.dumps(measurement, sort_keys=True) != json.dumps(
            evidence["measurement"], sort_keys=True
        ):
            raise ValueError("retained measurement does not match packet evidence")
        recovered = (
            measurement["verification_complete_seconds"] is not None
            and measurement["verification_complete_seconds"] <= config.deadline_seconds
        )
        outcome = evidence["outcome"]
        if (
            type(outcome["service_recovery"]) is not bool
            or type(outcome["recovery_failure"]) is not bool
            or outcome["service_recovery"] != recovered
            or outcome["recovery_failure"] == recovered
        ):
            raise ValueError("inconsistent service outcome")
        row["service_recovery"] = recovered
        row["successful_s1_repair"] = recovered and (
            config.treatment == "s1_only"
            or config.treatment in {"combined_ungated", "combined_gated"}
            and outcome.get("s1_status") == "verified"
        )
        row["verification_complete_seconds"] = measurement[
            "verification_complete_seconds"
        ]
        row["censored"] = measurement["censored"]
        prov = evidence.get("provenance", {})
        for name in ("seed", "environment_seed", "execution_order"):
            if name in prov and prov[name] != getattr(config, name):
                raise ValueError(f"provenance/config {name} mismatch")
        row["missing_metadata"] = [
            name
            for name in (
                "code_revision",
                "source_manifest",
                "dependencies",
                "model_revision",
                "runtime_model_settings",
                "clock_accuracy_ns",
                "instructions",
            )
            if not prov.get(name)
        ]
        if config.environment_seed is None:
            row["missing_metadata"].append("environment_seed")
        checks = packet_evidence.get("fault_checks", [])
        if validity["valid"] is not True or validity["reasons"]:
            row["errors"].append("retained_invalid_attempt")
        if (
            measurement["measurement_errors"]
            or packet_evidence.get("measurement_errors")
            or not measurement["baseline_healthy"]
        ):
            row["errors"].append("measurement_or_baseline_invalid")
        if not checks or any(c.get("both_endpoints_down") is not True for c in checks):
            row["errors"].append("fault_persistence_missing")
        row["accepted_for_timing"] = not row["errors"]
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        row["errors"].append(f"unusable_artifact:{exc}")
    return row


def budget_summary(cells: list[dict], rows: list[dict], config: OfflineConfig) -> dict:
    for cell in cells:
        attempts = [
            r
            for r in rows
            if all(
                r.get(k) == cell[k] for k in ("class_id", "load_fraction", "treatment")
            )
        ]
        eligible = [r for r in attempts if r["accepted_for_timing"]]
        successes = sorted(
            r["verification_complete_seconds"]
            for r in eligible
            if r["service_recovery"]
        )
        s1_successes = sorted(
            r["verification_complete_seconds"]
            for r in eligible
            if r["successful_s1_repair"]
        )
        cell.update(
            {
                "retained_attempts": len(attempts),
                "valid_attempts": len(eligible),
                "successful_observations": len(successes),
                "successful_s1_observations": len(s1_successes),
                "successful_s1_repair_p95_seconds": s1_successes[
                    math.ceil(0.95 * len(s1_successes)) - 1
                ]
                if s1_successes
                else None,
                "valid_unsuccessful_attempts": sum(
                    not r["service_recovery"] for r in eligible
                ),
                "invalid_or_unusable_attempts": len(attempts) - len(eligible),
                "successful_end_to_end_p95_seconds": successes[
                    math.ceil(0.95 * len(successes)) - 1
                ]
                if successes
                else None,
                "adequate": cell["repairable"]
                and len(eligible) >= cell["attempt_floor"]
                and config.successful_observation_minimum is not None
                and len(successes) >= config.successful_observation_minimum,
            }
        )
    usable = [
        c
        for c in cells
        if c["repairable"]
        and c["successful_end_to_end_p95_seconds"] is not None
        and config.successful_observation_minimum is not None
        and c["successful_observations"] >= config.successful_observation_minimum
    ]
    s1 = [
        c["successful_s1_repair_p95_seconds"]
        for c in usable
        if c["successful_s1_repair_p95_seconds"] is not None
        and c["successful_s1_observations"] >= config.successful_observation_minimum
    ]
    cap = 2 * max(s1) if s1 else None
    twice = (
        2 * max(c["successful_end_to_end_p95_seconds"] for c in usable)
        if usable
        else None
    )
    enlarged = (
        None
        if cap is None or config.handoff_seconds is None
        else cap + config.handoff_seconds + config.verification_seconds
    )
    return {
        "kind": "mechanism_fixture_only",
        "scientific_budget": None,
        "quantile_method": "nearest_rank_ceil_0.95_n",
        "timing_definition": "injection submission to independent verification completion; S1 cap includes locally verified successes in all S1-bearing slots",
        "successful_observation_minimum_unapproved": config.successful_observation_minimum,
        "s1_cap_seconds": cap,
        "twice_slowest_end_to_end_p95_seconds": twice,
        "handoff_allowance_seconds": config.handoff_seconds,
        "verification_allowance_seconds": config.verification_seconds,
        "s1_handoff_verification_enlargement_seconds": enlarged,
        "common_deadline_seconds": max(twice, enlarged)
        if twice is not None and enlarged is not None
        else None,
        "coverage_complete": all(c["adequate"] for c in cells if c["repairable"]),
        "warning": "partial synthetic illustration only; never a sign-off-ready numerical proposal",
    }


def report(manifest: Manifest, config: OfflineConfig) -> dict:
    manifest = Manifest.model_validate_json(manifest.model_dump_json())
    config = OfflineConfig.model_validate(config.model_dump())
    cells = []
    for class_id in CLASSES:
        scenario = next(s for s in manifest.scenarios if s.class_id == class_id)
        structural = scenario.structural_check()
        for load in manifest.loads:
            for treatment in PRIMARY:
                repairable = structural["regime"] != "no_surviving_path" and (
                    treatment != "s1_only"
                    or structural["regime"] == "local_repair_sufficient"
                )
                cells.append(
                    {
                        "class_id": class_id,
                        "load_fraction": load,
                        "treatment": treatment,
                        "repairable": repairable,
                        "structural_check": structural,
                        "attempt_floor": 30 if repairable else None,
                        "expectation": "repair"
                        if repairable
                        else "no_permitted_repair",
                    }
                )
    baselines = [
        {"class_id": c, "load_fraction": load, "kind": "fault_free_not_repair_timing"}
        for c in CLASSES
        for load in manifest.loads
    ]
    allocation = manifest.allocation()
    calibration_placements = sum(s.split == "calibration" for s in manifest.scenarios)
    calibration_base = (
        calibration_placements * manifest.repetitions * len(config.calibration_loads)
    )
    calibration = {
        "gate_disabled_trials": calibration_base,
        "numeric_threshold_trials": 11 * calibration_base,
        "numeric_thresholds": [n / 10 for n in range(11)],
        "matched_ungated_trials": calibration_base,
        "comparator_shared_with_gate_disabled": config.share_ungated_comparator,
        "sharing_justification": config.comparator_sharing_justification,
        "additional_comparator_trials": 0
        if config.share_ungated_comparator
        else calibration_base,
        "loads_assumption_unapproved": config.calibration_loads,
    }
    calibration["total_trials"] = (
        12 * calibration_base + calibration["additional_comparator_trials"]
    )
    placements = sum(s.split == "heldout" for s in manifest.scenarios)
    resources = {
        "primary_trials": allocation["primary_trials"]
        + placements * 4 * config.replication_increase,
        "higher_load_trials": allocation["higher_load_trials"]
        + placements * 3 * config.replication_increase,
        "calibration": calibration,
        "feasibility_repairable_trials": sum(
            c["attempt_floor"] for c in cells if c["repairable"]
        ),
        "feasibility_nonrepairable_trials": config.nonrepairable_attempts_per_cell
        * sum(not c["repairable"] for c in cells),
        "fault_free_baseline_checks": len(baselines),
        "replacement_trials": config.replacement_trials,
        "nonrepairable_attempts_assumption_unapproved": config.nonrepairable_attempts_per_cell,
        "local_model_identity_assumption": config.local_model_identity,
        "runtime_assumptions": config.runtime_assumptions,
        "unmeasured_resources": [
            "CPU",
            "RSS/memory",
            "GPU utilization/memory",
            "energy",
            "warmup/startup overhead unless included in runtime assumptions",
        ],
        "reasoning_token_accounting": "separate, never added to output tokens; inputs must exclude overlap",
        "units": "serial aggregate trial-seconds; runtime includes inference; tokens are not time or energy",
        "input_kind": "unapproved_planning_assumptions_not_measurements",
    }
    resources["known_trials_excluding_replacements"] = (
        sum(
            resources[k]
            for k in (
                "primary_trials",
                "higher_load_trials",
                "feasibility_repairable_trials",
                "feasibility_nonrepairable_trials",
                "fault_free_baseline_checks",
            )
        )
        + calibration["total_trials"]
    )
    total = (
        None
        if config.replacement_trials is None
        else resources["known_trials_excluding_replacements"]
        + config.replacement_trials
    )
    resources["total_trials"] = total
    missing = []
    for field in (
        "replacement_trials",
        "local_model_identity",
        "runtime_assumptions",
        "input_tokens_per_trial",
        "output_tokens_per_trial",
        "reasoning_tokens_per_trial",
        "inference_seconds_per_trial",
        "runtime_seconds_per_trial",
    ):
        value = getattr(config, field)
        if value is None:
            missing.append(field)
        if field.endswith("_per_trial"):
            resources[field.removesuffix("_per_trial") + "_total"] = (
                None if value is None or total is None else value * total
            )
    resources["missing_inputs"] = missing
    blockers = [
        "offline_only_no_scientific_approval",
        "Pedro_budget_and_resource_approval_pending",
        "03_containment_and_04_05_live_integration_pending",
        "live_measurement_workload_provenance_validation_pending",
        "feasibility_policy_threshold_is_unapproved_not_calibration_selected",
    ]
    if config.successful_observation_minimum is None:
        blockers.append("successful_observation_minimum_pending_Pedro")
    else:
        blockers.append("successful_observation_minimum_is_unapproved_assumption")
    if config.handoff_seconds is None:
        blockers.append("handoff_allowance_missing")
    if missing:
        blockers.append("resource_inputs_missing")
    paths = [Path(p).resolve() for p in config.evidence_paths]
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate evidence paths/aliases")
    rows = [observation(p, manifest) for p in paths]
    ids = [r["trial_id"] for r in rows if "trial_id" in r]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate trial IDs cannot fill feasibility cells")
    budgets = budget_summary(cells, rows, config)
    if not budgets["coverage_complete"]:
        blockers.append("repairable_cell_coverage_or_successes_insufficient")
    if any(r["errors"] for r in rows):
        blockers.append("invalid_or_unusable_artifacts_retained")
    if any(r["missing_metadata"] for r in rows):
        blockers.append("artifact_provenance_incomplete")
    blockers.append("synthetic_artifacts_are_not_live_feasibility")
    result = {
        "schema_version": "recovery-feasibility-offline/v1",
        "numerical_signoff_ready": False,
        "execution_authorized": False,
        "scientific_approver": "Pedro",
        "plan": {"cells": cells, "fault_free_checks": baselines},
        "resources": resources,
        "observations": rows,
        "budget_candidates": budgets,
        "blockers": blockers,
    }
    # Finite inputs can still overflow during aggregation. Never export Infinity.
    json.dumps(result, allow_nan=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--output", type=Path, required=True, help="new report file; never overwritten"
    )
    args = parser.parse_args()
    manifest = (
        Manifest.model_validate(read_json(args.manifest))
        if args.manifest
        else build_manifest()
    )
    config = (
        OfflineConfig.model_validate(read_json(args.config))
        if args.config
        else OfflineConfig()
    )
    # Resolve input paths relative to the configuration, independent of CLI cwd.
    if args.config:
        config = config.model_copy(
            update={
                "evidence_paths": [
                    str(args.config.resolve().parent / p) for p in config.evidence_paths
                ]
            }
        )
    result = report(manifest, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "numerical_signoff_ready": False,
                "blockers": result["blockers"],
            }
        )
    )


if __name__ == "__main__":
    main()
