"""Offline configuration/artifact → report checks; not scientific observations."""

import asyncio
import json
import subprocess
import sys
from typing import Literal

import pytest

from examples.noms27.recovery_campaign.blocks import build_manifest


def test_plan_keeps_all_slots_and_separates_repairability_and_baselines():
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    result = report(build_manifest(), OfflineConfig())
    cells = result["plan"]["cells"]
    assert len(cells) == 48
    assert sum(c["repairable"] for c in cells) == 28
    assert {c["attempt_floor"] for c in cells if c["repairable"]} == {30}
    assert len(result["plan"]["fault_free_checks"]) == 12
    assert result["resources"]["primary_trials"] == 1440
    assert result["resources"]["higher_load_trials"] == 1080
    assert result["resources"]["calibration"]["gate_disabled_trials"] == 180
    assert result["resources"]["calibration"]["numeric_threshold_trials"] == 1980
    assert result["resources"]["calibration"]["matched_ungated_trials"] == 180
    assert result["resources"]["feasibility_repairable_trials"] == 840
    assert result["numerical_signoff_ready"] is False
    assert "successful_observation_minimum_pending_Pedro" in result["blockers"]


def artifact(
    tmp_path,
    *,
    trial="fixture",
    treatment: Literal[
        "s1_only", "s2_only", "combined_ungated", "combined_gated"
    ] = "s1_only",
    recovery=0.9,
    class_id="diamond_link",
    load=0.1,
):
    """Known packet timeline: recovery at .9, verified at 2.0 seconds."""
    from examples.noms27.recovery_campaign.runner import CampaignConfig, measure

    scenario = next(
        s
        for s in build_manifest().scenarios
        if s.class_id == class_id and s.split == "calibration"
    )
    config = CampaignConfig(
        trial_id=trial,
        treatment=treatment,
        scenario="matched_scenario",
        scenario_instance=scenario,
        deadline_seconds=6,
        s1_cap_seconds=3,
        bottleneck_mbps=9.6 if load == 0.1 else 1.37142857143,
        load_fraction=load,
        seed=1000,
    )
    packets = [
        {
            "seq": i,
            "send_ns": i * 10_000_000,
            "arrivals_ns": [i * 10_000_000 + 1_000_000]
            if i < 500 or i >= 500 + round(recovery * 100)
            else [],
        }
        for i in range(1600)
    ]
    evidence = {
        "schema_version": "recovery-s1/v1"
        if treatment == "s1_only"
        else "recovery-s2/v1"
        if treatment == "s2_only"
        else "recovery-combined/v1",
        "trial_id": trial,
        "config": config.model_dump(mode="json"),
        "scenario": {
            "definition": scenario.model_dump(mode="json"),
            "structural_check": scenario.structural_check(),
        },
        "evidence_kind": "mechanism_validation",
        "validity": {"valid": True, "reasons": []},
        "outcome": {"service_recovery": True, "recovery_failure": False},
        "events": [
            {
                "id": 1,
                "trial_id": trial,
                "kind": "injection_start",
                "tick_ns": 5_000_000_000,
                "deadline_ns": 11_000_000_000,
            },
            {
                "id": 2,
                "trial_id": trial,
                "kind": "injection_complete",
                "tick_ns": 5_000_000_000,
            },
            {
                "id": 3,
                "trial_id": trial,
                "kind": "fault_confirmed",
                "tick_ns": 5_000_000_000,
                "confirmed_ns": 5_000_000_000,
            },
        ],
        "packet_evidence": {
            "packets": packets,
            "measurement_errors": [],
            "fault_checks": [{"both_endpoints_down": True}],
        },
        "measurement": measure(
            config, packets, 5_000_000_000, recovery_not_before_ns=5_000_000_000
        ),
        "provenance": {},
    }
    path = tmp_path / (trial + ".json")
    path.write_text(json.dumps(evidence))
    return path, evidence


