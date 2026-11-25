"""Manifest allocation and offline matched-block execution at the incident seam."""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .confidence import valid_confidence
from .runner import CampaignConfig, CampaignRuntime, run_campaign, write_json
from .scenarios import CLASSES, Scenario, make_scenario

PRIMARY = ("s1_only", "s2_only", "combined_ungated", "combined_gated")
HIGHER = PRIMARY[1:]
AVAILABLE = PRIMARY[:3]
VALIDITY_RULES = {
    "invalid": [
        "demonstrated_injection_failure",
        "demonstrated_initial_state_failure",
        "missing_or_broken_essential_measurement",
    ],
    "valid_unsuccessful": [
        "model_failure",
        "malformed_response",
        "action_failure",
        "budget_exhaustion",
        "no_permitted_repair",
    ],
    "replacement": "retain originals; replace whole block with fresh IDs; bounded attempts",
    "admission": "stop immediately when incident containment denies next trial",
}


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_id: str = "recovery-05-development"
    status: str = "development_unapproved"
    scenarios: tuple[Scenario, ...]
    calibration_seeds: tuple[int, ...] = tuple(range(1000, 1030))
    evaluation_seeds: tuple[int, ...] = tuple(range(2000, 2030))
    repetitions: int = Field(default=30, ge=30)
    primary_treatments: tuple[str, ...] = PRIMARY
    higher_load_treatments: tuple[str, ...] = HIGHER
    loads: tuple[float, float] = (0.1, 0.7)
    automatic_restoration: bool = False

    @model_validator(mode="after")
    def check_design(self):
        if self.status != "development_unapproved" or self.automatic_restoration:
            raise ValueError("no approval or automatic restoration is permitted")
        if (
            self.primary_treatments != PRIMARY
            or self.higher_load_treatments != HIGHER
            or self.loads != (0.1, 0.7)
        ):
            raise ValueError("preserve approved allocation slots and loads")
        if len({s.placement_id for s in self.scenarios}) != len(self.scenarios):
            raise ValueError("placements require unique identifiers")
        structural_splits: dict[tuple, str] = {}
        topologies: dict[str, tuple] = {}
        for scenario in self.scenarios:
            topology = tuple(sorted(tuple(sorted(edge)) for edge in scenario.links))
            if topologies.setdefault(scenario.topology_id, topology) != topology:
                raise ValueError("topology identifier describes different graphs")
            identity = (
                topology,
                scenario.fault_kind,
                tuple(sorted(scenario.fault_target)),
            )
            existing_split = structural_splits.get(identity)
            if existing_split is not None and existing_split != scenario.split:
                raise ValueError(
                    "splits require disjoint structural placements, not renamed IDs"
                )
            if existing_split is not None:
                raise ValueError("allocation requires distinct structural placements")
            structural_splits[identity] = scenario.split
        if {s.class_id for s in self.scenarios} != set(CLASSES):
            raise ValueError("all six classes required")
        for class_id in CLASSES:
            splits = [s.split for s in self.scenarios if s.class_id == class_id]
            if splits.count("calibration") < 1 or splits.count("heldout") < 2:
                raise ValueError(
                    "each class requires calibration and two heldout placements"
                )
        if set(self.calibration_seeds) & set(self.evaluation_seeds):
            raise ValueError("calibration and evaluation seeds must be disjoint")
        if any(
            len(seeds) < self.repetitions or len(set(seeds)) != len(seeds)
            for seeds in (self.calibration_seeds, self.evaluation_seeds)
        ):
            raise ValueError("distinct traffic seeds required for every repetition")
        return self

    def allocation(self) -> dict:
        placements = sum(s.split == "heldout" for s in self.scenarios)
        return {
            "primary_trials": placements
            * len(self.primary_treatments)
            * self.repetitions,
            "higher_load_trials": placements
            * len(self.higher_load_treatments)
            * self.repetitions,
            "calibration_repetitions_per_placement": self.repetitions,
            "primary_treatments": list(self.primary_treatments),
            "higher_load_treatments": list(self.higher_load_treatments),
            "excludes": ["calibration", "feasibility", "invalid_block_replacements"],
            "execution_authorized": False,
        }

    def plan(self, *, split: str = "heldout", load_fraction: float = 0.1) -> list[dict]:
        """Materialize future matched slots, never authorize or execute them."""
        if split not in {"calibration", "heldout"} or load_fraction not in self.loads:
            raise ValueError("unknown split or load")
        treatments = (
            self.primary_treatments
            if load_fraction == 0.1
            else self.higher_load_treatments
        )
        seeds = self.evaluation_seeds if split == "heldout" else self.calibration_seeds
        return [
            {
                "placement_id": s.placement_id,
                "topology_id": s.topology_id,
                "class_id": s.class_id,
                "split": s.split,
                "repetition": repetition,
                "traffic_seed": seeds[repetition],
                "environment_seed": seeds[repetition] + 10_000,
                "load_fraction": load_fraction,
                "payload_bytes": 1200,
                "fault_timing": "after_5_seconds_healthy_baseline",
                "automatic_restoration": False,
                "scenario_sha256": hashlib.sha256(
                    s.model_dump_json().encode()
                ).hexdigest(),
                "treatments": list(treatments),
            }
            for s in self.scenarios
            if s.split == split
            for repetition in range(self.repetitions)
        ]