def test_known_packet_artifacts_report_twice_p95_and_enlarged_common_deadline(tmp_path):
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    s1, _ = artifact(tmp_path)
    s2, _ = artifact(tmp_path, trial="s2", treatment="s2_only", recovery=2.9)
    result = report(
        build_manifest(),
        OfflineConfig(
            evidence_paths=[str(s1), str(s2)],
            successful_observation_minimum=1,
            handoff_seconds=4.0,
        ),
    )
    assert result["budget_candidates"]["s1_cap_seconds"] == 4.0
    assert result["budget_candidates"]["twice_slowest_end_to_end_p95_seconds"] == 8.0
    assert result["budget_candidates"]["common_deadline_seconds"] == 9.1
    assert result["budget_candidates"]["coverage_complete"] is False
    assert result["budget_candidates"]["kind"] == "mechanism_fixture_only"
    assert result["numerical_signoff_ready"] is False
    assert result["observations"][0]["missing_metadata"]
    assert "repairable_cell_coverage_or_successes_insufficient" in result["blockers"]


@pytest.mark.parametrize(
    "damage",
    [
        "nonfinite",
        "bool_packet",
        "string_budget",
        "bool_measurement",
        "duplicate_key",
        "false_outcome",
        "metadata",
        "missing",
        "heldout",
        "calibration",
        "invalid",
        "heldout_seed",
        "provenance_mismatch",
    ],
)
def test_unusable_originals_are_retained_and_cannot_contribute_timing(tmp_path, damage):
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    path, evidence = artifact(tmp_path)
    if damage == "nonfinite":
        evidence["measurement"]["verification_complete_seconds"] = float("nan")
    elif damage == "bool_packet":
        evidence["packet_evidence"]["packets"][0]["seq"] = True
    elif damage == "string_budget":
        evidence["config"]["deadline_seconds"] = "6"
    elif damage == "bool_measurement":
        evidence["measurement"]["baseline_healthy"] = 1
    elif damage == "false_outcome":
        evidence["outcome"]["service_recovery"] = False
    elif damage == "metadata":
        evidence["scenario"]["definition"]["class_id"] = "tail_link"
    elif damage == "heldout":
        evidence["config"]["scenario_instance"]["split"] = "heldout"
        evidence["scenario"]["definition"]["split"] = "heldout"
    elif damage == "calibration":
        evidence["evidence_kind"] = "calibration"
    elif damage == "invalid":
        evidence["validity"] = {
            "valid": False,
            "reasons": ["demonstrated_injection_failure"],
        }
    elif damage == "heldout_seed":
        evidence["config"]["seed"] = 2000
    elif damage == "provenance_mismatch":
        evidence["provenance"]["seed"] = 12345
    path.write_text(json.dumps(evidence))
    if damage == "duplicate_key":
        path.write_text('{"trial_id":"a","trial_id":"b"}')
    elif damage == "missing":
        path.unlink()
    result = report(
        build_manifest(),
        OfflineConfig(evidence_paths=[str(path)], successful_observation_minimum=1),
    )
    assert len(result["observations"]) == 1
    assert result["observations"][0]["accepted_for_timing"] is False
    assert result["observations"][0]["errors"]
    assert result["budget_candidates"]["s1_cap_seconds"] is None
    assert result["numerical_signoff_ready"] is False


def test_duplicate_trial_ids_and_path_aliases_rejected(tmp_path):
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    path, _ = artifact(tmp_path)
    duplicate = tmp_path / "copy.json"
    duplicate.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="duplicate trial"):
        report(
            build_manifest(), OfflineConfig(evidence_paths=[str(path), str(duplicate)])
        )
    with pytest.raises(ValueError, match="duplicate evidence"):
        report(
            build_manifest(),
            OfflineConfig(
                evidence_paths=[str(path), str(path.parent / "x/../fixture.json")]
            ),
        )


def test_failed_and_invalid_attempts_do_not_disappear_or_become_successes(tmp_path):
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    failure, evidence = artifact(tmp_path, trial="failed", recovery=20)
    evidence["outcome"] = {"service_recovery": False, "recovery_failure": True}
    failure.write_text(json.dumps(evidence))
    invalid, evidence = artifact(tmp_path, trial="invalid")
    evidence["validity"] = {
        "valid": False,
        "reasons": ["missing_essential_measurements"],
    }
    invalid.write_text(json.dumps(evidence))
    result = report(
        build_manifest(), OfflineConfig(evidence_paths=[str(failure), str(invalid)])
    )
    cell = next(
        c
        for c in result["plan"]["cells"]
        if c["class_id"] == "diamond_link"
        and c["load_fraction"] == 0.1
        and c["treatment"] == "s1_only"
    )
    assert cell["retained_attempts"] == 2
    assert cell["valid_unsuccessful_attempts"] == 1
    assert cell["invalid_or_unusable_attempts"] == 1
    assert cell["successful_observations"] == 0
    assert result["observations"][0]["censored"] is True


def test_resource_estimate_counts_candidates_comparator_replacements_and_increases():
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    config = OfflineConfig(
        replication_increase=1,
        replacement_trials=8,
        local_model_identity="hypothetical local S1/S2 pair, not measured",
        input_tokens_per_trial=2.0,
        output_tokens_per_trial=3.0,
        reasoning_tokens_per_trial=1.0,
        inference_seconds_per_trial=2.0,
        runtime_seconds_per_trial=3.0,
        runtime_assumptions="serial incl setup/reset/observation/cleanup and inference; reasoning separate non-overlapping",
    )
    resources = report(build_manifest(), config)["resources"]
    assert resources["primary_trials"] == 1488
    assert resources["higher_load_trials"] == 1116
    assert resources["total_trials"] == 6404
    assert resources["input_tokens_total"] == 12808
    assert resources["output_tokens_total"] == 19212
    assert resources["reasoning_tokens_total"] == 6404
    assert resources["inference_seconds_total"] == 12808
    assert resources["runtime_seconds_total"] == 19212
    assert resources["missing_inputs"] == []
    shared = config.model_copy(
        update={
            "share_ungated_comparator": True,
            "comparator_sharing_justification": "same gate-disabled requests, seeds, placement and outcomes reused as paired ungated comparator",
        }
    )
    assert report(build_manifest(), shared)["resources"]["total_trials"] == 6224


@pytest.mark.parametrize(
    "patch",
    [
        {"successful_observation_minimum": True},
        {"successful_observation_minimum": "30"},
        {"handoff_seconds": float("inf")},
        {"runtime_seconds_per_trial": float("nan")},
        {"replication_increase": -1},
        {"replacement_trials": False},
        {"share_ungated_comparator": True},
        {"calibration_loads": [0.1, 0.1]},
        {"scientific_approval": "Pedro"},
        {"live_override": True},
    ],
)
def test_config_fails_closed_without_executable_approval_or_numeric_coercion(patch):
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig

    with pytest.raises(ValueError):
        OfflineConfig.model_validate(patch)


def test_config_to_report_cli_resolves_paths_and_never_overwrites_inputs(tmp_path):
    path, _ = artifact(tmp_path)
    original = path.read_bytes()
    config = tmp_path / "offline.json"
    config.write_text(json.dumps({"evidence_paths": [path.name]}))
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        "-m",
        "examples.noms27.recovery_campaign.feasibility",
        "--config",
        str(config),
        "--output",
        str(output),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    result = json.loads(output.read_text())
    assert result["observations"][0]["trial_id"] == "fixture"
    assert result["numerical_signoff_ready"] is False
    assert subprocess.run(command, capture_output=True, check=False).returncode != 0
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "treatment", ["s1_only", "s2_only", "combined_ungated", "combined_gated"]
)
@pytest.mark.parametrize("load", [0.1, 0.7])
def test_retained_run_campaign_packet_evidence_is_accepted_as_fixture_only(
    tmp_path, treatment, load
):
    from examples.noms27.recovery_campaign.controlled import ControlledScenarioRuntime
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report
    from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign

    config = CampaignConfig(
        trial_id=f"retained-{treatment}-{load}",
        treatment=treatment,
        scenario="matched_scenario",
        scenario_instance=build_manifest().scenarios[0],
        deadline_seconds=6,
        s1_cap_seconds=3,
        bottleneck_mbps=9.6,
        seed=1000,
        load_fraction=load,
        confidence_gate_enabled=treatment == "combined_gated",
        confidence_threshold=1.0 if treatment == "combined_gated" else None,
    )
    asyncio.run(
        run_campaign(config, tmp_path / "retained", runtime=ControlledScenarioRuntime())
    )
    result = report(
        build_manifest(),
        OfflineConfig(evidence_paths=[str(tmp_path / "retained/evidence.json")]),
    )
    assert result["observations"][0]["errors"] == []
    assert result["observations"][0]["accepted_for_timing"] is True
    assert result["numerical_signoff_ready"] is False