def build_manifest() -> Manifest:
    return Manifest(
        scenarios=tuple(make_scenario(c, p) for c in CLASSES for p in range(3))
    )


async def run_blocks(
    manifest: Manifest,
    evidence_dir: Path,
    *,
    repetitions: int = 2,
    load_fraction: float = 0.1,
    order_seed: int = 505,
    invalid_first: str | None = None,
    max_replacements: int = 1,
    confidence_threshold: float | None = None,
    runtime_factory: Callable[..., CampaignRuntime] | None = None,
) -> dict:
    """Small controlled calibration-placement rehearsal, not a campaign executor.

    Future held-out slots are only planned. The explicit offline evidence-kind
    check is in addition to run_campaign's unchanged containment admission gate.
    """
    from .controlled import ControlledScenarioRuntime

    # Revalidate serialized input, including nested mutable forwarding maps.
    manifest = Manifest.model_validate_json(manifest.model_dump_json())
    if not 1 <= repetitions <= manifest.repetitions or max_replacements < 0:
        raise ValueError("invalid controlled repetition/replacement bound")
    if load_fraction not in manifest.loads:
        raise ValueError("load must be 0.1 or 0.7")
    if confidence_threshold is not None and not valid_confidence(confidence_threshold):
        raise ValueError(
            "development confidence threshold must be finite numeric [0,1]"
        )
    treatments = PRIMARY if confidence_threshold is not None else AVAILABLE
    if load_fraction == 0.7:
        treatments = treatments[1:]
    if invalid_first not in {
        None,
        "injection",
        "initial_state",
        "essential_measurement",
        "model",
        "action",
    }:
        raise ValueError("unknown controlled failure")
    factory = runtime_factory or ControlledScenarioRuntime
    evidence_dir.mkdir(parents=True, exist_ok=False)
    write_json(evidence_dir / "manifest.json", manifest.model_dump(mode="json"))
    write_json(
        evidence_dir / "structural-checks.json",
        {s.placement_id: s.structural_check() for s in manifest.scenarios},
    )
    write_json(
        evidence_dir / "allocation.json",
        {
            **manifest.allocation(),
            "primary": manifest.plan(),
            "higher_load": manifest.plan(load_fraction=0.7),
        },
    )
    result = {
        "schema_version": "recovery-blocks/v1",
        "evidence_kind": "mechanism_validation",
        "status": "running",
        "manifest_sha256": hashlib.sha256(
            (evidence_dir / "manifest.json").read_bytes()
        ).hexdigest(),
        "validity_rules": VALIDITY_RULES,
        "order_seed": order_seed,
        "requested_repetitions": repetitions,
        "budgets": "development_only_not_feasibility_derived_or_approved",
        "heldout_execution": False,
        "blocks": [],
        "available_treatments": list(treatments),
        "reserved_unavailable_treatments": ["combined_gated"]
        if confidence_threshold is None
        else [],
        "development_confidence_threshold": confidence_threshold,
    }
    rng = random.Random(order_seed)
    trials_started = 0
    scenarios = {s.placement_id: s for s in manifest.scenarios}

    def persist():
        write_json(evidence_dir / "blocks.json", result)

    persist()
    for matching in manifest.plan(split="calibration", load_fraction=load_fraction):
        if matching["repetition"] >= repetitions:
            continue
        replaces = None
        for replacement in range(max_replacements + 1):
            block_id = "block-" + uuid.uuid4().hex
            order = list(treatments)
            rng.shuffle(order)
            block = {
                "block_id": block_id,
                "replaces": replaces,
                "matching": matching,
                "treatment_order": order,
                "valid": None,
                "complete": False,
                "trials": [],
            }
            result["blocks"].append(block)
            persist()
            for treatment in order:
                trial_id = block_id + "-" + treatment
                failure = invalid_first if trials_started == 0 else None
                runtime = factory(failure=failure)
                if runtime.evidence_kind != "mechanism_validation":
                    result["status"] = "blocked_non_offline_runtime"
                    persist()
                    raise ValueError(
                        "matched rehearsal requires an offline mechanism runtime"
                    )
                config = CampaignConfig(
                    trial_id=trial_id,
                    treatment=treatment,
                    confidence_gate_enabled=treatment == "combined_gated",
                    confidence_threshold=confidence_threshold,
                    scenario="matched_scenario",
                    scenario_instance=scenarios[matching["placement_id"]],
                    deadline_seconds=6,
                    s1_cap_seconds=3,
                    bottleneck_mbps=9.6,
                    seed=matching["traffic_seed"],
                    environment_seed=matching["environment_seed"],
                    load_fraction=load_fraction,
                    execution_order=trials_started + 1,
                )
                trials_started += 1
                evidence = await run_campaign(
                    config, evidence_dir / trial_id, runtime=runtime
                )
                block["trials"].append(
                    {
                        "trial_id": trial_id,
                        "treatment": treatment,
                        "execution_order": trials_started,
                        "evidence": f"{trial_id}/evidence.json",
                        "validity": evidence["validity"],
                        "reset": evidence["reset"],
                    }
                )
                persist()
                if evidence["containment"]["next_trial_allowed"] is not True:
                    result["status"] = "blocked_containment"
                    persist()
                    return result
            block["complete"] = True
            block["valid"] = all(t["validity"]["valid"] for t in block["trials"])
            persist()
            if block["valid"]:
                break
            replaces = block_id
            if replacement == max_replacements:
                result["status"] = "blocked_replacement_limit"
                persist()
                return result
    result["status"] = "completed"
    persist()
    return result