def test_nearest_rank_p95_is_not_maximum_and_30_attempts_is_not_success_minimum(
    tmp_path,
):
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    paths = []
    for i in range(30):
        # 19 successes at 2s, one at 4s; ten valid censored failures.
        path, evidence = artifact(
            tmp_path,
            trial=f"p95-{i}",
            recovery=0.9 if i < 19 else 2.9 if i == 19 else 20,
        )
        if i >= 20:
            evidence["outcome"] = {"service_recovery": False, "recovery_failure": True}
            path.write_text(json.dumps(evidence))
        paths.append(str(path))
    result = report(
        build_manifest(),
        OfflineConfig(
            evidence_paths=paths, successful_observation_minimum=20, handoff_seconds=0.0
        ),
    )
    cell = result["plan"]["cells"][0]
    assert cell["valid_attempts"] == 30
    assert cell["successful_observations"] == 20
    assert cell["valid_unsuccessful_attempts"] == 10
    assert cell["successful_end_to_end_p95_seconds"] == 2.0
    assert cell["adequate"] is True
    assert result["budget_candidates"]["s1_cap_seconds"] == 4.0
    assert result["numerical_signoff_ready"] is False
    insufficient = report(
        build_manifest(),
        OfflineConfig(evidence_paths=paths, successful_observation_minimum=21),
    )
    assert insufficient["plan"]["cells"][0]["adequate"] is False
    assert insufficient["budget_candidates"]["s1_cap_seconds"] is None


def test_overflowed_resource_totals_rejected_instead_of_emitting_nonfinite_report():
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    with pytest.raises(ValueError):
        report(
            build_manifest(),
            OfflineConfig(replacement_trials=0, input_tokens_per_trial=1e308),
        )


def test_maxima_span_classes_and_loads_without_nonrepairable_timing(tmp_path):
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    s1, _ = artifact(
        tmp_path, trial="local", class_id="diamond_switch", load=0.7, recovery=1.9
    )
    s2, _ = artifact(
        tmp_path,
        trial="broader",
        treatment="s2_only",
        class_id="extended_link",
        load=0.7,
        recovery=4.9,
    )
    result = report(
        build_manifest(),
        OfflineConfig(
            evidence_paths=[str(s1), str(s2)],
            successful_observation_minimum=1,
            handoff_seconds=4.0,
        ),
    )
    assert result["budget_candidates"]["s1_cap_seconds"] == 6.0
    assert result["budget_candidates"]["twice_slowest_end_to_end_p95_seconds"] == 12.0
    assert result["budget_candidates"]["common_deadline_seconds"] == 12.0
    assert result["numerical_signoff_ready"] is False


def test_s1_cap_includes_successful_local_repairs_in_combined_slots(tmp_path):
    from examples.noms27.recovery_campaign.feasibility import OfflineConfig, report

    s1, _ = artifact(tmp_path, trial="s1-fast")
    combined, evidence = artifact(
        tmp_path, trial="combined-slow", treatment="combined_ungated", recovery=2.9
    )
    evidence["outcome"]["s1_status"] = "verified"
    combined.write_text(json.dumps(evidence))
    result = report(
        build_manifest(),
        OfflineConfig(
            evidence_paths=[str(s1), str(combined)],
            successful_observation_minimum=1,
            handoff_seconds=0.0,
        ),
    )
    assert result["budget_candidates"]["s1_cap_seconds"] == 8.0