def main() -> int:
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--write-manifest", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--controlled", action="store_true")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--load", type=float, choices=(0.1, 0.7), default=0.1)
    parser.add_argument(
        "--development-confidence-threshold",
        type=float,
        help="opt in to the gated offline slot; not a calibrated or approved threshold",
    )
    parser.add_argument(
        "--invalid-first",
        choices=("injection", "initial_state", "essential_measurement"),
    )
    args = parser.parse_args()
    manifest = (
        Manifest.model_validate_json(args.manifest.read_text())
        if args.manifest
        else build_manifest()
    )
    if args.write_manifest:
        # Never overwrite an unfamiliar freeze or earlier evidence.
        with args.write_manifest.open("x") as stream:
            stream.write(manifest.model_dump_json(indent=2) + "\n")
    if args.check:
        print(
            json.dumps(
                {
                    "allocation": manifest.allocation(),
                    "structural_checks": {
                        s.placement_id: s.structural_check() for s in manifest.scenarios
                    },
                }
            )
        )
        return 0
    if args.evidence is None:
        parser.error("--controlled requires --evidence")
    result = asyncio.run(
        run_blocks(
            manifest,
            args.evidence,
            repetitions=args.repetitions,
            load_fraction=args.load,
            confidence_threshold=args.development_confidence_threshold,
            invalid_first=args.invalid_first,
        )
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "evidence_kind": result["evidence_kind"],
                "blocks": len(result["blocks"]),
                "heldout_execution": False,
            }
        )
    )
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
